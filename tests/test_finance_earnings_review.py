"""DART 실적 리뷰 (agentworld_lite/finance/earnings_review.py), offline. The 2026 Q3 reports used here are synthetic
(flows x 1.5 and balances x 1.01 of each group's H1 report, receipt numbers that do not exist on DART); a fake client
stands in for the model."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentworld_lite.finance import ai_report as ar
from agentworld_lite.finance import earnings_review as er
from agentworld_lite.finance.dart import FHC_ACCOUNT_RULES
from agentworld_lite.finance.note_ko import uncited_figures_ko
from agentworld_lite.llm import UsageMeter

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "finance" / "dart_fhc"
BALANCES = {k for k, (kinds, _, _) in FHC_ACCOUNT_RULES.items() if "BS" in kinds}


def _st(code, key):
    return json.loads((DATA / f"{code}.json").read_text(encoding="utf-8"))["statements"][key]


def test_latest_report_review_matches_the_desk_answers():
    rv = er.review("105560", today="2026-10-22")
    assert (rv.year, rv.period, rv.label) == (2026, "H1", "2026년 2분기")
    ni = rv.now.values["net_income_owners"][0]
    assert ni == _st("105560", "2026:H1:CFS")["net_income_owners"] - _st("105560", "2026:Q1:CFS")["net_income_owners"]
    assert round((ni / rv.year_ago.values["net_income_owners"][0] - 1) * 100, 1) == 14.6        # k04 answer
    assert round(er._cir(rv.now)[0], 2) == 36.89 and round(rv.ttm_roe[0], 2) == 10.47           # doc, value-up
    md = er.render(rv)
    assert md.startswith("# KB금융 2026년 2분기 실적 리뷰\n") and er.BANNER in md and uncited_figures_ko(md) == []
    assert "| 지배주주순이익 | 1조 9,922억원 [사실 `105560.2026H1.CFS.net_income_owners`" in md
    assert "목표 ROE 10.00% [사실 `105560.target.roe` · 접수번호 20241024800258] 이상(기한 명시 없음): 달성." in md
    assert "이번 보고서 기준 13.74% [사실 `105560.cet1.2026H1`" in md
    flags = {name: note for name, ok, note in rv.checks if not ok}
    assert list(flags) == ["핵심 이익의 전년 대비 변동이 크지 않음"]                            # fee income +55%
    assert flags["핵심 이익의 전년 대비 변동이 크지 않음"].startswith("순수수료이익: XBRL 계정은 두 해 같음")


def test_targets_read_from_images_and_the_group_argument():
    md = er.render(er.review("316140", today="2026-10-22"))
    capital = md.split("## 검사")[0].split("## 자본")[1]
    assert "목표 ROE 10.00% [사실 `316140.target.roe` · 접수번호 20260206801698]" in capital
    assert "이번 보고서의 CET1 비율은 데이터셋에 없음(확인 필요)" in capital
    assert er.groups("all") == ["105560", "055550", "086790", "316140"]
    assert er.groups("신한지주") == er.groups("055550") == er.groups("shinhan") == ["055550"]
    with pytest.raises(er.ReviewError, match="unknown group"):
        er.groups("삼성전자")


@pytest.fixture
def q3_data(tmp_path):
    """The dataset with synthetic 2026 Q3 reports for all four groups."""
    d = tmp_path / "dart_fhc"
    shutil.copytree(DATA, d)
    for i, p in enumerate(sorted(d.glob("*.json"))):
        data = json.loads(p.read_text(encoding="utf-8"))
        h1 = data["statements"]["2026:H1:CFS"]
        rcept = f"2026111{3 + i}90000{i + 1}"
        q3 = {k: round(v * (1.01 if k in BALANCES else 1.5)) for k, v in h1.items() if k in FHC_ACCOUNT_RULES}
        data["statements"]["2026:Q3:CFS"] = {"rcept_no": rcept, "filed": f"2026-11-1{3 + i}", "form": "분기보고서",
                                             "concepts": h1.get("concepts", {}), **q3}
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def test_a_new_q3_report_is_reviewed_against_last_year_and_the_previous_quarter(q3_data):
    rv = er.review("055550", data_dir=q3_data, today="2026-11-20")
    assert (rv.period, rv.label, rv.prev.period, rv.year_ago.period) == ("Q3", "2026년 3분기", "H1", "Q3")
    h1 = _st("055550", "2026:H1:CFS")["net_income_owners"]
    assert rv.now.values["net_income_owners"][0] == round(h1 * 1.5) - h1
    ni25 = _st("055550", "2025:Q3:CFS")["net_income_owners"] - _st("055550", "2025:H1:CFS")["net_income_owners"]
    assert rv.year_ago.values["net_income_owners"][0] == ni25
    md = er.render(rv)
    assert uncited_figures_ko(md) == [] and "3분기 = 3분기 누적(1~9월) − 직전 누적 보고서" in md
    assert "이번 보고서의 CET1 비율은 데이터셋에 없음(확인 필요)" in md
    assert all(ok for name, ok, _ in rv.checks if name.startswith("보고서 접수일"))
    early = er.review("055550", data_dir=q3_data, today="2026-11-01")
    assert not next(ok for name, ok, _ in early.checks if name.startswith("보고서 접수일"))
    model = er.coverage_model(rv, er.Dataset(q3_data))
    assert list(model["quarters"])[-3:] == ["2026 1분기", "2026 2분기", "2026 3분기"]
    q3_rcept = json.loads((q3_data / "055550.json").read_text(encoding="utf-8"))["statements"]["2026:Q3:CFS"]["rcept_no"]
    assert model["quarters"]["2026 3분기"]["net_income_owners"]["rcept_no"] == ["20261007000223", q3_rcept]
    refs = ar.references(q3_data)                                        # the fact-check now knows Q3
    assert any(r.label == "4개 그룹 2026년 3분기 지배주주순이익 합계" for r in refs)
    rec = ar.check_answer(f"신한지주 2026년 3분기 지배주주순이익은 {ar.fmt_krw(rv.now.values['net_income_owners'][0])}입니다.",
                          refs)
    assert rec[0]["verdict"] == "일치" and rec[0]["ref"]["label"] == "신한지주 2026년 3분기 지배주주순이익"


def test_write_stages_the_review_and_the_model(tmp_path):
    rv = er.review("086790", today="2026-10-22")
    d = er.write(rv, tmp_path)
    assert d.name == "086790_2026H1" and {p.name for p in d.iterdir()} == {"review.md", "model.json"}
    assert b"\r\n" not in (d / "review.md").read_bytes()


class FakeClient:
    def __init__(self, text):
        self.model, self.text, self.calls, self.usage = "claude-opus-5-5", text, [], UsageMeter()

    def create(self, **params):
        self.calls.append(params)
        resp = SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason="end_turn",
                               model=self.model, usage=SimpleNamespace(input_tokens=9000, output_tokens=2000,
                                                                      cache_read_input_tokens=0,
                                                                      cache_creation_input_tokens=0))
        return resp, self.usage.add(resp.usage, self.model)


def test_model_draft_is_fact_checked_before_it_is_kept(tmp_path):
    rv = er.review("055550", today="2026-10-22")
    q2 = "`055550.2026H1.CFS.net_income_owners` - `055550.2026Q1.CFS.net_income_owners`"
    good = FakeClient(f"## 신한지주 2분기\n- 2026년 2분기 지배주주순이익 1조 8,201억원 [계산: {q2}]\n")
    res = er.draft(good, rv, tmp_path, log=lambda s: None)
    assert res["status"] == "검토 가능" and res["summary"]["인용 일치"] == 1 and res["summary"]["일치"] == 1
    prompt = good.calls[0]["messages"][0]["content"]
    assert good.calls[0]["system"] == er.SYSTEM and ar.DATA_TAIL in prompt and "`055550.target.roe`" in prompt
    assert "| `055550.2025Q3.CFS.net_income_owners` |" not in prompt               # only what the review uses
    bad = FakeClient(f"## 신한지주 2분기\n- 2026년 2분기 지배주주순이익 1조 9,000억원 [계산: {q2}]\n")
    res = er.draft(bad, rv, tmp_path, log=lambda s: None)
    assert res["status"] == "수정 필요" and res["summary"]["인용 불일치"] == 1 and res["summary"]["불일치"] == 1
    d = tmp_path / "055550_2026H1"
    assert {p.name for p in d.iterdir()} >= {"draft_prompt.md", "draft.md", "draft_check.json"}
    pricey = FakeClient("")
    with pytest.raises(er.ReviewError, match="exceeds --max-cost"):
        er.draft(pricey, rv, tmp_path, max_cost=0.05, log=lambda s: None)
    assert pricey.calls == []


def test_the_reviewer_posts_nothing_and_never_runs_git():
    src = Path(er.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    for word in ("subprocess", "os.system", "git ", "requests.post", "smtplib", "urlopen"):
        assert word not in src, word
