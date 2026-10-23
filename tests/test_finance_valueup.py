"""밸류업 약속 점검 (agentworld_lite/finance/valueup.py) and the k06 value-up task. Offline: the committed
data/finance/valueup/valueup.json and a fake DART client stand in for the network."""

import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from agentworld_lite.finance import valueup as vu
from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.catalog import FHC, SPECS_KR_FHC
from agentworld_lite.finance.note_ko import sector_note_ko, uncited_figures_ko
from agentworld_lite.finance.run import make_factory, run_episode, validate_task
from agentworld_lite.task import Task

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "finance" / "dart_fhc"
KR_DIR = ROOT / "tasks" / "finance_kr"


def _st(code):
    return json.loads((DATA / f"{code}.json").read_text(encoding="utf-8"))["statements"]


def _off(got, cp):
    return abs(got - cp["expected"]) > cp["rel_tol"] * abs(cp["expected"])


# ---------------------------------------------------------------------- quotes and documents
def _zip(html: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.xml", html.encode("utf-8"))
    return buf.getvalue()


def test_document_text_and_quote_check_ignore_layout():
    text = vu.document_text(_zip("<html><body><p>목표 1)&nbsp;ROE <span>12%</span></p><table><tr><td>보통주자본비율"
                                 "</td><td>13.27%</td></tr></table></body></html>"))
    assert vu.check_quotes(text, ["목표 1) ROE 12%", "보통주자본비율 13.27%"]) == []
    assert vu.check_quotes(text, ["목표 1) ROE 13%"]) == ["목표 1) ROE 13%"]


def test_every_target_is_a_verbatim_quote_with_its_receipt_number():
    data = vu.load()
    for t in vu.TARGETS:
        assert t.rcept_no in data["documents"], t
        assert data["documents"][t.rcept_no]["quotes_checked"] >= 1
        if t.threshold is not None and t.metric != "shares":
            assert f"{t.threshold:g}%" in t.quote, t                          # the number is in the quoted wording
    assert {t.code for t in vu.TARGETS} == {"105560", "055550", "086790"}     # Woori: no target in the plan's text
    assert all(len(d["sha256"]) == 64 for d in data["documents"].values())
    assert all(p["report"].startswith(("기업가치", "[기재정정]기업가치")) for ps in data["plans"].values() for p in ps)
    assert len(data["plans"]["105560"]) == 5                                  # all pages of the exchange filings


def test_woori_targets_are_read_from_the_pdf_pages_and_kept_apart_from_text_quotes():
    assert {t.code for t in vu.TARGETS_FROM_IMAGES} == {"316140"} and not set(vu.TARGETS_FROM_IMAGES) & set(vu.TARGETS)
    assert all(t.rcept_no == vu.IMAGE_PDF["rcept_no"] and f"{t.threshold:g}%" in t.quote for t in vu.TARGETS_FROM_IMAGES)
    assert len(vu.IMAGE_PDF["sha256"]) == 64 and vu.all_targets() == [*vu.TARGETS, *vu.TARGETS_FROM_IMAGES]
    rows = {r["target"].metric: r for r in vu.scorecard_rows() if r["target"].code == "316140"}
    assert rows["roe"]["status"] == "미달(기한 미명시)" and round(rows["roe"]["now"][0], 2) == 8.71
    assert rows["payout"]["status"] == "미달(기한 미명시)" and round(rows["payout"]["now"][0], 2) == 36.76  # Woori: 36.6
    assert rows["cet1"]["status"] == "측정 불가" and rows["cet1"]["target"].deadline == "2026-12-31"    # not in its H1 report


class FakeClient:
    """Serves build() offline: one document per receipt number, statements, dividends and buyback decisions."""

    def __init__(self, drop_quote=None):
        self.drop = drop_quote
        self.docs = {}
        for t in vu.TARGETS:
            self.docs.setdefault(t.rcept_no, []).append(t.quote)
        for c in [*vu.CET1.values(), *vu.CET1_NOT_REPORTED.values(), *vu.COMPANY_REPORTED]:
            self.docs.setdefault(c["rcept_no"], []).append(c["quote"])
        for notes in vu.DEADLINE_NOTE.values():
            for rcept, quote in notes.values():
                self.docs.setdefault(rcept, []).append(quote)

    def _get(self, endpoint, rcept_no):
        quotes = [q for q in self.docs[rcept_no] if q != self.drop]
        return _zip("<p>" + "</p><p>".join(quotes) + "</p>")

    def resolve(self, code):
        return {"corp_code": "C" + code}

    def filing_list(self, corp, bgn, end, kind="A"):
        return [{"rcept_no": "20260101800001", "report_nm": "기업가치제고계획(자율공시)   (2026년)"},
                {"rcept_no": "20260101800002", "report_nm": "현금ㆍ현물배당결정"}]

    def statement(self, corp, year, period, basis, profile="default"):
        return {"rcept_no": "20240814000001", "filed": "2024-08-14", "net_income_owners": 1, "equity_owners": 2}

    def _json(self, endpoint, **p):
        if endpoint == "alotMatter.json":
            return [{"se": "현금배당금총액(백만원)", "thstrm": "1,000", "rcept_no": "20250318000001"}]
        if endpoint == "tsstkAqDecsn.json":
            return [{"rcept_no": "20250205000001", "aq_dd": "2025년 02월 05일", "aqpln_prc_ostk": "100,000,000,000",
                     "aq_pp": "주식소각"}]
        return [{"rcept_no": "20250724000001", "bddd": "2025년 07월 24일", "ctr_prc": "50,000,000,000",
                 "ctr_pp": "주주환원"}]


def test_build_writes_lf_json_only_when_every_quote_is_found(tmp_path):
    out = vu.build(FakeClient(), tmp_path / "valueup.json", log=lambda s: None)
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    data = json.loads(raw)
    assert data["plans"]["105560"] == [{"rcept_no": "20260101800001", "filed": "2026-01-01",
                                        "report": "기업가치제고계획(자율공시) (2026년)"}]
    assert data["dividends"]["105560"]["2025"]["amount"] == 1_000_000_000
    assert [b["kind"] for b in data["buybacks"]["055550"]] == ["직접 취득", "신탁계약"]
    assert data["buybacks"]["055550"][0]["decided"] == "2025-02-05"
    with pytest.raises(vu.ValueupError, match="quote not found"):
        vu.build(FakeClient(drop_quote="목표 1) ROE 12%"), tmp_path / "bad.json", log=lambda s: None)
    assert not (tmp_path / "bad.json").exists()


# ---------------------------------------------------------------------- measures and verdicts
def test_judge_rules():
    t = vu.Target("x", "roe", 10.0, ">=", "2027-12-31", "r", "q")
    assert vu.judge(t, 10.2, 9.0)[0] == "달성"
    status, gap, need = vu.judge(t, 9.0, 8.0)          # 1.0 pp to go in 1.5 years: 0.67 pp a year needed
    assert status == "순항" and gap == pytest.approx(1.0) and need == pytest.approx(1.0 / 1.5, rel=1e-2)
    assert vu.judge(t, 9.0, 8.8)[0] == "지연"
    assert vu.judge(t, 9.0, None)[0] == "판단 불가"
    assert vu.judge(vu.Target("x", "roe", 12.0, ">=", None, "r", "q"), 9.0, 9.5)[0] == "미달(기한 미명시)"
    assert vu.judge(vu.Target("x", "shares", 450.0, "<=", "2027-12-31", "r", "q"), 440.0, 460.0)[0] == "달성"
    assert vu.judge(vu.Target("x", "roe", 10.0, ">=", "2026-01-01", "r", "q"), 9.0, 8.0)[0] == "미달(기한 경과)"
    assert vu.judge(vu.Target("x", "policy", None, "policy", None, "r", "q"), 52.0, None)[0] == "정량 목표 없음"


def test_scorecard_figures_and_verdicts():
    rows = {(r["target"].code, r["target"].metric): r for r in vu.scorecard_rows()}
    kb, sh, hn = FHC["kb"], FHC["shinhan"], FHC["hana"]
    roe = lambda code: (lambda s: (s["2025:FY:CFS"]["net_income_owners"] - s["2025:H1:CFS"]["net_income_owners"]
                                   + s["2026:H1:CFS"]["net_income_owners"])
                        / ((s["2025:H1:CFS"]["equity_owners"] + s["2026:H1:CFS"]["equity_owners"]) / 2) * 100)(_st(code))
    assert rows[(kb, "roe")]["now"][0] == pytest.approx(roe(kb)) and rows[(kb, "roe")]["status"] == "달성"
    assert round(rows[(kb, "roe")]["now"][0], 2) == 10.47 and round(rows[(kb, "cet1")]["now"][0], 2) == 13.74
    assert rows[(sh, "roe")]["status"] == "순항" and round(rows[(sh, "roe")]["prev"][0], 2) == 8.46
    assert rows[(sh, "shares")]["status"] == "지연" and rows[(sh, "shares")]["gap"] == 474_654_361 - 450_000_000
    assert rows[(sh, "payout")]["status"] == "달성" and round(rows[(sh, "payout")]["now"][0], 2) == 51.21
    assert rows[(hn, "roe")]["status"] == "미달(기한 미명시)" and rows[(hn, "roe")]["now"][0] < rows[(hn, "roe")]["prev"][0]
    assert {round(rows[(c, "cet1")]["now"][0], 2) for c in (kb, sh, hn)} == {13.74, 13.43, 13.27}
    m = vu.measures(vu.Dataset(DATA), vu.load())
    assert round(m[kb]["payout"][1][0], 1) == 39.7                                        # KB, 2024
    assert round(m[sh]["payout"][1][0], 2) == 43.55 and round(m[sh]["roe_fy2025"][0], 2) == 8.72  # Shinhan says 40.2, 9.11


def test_scorecard_markdown_cites_everything(tmp_path):
    md = vu.render_scorecard()
    assert md.startswith("# 밸류업 약속 점검: 4대 금융지주\n") and vu.NOTICE in md
    assert uncited_figures_ko(md) == []
    for t in vu.TARGETS:
        assert f"“{t.quote}” [사실 `{t.code}.target.{t.metric}` · 접수번호 {t.rcept_no}]" in md
    assert "**지연**" in md and "**순항**" in md
    for t in vu.TARGETS_FROM_IMAGES:                                         # Woori: read from the PDF's page images
        assert (f"“{t.quote}” [사실 `{t.code}.target.{t.metric}` · 접수번호 {t.rcept_no}], 첨부 PDF "
                f"{vu.IMAGE_PAGE[(t.code, t.metric)]}쪽 이미지에서 판독") in md
    assert vu.IMAGE_NOTE in md and f"SHA-256 `{vu.IMAGE_PDF['sha256']}`" in md
    assert uncited_figures_ko("목표: “ROE 12%” 출처 없음") == ["“ROE 12%”"]       # a quote must carry a citation
    out = tmp_path / "vu.md"
    assert finance_main(["valueup", "--out", str(out)]) == 0
    assert b"\r\n" not in out.read_bytes()


# ---------------------------------------------------------------------- k06
def test_k06_reference_note_and_targets_keep_their_receipt_numbers():
    tasks = [Task.load(KR_DIR / f"k06_valueup_roe_gap_{p}.yaml") for p in ("shared", "private")]
    for t in tasks:
        assert validate_task(t) == []
    traj = run_episode(tasks[1], make_factory("scripted"))
    assert traj["result"]["rounds_used"] == 11
    note = sector_note_ko(traj, tasks[1])
    assert uncited_figures_ko(note) == []
    assert "0.83%p [사실 `calc.shinhan_roe_gap_pp` · 접수번호 20260423800235" in note
    assert "2.89%p [사실 `calc.hana_roe_gap_pp` · 접수번호 20260724800373" in note
    targets = {t.code: t.threshold for t in vu.TARGETS if t.metric == "roe"}
    assert all(f["value"] == targets[f["company"]] for a in tasks[0].agents for f in a.get("initial_facts") or [])


def test_k06_mistakes_are_wrong_answers():
    spec = next(s for s in SPECS_KR_FHC if s["pair"] == "k06")
    cps = {cp["field"]: cp for cp in spec["checkpoints"]}
    hist = vu.load()["history"]
    for g, target in (("shinhan", 10.0), ("hana", 12.0)):
        code = FHC[g]
        s = _st(code)
        fy, h1p, h1 = (s[k]["net_income_owners"] for k in ("2025:FY:CFS", "2025:H1:CFS", "2026:H1:CFS"))
        e0, e1 = s["2025:H1:CFS"]["equity_owners"], s["2026:H1:CFS"]["equity_owners"]
        roe_cp, gap_cp = cps[f"{g}_ttm_roe"], cps[f"{g}_roe_gap_pp"]
        right = (fy - h1p + h1) / ((e0 + e1) / 2) * 100
        assert not _off(right, roe_cp) and not _off(target - right, gap_cp)
        assert _off((fy - h1p + h1) / e1 * 100, roe_cp)                                      # period-end equity only
        ce = lambda k: s[k]["equity_owners"] - s[k]["hybrid_capital"]
        assert _off((fy - h1p + h1) / ((ce("2025:H1:CFS") + ce("2026:H1:CFS")) / 2) * 100, roe_cp)  # hybrid removed
        ni = lambda k: s[k]["net_income"]
        assert _off((ni("2025:FY:CFS") - ni("2025:H1:CFS") + ni("2026:H1:CFS")) / ((e0 + e1) / 2) * 100, roe_cp)
        dec25 = s["2025:FY:CFS"]["equity_owners"]
        assert _off(2 * h1 / ((dec25 + e1) / 2) * 100, roe_cp)                               # annualized H1
        dec24 = hist[code]["2024:FY"]["equity_owners"]
        assert _off(fy / ((dec24 + dec25) / 2) * 100, roe_cp)                                # fiscal 2025 ROE
        assert _off(right / 100, roe_cp)                                                     # fraction
        assert _off(right - target, gap_cp)                                                  # sign reversed
        assert _off((target - right) / target * 100, gap_cp)                                 # relative gap
    assert _off(cps["shinhan_ttm_roe"]["expected"], cps["hana_ttm_roe"])                     # groups swapped
