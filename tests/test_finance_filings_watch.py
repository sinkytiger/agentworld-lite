"""3분기 실적 자동 갱신 (agentworld_lite/finance/filings_watch.py), offline. The DART list comes from a synthetic
fixture (tests/fixtures/dart_watch), and the fake client makes synthetic Q3 2026 statements from each group's H1 ones
(flows x 1.5, balances x 1.01): test data, not filings. Every run works on a copy of the dataset."""

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from agentworld_lite.finance import filings_watch as fw
from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.dart import FHC_ACCOUNT_RULES
from agentworld_lite.finance.note_ko import fmt_krw, uncited_figures_ko

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "finance" / "dart_fhc"
LISTS = json.loads((ROOT / "tests" / "fixtures" / "dart_watch" / "list_2026q3.json").read_text(encoding="utf-8"))
BALANCES = {k for k, (kinds, _, _) in FHC_ACCOUNT_RULES.items() if "BS" in kinds}


class FakeClient:
    def __init__(self, data_dir: Path):
        self.data = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in data_dir.glob("*.json")}
        self.by_corp = {d["corp_code"]: code for code, d in self.data.items()}
        self.calls = []

    def resolve(self, company):
        d = self.data[company]
        return {"corp_code": d["corp_code"], "corp_name": d["name"], "stock_code": company}

    def filing_list(self, corp_code, bgn_de, end_de, kind="A"):
        self.calls.append(("list", corp_code, bgn_de, end_de, kind))
        if end_de == dt.date.today().strftime("%Y%m%d"):  # fetch_company lists up to today; the fixture is dated later
            end_de = "29991231"
        return [r for r in LISTS[self.by_corp[corp_code]] if bgn_de <= r["rcept_no"][:8] <= end_de]

    def statement(self, corp_code, year, period, basis, profile="default"):
        self.calls.append(("statement", corp_code, year, period, basis, profile))
        code = self.by_corp[corp_code]
        h1 = self.data[code]["statements"].get(f"{year}:H1:{basis}")
        q3 = [r["rcept_no"] for r in LISTS[code] if fw.parse_report(r["report_nm"])[:2] == (year, "Q3")]
        if period != "Q3" or not h1 or not q3:
            return None
        accounts = {k: v for k, v in h1.items() if k in FHC_ACCOUNT_RULES}
        st = {k: round(v * (1.01 if k in BALANCES else 1.5)) for k, v in accounts.items()}
        return {"filed": fw.filed_date(max(q3)), "rcept_no": max(q3), **st}

    def shares(self, corp_code, year, period):
        return None  # quarterly reports may leave the share table empty


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "dart_fhc"
    shutil.copytree(DATA, d)
    return d


def test_report_names_are_placed_by_period():
    assert fw.parse_report("분기보고서 (2026.09)") == (2026, "Q3", False)
    assert fw.parse_report("[기재정정]반기보고서 (2026.06)") == (2026, "H1", True)
    assert fw.parse_report("[첨부정정][기재정정]사업보고서 (2025.12)") == (2025, "FY", True)
    assert fw.parse_report("분기보고서 (2026.03)") == (2026, "Q1", False)
    assert fw.parse_report("임원ㆍ주요주주특정증권등소유상황보고서") is None
    assert fw.parse_report("반기보고서 (2026.09)") is None


def test_scan_finds_new_periods_and_amendments_only(data_dir):
    client = FakeClient(data_dir)
    events = fw.scan(client, data_dir, until="20261130")
    since = {c[1]: c[2] for c in client.calls}                         # each group from its own latest filing
    assert since == {client.data[code]["corp_code"]: d for code, d in {
        "105560": "20260814", "055550": "20261007", "086790": "20260814", "316140": "20260814"}.items()}
    assert {c[4] for c in client.calls} == {"A"}                       # periodic reports only
    assert [(e.name, e.kind, e.year, e.period, e.rcept_no, e.dataset_rcept_no) for e in events] == [
        ("KB금융", "new", 2026, "Q3", "20261113900001", None),
        ("신한지주", "new", 2026, "Q3", "20261120900003", None),           # the latest filing of the new period
        ("우리금융지주", "amended", 2026, "H1", "20261118900004", "20260814003410")]
    assert events[1].report == "[기재정정]분기보고서 (2026.09)" and events[1].filed == "2026-11-20"
    assert fw.scan(client, data_dir, until="20261110") == []           # before the Q3 reports
    wide = fw.scan(client, data_dir, since="20260801", until="20261130")  # KB's H1 row is already in the dataset
    assert [e.rcept_no for e in wide] == [e.rcept_no for e in events]


def test_state_remembers_when_a_filing_was_first_seen(data_dir, tmp_path):
    state = tmp_path / "state.json"
    events = fw.scan(FakeClient(data_dir), data_dir, until="20261130")
    fw.remember(events, state, today="2026-11-21")
    again = fw.scan(FakeClient(data_dir), data_dir, until="20261130")
    fw.remember(again, state, today="2026-11-25")
    assert {e.first_seen for e in again} == {"2026-11-21"}
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["last_run"] == "2026-11-25" and set(saved["seen"]) == {e.rcept_no for e in events}


def test_apply_adds_new_periods_and_leaves_amendments(data_dir, tmp_path):
    client = FakeClient(data_dir)
    woori_before = (data_dir / "316140.json").read_text(encoding="utf-8")
    logs = []
    events = fw.run(client, data_dir, until="20261130", do_apply=True, state_path=tmp_path / "s.json",
                    log=logs.append)
    assert [e.applied for e in events] == [True, True, False]
    sh = json.loads((data_dir / "055550.json").read_text(encoding="utf-8"))["statements"]["2026:Q3:CFS"]
    assert (sh["rcept_no"], sh["form"], sh["first_rcept_no"], sh["first_filed"]) == (
        "20261120900003", "[기재정정]분기보고서", "20261114900002", "2026-11-14")
    assert set(json.loads((data_dir / "105560.json").read_text(encoding="utf-8"))["statements"]) >= {
        "2026:Q3:CFS", "2026:Q3:OFS"}
    assert (data_dir / "316140.json").read_text(encoding="utf-8") == woori_before
    assert any("정정 보고서는 자동 반영하지 않습니다" in s for s in logs)
    assert ("statement", client.data["055550"]["corp_code"], 2026, "Q3", "CFS", "fhc") in client.calls
    left = fw.scan(FakeClient(data_dir), data_dir, until="20261130")
    assert [(e.name, e.kind) for e in left] == [("우리금융지주", "amended")]


def test_q3_note_cites_every_figure(data_dir, tmp_path):
    fw.run(FakeClient(data_dir), data_dir, until="20261130", do_apply=True, state_path=None, log=lambda s: None)
    md = fw.render_q3_note(data_dir)
    assert md.startswith("# 4대 금융지주 2026년 3분기 실적 (공시 자동 갱신)\n") and fw.NOTICE in md
    assert uncited_figures_ko(md) == []
    kb = json.loads((DATA / "105560.json").read_text(encoding="utf-8"))["statements"]
    q3 = round(kb["2026:H1:CFS"]["net_income_owners"] * 1.5) - kb["2026:H1:CFS"]["net_income_owners"]
    assert f"| KB금융 | {fmt_krw(q3)} [사실 `105560.2026Q3.CFS.net_income_owners`, `105560.2026H1.CFS.net_income_owners`" \
           f" · 접수번호 20261113900001, 20260814004200]" in md
    assert "아직 3분기보고서가 데이터셋에 없는 그룹: 하나금융지주, 우리금융지주." in md
    assert "| 신한지주 | [기재정정]분기보고서 | [20261120900003](" in md and "| 20261114900002 |" in md


def test_without_q3_reports_the_note_says_so_and_cli_works_offline(tmp_path):
    md = fw.render_q3_note(DATA)
    assert "아직 데이터셋에 들어온 3분기보고서가 없습니다" in md and "|" not in md
    out = tmp_path / "q3.md"
    assert finance_main(["watch-filings", "--note-only", "--note", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == md


def test_the_watcher_schedules_nothing_and_never_runs_git():
    src = Path(fw.__file__).read_text(encoding="utf-8")
    for word in ("subprocess", "os.system", "schtasks", "crontab", "Register-ScheduledTask", "git "):
        assert word not in src.split('"""', 2)[2], word                  # outside the module docstring
