"""Korean desk on real DART filings of the four financial holding companies (4대 금융지주): the fhc account profile,
share counts, amended reports, the dataset, the k01-k03 tasks, and the Korean sector note and valuation snapshot.
No test touches the network: DART responses come from trimmed real replies in tests/fixtures/dart_fhc."""

import copy
import itertools
import json
import re
import shutil
import statistics
from pathlib import Path

import pytest

from agentworld_lite.agents import BaseAgent, Decision
from agentworld_lite.finance import dart
from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.catalog import FHC, SPECS_KR_FHC, build_task
from agentworld_lite.finance.dataset import Dataset
from agentworld_lite.finance.env import build_env
from agentworld_lite.finance.note import NoteError
from agentworld_lite.finance.note_ko import fmt_krw, sector_note_ko, uncited_figures_ko
from agentworld_lite.finance.run import make_factory, run_episode, validate_task
from agentworld_lite.finance.tools import SYSTEM_PROMPT_KR_FHC
from agentworld_lite.finance.valuation import ValuationError
from agentworld_lite.finance.valuation_ko import NOTICE, PRICE_DATE, RATING_WORDS_KO, prices_ready, snapshot_ko
from agentworld_lite.task import Task, load_tasks

ROOT = Path(__file__).resolve().parents[1]
KR_DIR = ROOT / "tasks" / "finance_kr"
DATA = ROOT / "data" / "finance" / "dart_fhc"
FIX = Path(__file__).resolve().parent / "fixtures" / "dart_fhc"
B = 100_000_000  # 1억원


def _fixture(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))["list"]


def _group(code):
    return json.loads((DATA / f"{code}.json").read_text(encoding="utf-8"))


def _st(code):
    return _group(code)["statements"]


def _off(got, cp):
    return abs(got - cp["expected"]) > cp["rel_tol"] * abs(cp["expected"])


def _specs():
    return {s["pair"]: s for s in SPECS_KR_FHC}


# ---------------------------------------------------------------------- DART normalization (fixtures)
def test_fhc_profile_reads_bank_format_statements_by_xbrl_id():
    sh = dart.normalize_statement(_fixture("055550_2026H1_CFS"), "H1", "fhc")
    assert sh["rcept_no"] == "20261007000223" and sh["filed"] == "2026-10-07"
    assert sh["general_admin_expense"] == 3_216_096_000_000           # Shinhan tags G&A without "Selling"
    assert sh["concepts"]["general_admin_expense"] == "ifrs-full_GeneralAndAdministrativeExpense"
    kb = dart.normalize_statement(_fixture("105560_2026H1_CFS"), "H1", "fhc")
    assert kb["concepts"]["general_admin_expense"] == "ifrs-full_SellingGeneralAndAdministrativeExpense"
    assert kb["net_interest_income"] == 6_478_260_000_000 and kb["net_fee_income"] == 2_961_245_000_000
    assert kb["hybrid_capital"] == 4_206_721_000_000 and kb["concepts"]["hybrid_capital"] == "dart_HybridBonds"
    hana = dart.normalize_statement(_fixture("086790_2026H1_CFS"), "H1", "fhc")
    assert hana["net_income_owners"] == 2_402_868_000_000                # labelled "반기순이익(손실)" by Hana
    assert hana["concepts"]["net_income_owners"] == "ifrs-full_ProfitLossAttributableToOwnersOfParent"
    for st in (sh, kb, hana):                                            # every account matched by id, none by name
        assert set(st["concepts"]) == set(dart.FHC_ACCOUNT_RULES)
        assert not any(c.startswith("name:") for c in st["concepts"].values())


def test_2025_q1_reports_use_other_credit_loss_elements():
    kb = dart.normalize_statement(_fixture("105560_2025Q1_CFS"), "Q1", "fhc")
    assert kb["credit_loss_provision"] == 655_597_000_000
    assert kb["concepts"]["credit_loss_provision"] == "dart_AdditionReversalOfCreditLossFinancialAssets"
    hana = dart.normalize_statement(_fixture("086790_2025Q1_CFS"), "Q1", "fhc")
    assert hana["credit_loss_provision"] == 297_226_000_000
    assert hana["concepts"]["credit_loss_provision"].startswith("ifrs-full_AdditionalAllowanceRecognisedInProfitOrLoss")


def test_name_fallback_never_takes_a_comprehensive_income_row():
    rows = [{"sj_div": "CIS", "account_id": "ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent",
             "account_nm": "지배기업 소유주지분", "thstrm_amount": "9", "thstrm_add_amount": "99", "rcept_no": "20260814000001"},
            {"sj_div": "CIS", "account_id": "-표준계정코드 미사용-", "account_nm": "지배기업 소유주지분",
             "thstrm_amount": "5", "thstrm_add_amount": "50", "rcept_no": "20260814000001"}]
    st = dart.normalize_statement(rows, "H1", "fhc")
    assert st["net_income_owners"] == 50 and st["concepts"]["net_income_owners"] == "name:지배기업 소유주지분"
    assert dart.normalize_statement(rows, "H1")["net_income_owners"] == 50   # the default profile too
    assert "concepts" not in dart.normalize_statement(rows, "H1")            # default output is unchanged
    with pytest.raises(dart.DartError, match="unknown account profile"):
        dart.normalize_statement(rows, "H1", "bank")


def test_share_counts_with_real_dart_labels():
    sh = dart.normalize_shares(_fixture("055550_2026H1_shares"))  # "의결권 있는\n보통주", preferred all retired ("-")
    assert sh == {"filed": "2026-10-07", "rcept_no": "20261007000223", "common": 474_654_361, "preferred": 0,
                  "treasury_common": 5_204_123}
    kb = dart.normalize_shares(_fixture("105560_2026H1_shares"))
    assert (kb["common"], kb["treasury_common"], kb["preferred"]) == (354_687_734, 1_300_000, 0)
    assert dart.normalize_shares(_fixture("055550_2026Q1_shares")) is None   # quarterly report: table left as "-"


def test_amended_reports_keep_the_served_version_and_record_the_original():
    filings = dart.find_filings(_fixture("055550_list"), 2026, "H1")
    assert [f["rcept_no"] for f in filings] == ["20260814003306", "20261007000223"]
    assert [f["rcept_no"] for f in dart.find_filings(_fixture("055550_list"), 2025, "FY")] == [
        "20260318000826", "20261007000218"]
    assert dart.find_filings(_fixture("055550_list"), 2026, "Q3") == []
    st = dart.normalize_statement(_fixture("055550_2026H1_CFS"), "H1", "fhc")
    dart._mark_amendments(st, filings)
    assert st["form"] == "[기재정정]반기보고서" and st["filed"] == "2026-10-07"
    assert (st["first_rcept_no"], st["first_filed"]) == ("20260814003306", "2026-08-14")
    q1 = dart.normalize_statement(_fixture("055550_2026Q1_CFS"), "Q1", "fhc")
    dart._mark_amendments(q1, dart.find_filings(_fixture("055550_list"), 2026, "Q1"))
    assert q1["form"] == "분기보고서" and "first_rcept_no" not in q1


def _serve_fixtures(monkeypatch, client, seen):
    def data(self, endpoint, **p):
        seen.append(endpoint)
        if endpoint == "list.json":
            name = "055550_list"
        else:
            period = {v: k for k, v in dart.REPORT_CODES.items()}[p["reprt_code"]]
            kind = "shares" if endpoint.startswith("stockTotqy") else p["fs_div"]
            name = f"055550_{p['bsns_year']}{period}_{kind}"
        path = FIX / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    monkeypatch.setattr(dart.DartClient, "_data", data)
    monkeypatch.setattr(client, "corp_codes", lambda refresh=False: [
        {"corp_code": "00382199", "corp_name": "신한지주", "stock_code": "055550", "modify_date": ""}])


def test_fetch_company_fhc_offline_writes_a_loadable_dataset(tmp_path, monkeypatch):
    client = dart.DartClient(api_key="TEST-KEY-NOT-REAL", cache_dir=tmp_path / "cache", pause=0)
    seen = []
    _serve_fixtures(monkeypatch, client, seen)
    path = dart.fetch_company(client, "055550", ["2026:Q1", "2026:H1"], bases=("CFS",), out_dir=tmp_path / "out",
                              log=lambda s: None, profile="fhc", prices={"2026-06-30": 50000.0},
                              price_sources={"2026-06-30": "test source"}, first_filings=True)
    raw = path.read_bytes()
    assert b"\r\n" not in raw and b"TEST-KEY" not in raw
    data = json.loads(raw)
    assert data["market"] == "kr_fhc" and data["profile"] == "fhc" and "Shinhan" in data["aliases"]
    assert data["accounts"]["hybrid_capital"] == {"label": "신종자본증권", "unit": "money"}
    assert set(data["shares"]) == {"2026:H1"}                                  # the empty Q1 table is not stored
    h1 = data["statements"]["2026:H1:CFS"]
    assert h1["form"] == "[기재정정]반기보고서" and h1["first_filed"] == "2026-08-14"
    assert seen.count("list.json") == 1
    ds = Dataset(tmp_path / "out")
    assert ds.market == "kr_fhc" and ds.lang == "ko" and ds.find("신한금융").stock_code == "055550"
    co = ds.find("Shinhan Financial Group")
    assert co.accounts()["general_admin_expense"] == ("일반관리비", "KRW")
    from agentworld_lite.finance.dataset import price_fact
    assert price_fact(co, "2026-06-30").meta["source"] == "test source"


def test_fetch_cli_takes_one_price_per_company(monkeypatch, tmp_path):
    calls = []

    class Fake:
        def build_company(self, company, periods, *, prices=None, price_sources=None, out_dir=None, **_):
            calls.append((company, periods, prices, price_sources))
            return Path(out_dir) / f"{company}.json"

    import agentworld_lite.finance.sources as sources
    monkeypatch.setattr(sources, "get_source", lambda name, **kw: Fake())
    assert finance_main(["fetch", "--source", "dart", "--profile", "fhc", "--company", "105560", "316140",
                         "--price", "105560@2026-06-30=100,000", "316140@2026-06-30=20000",
                         "--price-source", "KRX test", "--out", str(tmp_path)]) == 0
    assert calls == [("105560", [], {"2026-06-30": 100000.0}, {"2026-06-30": "KRX test"}),
                     ("316140", [], {"2026-06-30": 20000.0}, {"2026-06-30": "KRX test"})]
    assert finance_main(["fetch", "--source", "dart", "--company", "105560", "--price", "999999@2026-06-30=1"]) == 1


# ---------------------------------------------------------------------- the committed dataset
def test_dataset_has_every_account_for_every_period_and_group():
    for code in FHC.values():
        data = _group(code)
        assert data["market"] == "kr_fhc" and data["fiscal_year_end"] == "12-31"
        for y, p in itertools.product((2025, 2026), ("Q1", "H1", "Q3", "FY")):
            if (y, p) in ((2026, "Q3"), (2026, "FY")):
                continue
            st = data["statements"][f"{y}:{p}:CFS"]
            assert set(dart.FHC_ACCOUNT_RULES) <= set(st), (code, y, p)
            assert st["credit_loss_provision"] > 0 and st["general_admin_expense"] > 0
            assert st["filed"] == dart.filed_date(st["rcept_no"]) and st["form"].endswith("보고서")
            assert st["filed"] <= "2026-10-15"
        assert {"2025:FY", "2026:H1"} <= set(data["shares"])
        assert data["shares"]["2026:H1"]["common"] < data["shares"]["2025:FY"]["common"]   # shares cancelled in 2026


def test_dataset_matches_each_groups_earnings_release():
    """2Q 2026 releases (23-24 July 2026), 억원: net income to owners for H1 and Q2; KB's H1 CIR; Woori's H1 순영업수익."""
    released = {"105560": (38_846, 19_922), "055550": (34_427, 18_201), "086790": (24_029, 11_928),
                "316140": (16_089, 10_046)}
    for code, (h1, q2) in released.items():
        st = _st(code)
        got_h1 = st["2026:H1:CFS"]["net_income_owners"]
        assert round(got_h1 / B) == h1 and round((got_h1 - st["2026:Q1:CFS"]["net_income_owners"]) / B) == q2
    kb = _st("105560")["2026:H1:CFS"]
    rev = lambda s: s["operating_income"] + s["general_admin_expense"] + s["credit_loss_provision"]
    assert round(kb["general_admin_expense"] / rev(kb) * 100, 1) == 36.2
    assert round(rev(_st("316140")["2026:H1:CFS"]) / B) == 57_227


def test_amended_reports_in_the_dataset():
    amended = {(code, k): st["first_filed"] for code in FHC.values() for k, st in _st(code).items()
               if k.endswith("CFS") and st.get("first_rcept_no")}
    assert amended[("055550", "2026:H1:CFS")] == "2026-08-14" and _st("055550")["2026:H1:CFS"]["filed"] == "2026-10-07"
    assert amended[("105560", "2025:H1:CFS")] == "2025-08-14" and _st("105560")["2025:H1:CFS"]["filed"] == "2026-03-24"
    assert ("316140", "2026:H1:CFS") not in amended


# ---------------------------------------------------------------------- tasks
def test_committed_kr_tasks_match_the_catalog():
    on_disk = sorted(p.stem for p in KR_DIR.glob("*.yaml"))
    built = sorted(f"{s['pair']}_{s['slug']}_{pl}" for s in SPECS_KR_FHC for pl in ("shared", "private"))
    assert on_disk == built, "run `python -m agentworld_lite.finance make-tasks`"
    for spec in SPECS_KR_FHC:
        for placement in ("shared", "private"):
            t = Task.from_dict(build_task(spec, placement))
            assert Task.load(KR_DIR / f"{t.id}.yaml").to_dict() == t.to_dict()


def test_kr_reference_solves_and_idle_team_fails():
    tasks = load_tasks(str(KR_DIR))
    assert len(tasks) == 2 * len(SPECS_KR_FHC) == 6
    for t in tasks:
        assert validate_task(t) == [], t.id
        assert not any(f.get("price_of") for a in t.agents for f in a.get("initial_facts") or [])  # DART only, no prices
    rounds = {t.id.rsplit("_", 1)[0]: run_episode(t, make_factory("scripted"))["result"]["rounds_used"] for t in tasks}
    assert rounds == {"k01_kb_bps_roe": 10, "k02_q2_cost_income": 11, "k03_big4_q2_sector": 15}


def test_kr_pairs_differ_only_in_where_the_sentences_are():
    for spec in SPECS_KR_FHC:
        shared, private = build_task(spec, "shared"), build_task(spec, "private")
        moved = [s for owner in spec["facts"].values() for s in owner]
        assert all(s in shared["relevant_game_context"] for s in moved)
        assert not any(s in private["relevant_game_context"] for s in moved)
        strip = lambda d: {k: v for k, v in d.items() if k not in ("id", "relevant_game_context", "matched", "agents")}
        assert strip(shared) == strip(private)


def test_k01_mistakes_are_wrong_answers():
    cps = {cp["field"]: cp for cp in _specs()["k01"]["checkpoints"]}
    st, sh = _st("105560"), _group("105560")["shares"]
    fy, prev, h1, q1 = st["2025:FY:CFS"], st["2025:H1:CFS"], st["2026:H1:CFS"], st["2026:Q1:CFS"]
    ttm = lambda k: fy[k] - prev[k] + h1[k]
    ce = lambda s: s["equity_owners"] - s["hybrid_capital"]
    issued, treasury = sh["2026:H1"]["common"], sh["2026:H1"]["treasury_common"]
    ni, ni_cp, bps, roe = ttm("net_income_owners"), cps["ttm_net_income_owners"], cps["bps"], cps["ttm_roe"]
    avg = (ce(prev) + ce(h1)) / 2
    assert not _off(ni, ni_cp) and not _off(ce(h1) / (issued - treasury), bps) and not _off(ni / avg * 100, roe)
    assert round(cps["bps"]["expected"]) == 160_402 and round(cps["ttm_roe"]["expected"], 2) == 11.36
    assert _off(ttm("net_income"), ni_cp)                                           # incl. non-controlling interests
    assert _off(fy["net_income_owners"], ni_cp)                                     # last fiscal year only
    assert _off(2 * h1["net_income_owners"], ni_cp)                                 # H1 annualized
    assert _off(4 * (h1["net_income_owners"] - q1["net_income_owners"]), ni_cp)     # Q2 annualized
    ofs = st["2025:FY:OFS"]["net_income"] - st["2025:H1:OFS"]["net_income"] + st["2026:H1:OFS"]["net_income"]
    assert _off(ofs, ni_cp)                                                         # separate statements
    assert _off(ce(h1) / issued, bps)                                               # treasury shares not subtracted
    assert _off(h1["equity_owners"] / (issued - treasury), bps)                     # hybrid capital left in
    assert _off(h1["equity_total"] / (issued - treasury), bps)                      # incl. non-controlling interests
    fy_sh = sh["2025:FY"]
    assert _off(ce(h1) / (fy_sh["common"] - fy_sh["treasury_common"]), bps)        # year-end share count
    assert _off(ce(fy) / (issued - treasury), bps)                                  # year-end equity
    assert _off(ni / ce(h1) * 100, roe)                                             # period-end equity, not average
    assert _off(ni / ((ce(fy) + ce(h1)) / 2) * 100, roe)                            # average from the year end
    assert _off(ni / ((prev["equity_owners"] + h1["equity_owners"]) / 2) * 100, roe)  # hybrid capital left in
    assert _off(ni / ((prev["equity_total"] + h1["equity_total"]) / 2) * 100, roe)
    assert _off(2 * h1["net_income_owners"] / ((ce(fy) + ce(h1)) / 2) * 100, roe)  # annualized H1 ROE
    assert _off(ttm("net_income") / avg * 100, roe)
    assert _off(ni / avg, roe)                                                      # fraction, not percent
    assert not sh["2026:H1"]["preferred"]                                           # KB has no preferred shares


def test_k02_mistakes_are_wrong_answers():
    cps = {cp["field"]: cp for cp in _specs()["k02"]["checkpoints"]}
    for g in ("shinhan", "woori"):
        st, cp = _st(FHC[g]), cps[f"{g}_q2_cost_income_ratio"]
        q1, h1 = st["2026:Q1:CFS"], st["2026:H1:CFS"]
        q2 = {k: h1[k] - q1[k] for k in h1 if isinstance(h1[k], int)}
        rev = lambda s: s["operating_income"] + s["general_admin_expense"] + s["credit_loss_provision"]
        right = q2["general_admin_expense"] / rev(q2) * 100
        assert not _off(right, cp)
        assert _off(h1["general_admin_expense"] / rev(h1) * 100, cp)                  # H1 cumulative ratio
        assert _off(q1["general_admin_expense"] / rev(q1) * 100, cp)                  # Q1 ratio
        assert _off(q2["general_admin_expense"] / (q2["net_interest_income"] + q2["net_fee_income"]) * 100, cp)
        assert _off(q2["general_admin_expense"] / (q2["operating_income"] + q2["general_admin_expense"]) * 100, cp)
        assert _off(q2["general_admin_expense"] / q2["operating_income"] * 100, cp)  # operating income as revenue
        assert _off(right / 100, cp)                                                   # fraction, not percent
    assert _off(cps["shinhan_q2_cost_income_ratio"]["expected"], cps["woori_q2_cost_income_ratio"])  # swapped
    hana = _st(FHC["hana"])  # why Hana is not in k02: its H1 ratio is within the tolerance of its Q2 ratio
    rev = lambda s: s["operating_income"] + s["general_admin_expense"] + s["credit_loss_provision"]
    q1, h1 = hana["2026:Q1:CFS"], hana["2026:H1:CFS"]
    q2 = (h1["general_admin_expense"] - q1["general_admin_expense"]) / (rev(h1) - rev(q1)) * 100
    assert abs(h1["general_admin_expense"] / rev(h1) * 100 - q2) < 0.001 * q2


def test_k03_mistakes_are_wrong_answers():
    cps = {cp["field"]: cp for cp in _specs()["k03"]["checkpoints"]}
    ce = lambda s: s["equity_owners"] - s["hybrid_capital"]
    for g, code in FHC.items():
        st, cp = _st(code), cps[f"{g}_q2_net_income_owners"]
        q1, h1 = st["2026:Q1:CFS"], st["2026:H1:CFS"]
        assert not _off(h1["net_income_owners"] - q1["net_income_owners"], cp)
        assert _off(h1["net_income"] - q1["net_income"], cp)                          # incl. non-controlling interests
        assert _off(h1["net_income_owners"], cp)                                      # H1 cumulative
        assert _off(h1["net_income_owners"] / 2, cp)                                  # average quarter
        assert _off(q1["net_income_owners"], cp)                                      # Q1
        prev = st["2025:H1:CFS"]["net_income_owners"] - st["2025:Q1:CFS"]["net_income_owners"]
        assert _off(prev, cp)                                                         # Q2 of 2025
        assert _off(st["2025:FY:CFS"]["net_income_owners"] - st["2025:Q3:CFS"]["net_income_owners"], cp)  # Q4 2025
        assert _off((h1["net_income_owners"] - q1["net_income_owners"]) / 1e6, cp)   # millions, not KRW
    for a, b in itertools.permutations([f"{g}_q2_net_income_owners" for g in FHC], 2):
        assert _off(cps[a]["expected"], cps[b])                                       # groups swapped
    at = lambda key, f: [f(_st(c)[key]) for c in FHC.values()]
    avg, spread = cps["common_equity_average"], cps["common_equity_spread"]
    now = at("2026:H1:CFS", ce)
    assert not _off(statistics.mean(now), avg) and not _off(max(now) - min(now), spread)
    for f in (lambda s: s["equity_owners"], lambda s: s["equity_total"]):             # hybrid capital left in
        v = at("2026:H1:CFS", f)
        assert _off(statistics.mean(v), avg) and _off(max(v) - min(v), spread)
    for key in ("2026:Q1:CFS", "2025:FY:CFS"):                                        # stale balances
        v = at(key, ce)
        assert _off(statistics.mean(v), avg) and _off(max(v) - min(v), spread)
    assert _off(statistics.median(now), avg)                                          # median, not mean
    assert _off(statistics.mean(now[:3]), avg)                                        # a group left out


def test_as_of_guard_blocks_reports_amended_after_the_as_of_date():
    env = build_env(Task.load(KR_DIR / "k03_big4_q2_sector_shared.yaml"))
    ok = env.execute("collector", "fetch_financials", {"company": "신한금융", "year": 2026, "period": "H1", "basis": "CFS"})
    assert ok.ok and "[기재정정]반기보고서 20261007000223" in ok.message
    env = build_env(Task.load(KR_DIR / "k03_big4_q2_sector_shared.yaml"))
    env.as_of = "2026-10-01"  # the original was filed on 2026-08-14; DART serves the version of 2026-10-07
    r = env.execute("collector", "fetch_financials", {"company": "055550", "year": 2026, "period": "H1", "basis": "CFS"})
    assert not r.ok and "after the as-of date" in r.message
    assert env.execute("collector", "fetch_financials", {"company": "KB", "year": 2026, "period": "H1", "basis": "CFS"}).ok


def test_kr_desk_uses_the_fhc_prompt_and_tools():
    seen = []

    class Probe(BaseAgent):
        def act(self, turn):
            specs = turn.tool_specs(turn.allowed_tools)
            seen.append((turn.system_prompt, {s["name"]: s["description"] for s in specs}))
            return Decision("wait", {"reason": "probe"})

    task = Task.load(KR_DIR / "k02_q2_cost_income_shared.yaml")
    task.max_rounds = 1
    traj = run_episode(task, lambda t, n: Probe(n))
    prompt, specs = seen[0]
    assert prompt == SYSTEM_PROMPT_KR_FHC and "신종자본증권" in prompt
    assert "hybrid_capital" in specs["fetch_financials"] and "fetch_shares" in specs
    assert traj["market"] == "kr_fhc"


# ---------------------------------------------------------------------- Korean note and valuation
def _reference(task_id):
    task = Task.load(KR_DIR / f"{task_id}.yaml")
    return task, run_episode(task, make_factory("scripted"), meta={"agent": "scripted", "model": "scripted reference"})


def test_korean_note_cites_every_figure_with_its_receipt_number():
    task, traj = _reference("k03_big4_q2_sector_private")
    note = sector_note_ko(traj, task)
    assert note.startswith("# 4대 금융지주 2026년 2분기 지배주주순이익과 보통주자본\n")
    assert uncited_figures_ko(note) == []
    assert ("1조 9,922억원 [사실 `calc.kb_q2_net_income_owners` · 접수번호 20260814004200, 20260515002888]" in note)
    assert "47조 6,180억원 [사실 `calc.common_equity_average`" in note
    assert "| 신한지주 | 2026년 반기 | [기재정정]반기보고서 |" in note and "2026-08-14 ([20260814003306]" in note
    assert "언어 모델이 쓰지 않았습니다" in note and "## 계산 과정" in note
    task2, traj2 = _reference("k02_q2_cost_income_shared")
    note2 = sector_note_ko(traj2, task2)
    assert "36.29% [사실 `calc.shinhan_q2_cost_income_ratio`" in note2 and uncited_figures_ko(note2) == []


def test_korean_note_refuses_wrong_or_tampered_runs():
    task, traj = _reference("k03_big4_q2_sector_shared")
    tampered = copy.deepcopy(traj)
    tampered["submission"]["common_equity_spread"]["value"] = 1.0
    with pytest.raises(NoteError, match="recorded"):
        sector_note_ko(tampered, task)

    class Wait(BaseAgent):
        def act(self, turn):
            return Decision("wait", {"reason": "idle"})

    with pytest.raises(NoteError, match="no answers"):
        sector_note_ko(run_episode(task, lambda t, n: Wait(n)), task, require_success=False)


def test_uncited_figures_ko_flags_bare_numbers_only():
    cite = "[사실 `x` · 접수번호 20260814004200]"
    assert uncited_figures_ko(f"순이익 1조 9,922억원 {cite}, 2026년 2분기, `calc.a_2` 코드") == []
    assert uncited_figures_ko("순이익 1조 9,922억원이다") == ["1조 9,922억원"]
    assert uncited_figures_ko("CIR 36.29%, PBR 0.63배, 1,300,000주, 6,043억원") == ["36.29%", "0.63배", "1,300,000주",
                                                                                     "6,043억원"]
    assert uncited_figures_ko("# 2026년 36.29%\n접수번호 20260814004200 기준일 2026-10-15") == []
    assert fmt_krw(1_992_189_000_000) == "1조 9,922억원" and fmt_krw(604_318_000_000) == "6,043억원"
    assert fmt_krw(2e12) == "2조원" and fmt_krw(85_400) == "85,400원" and fmt_krw(-1.5e12) == "-1조 5,000억원"


def test_note_cli_writes_korean_lf_markdown(tmp_path):
    out = tmp_path / "k03.md"
    assert finance_main(["note", "--reference", "k03_big4_q2_sector_shared", "--out", str(out)]) == 0
    raw = out.read_bytes()
    assert b"\r\n" not in raw and raw.decode("utf-8").startswith("# 4대 금융지주")


def _priced(base, prices=None):
    """A copy of the dataset with synthetic closing prices, so the snapshot's arithmetic can be tested offline. They are
    not market prices and never leave the test's temporary directory; the committed dataset has no prices."""
    d = base / "dart_fhc"
    shutil.copytree(DATA, d)
    prices = prices or {"105560": 100_000, "055550": 60_000, "086790": 80_000, "316140": 20_000}
    for code, px in prices.items():
        p = d / f"{code}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["prices"][PRICE_DATE] = px
        data.setdefault("price_sources", {})[PRICE_DATE] = "test price"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def test_committed_dataset_has_no_prices_so_the_snapshot_is_off():
    for code in FHC.values():
        data = _group(code)
        assert data["prices"] == {} and not data.get("price_sources"), code
    assert not prices_ready()
    with pytest.raises(ValuationError, match="DART filings only and ships no prices"):
        snapshot_ko("105560")


def test_valuation_ko_arithmetic_citations_and_notice(tmp_path):
    md, n = snapshot_ko("KB금융", data_dir=_priced(tmp_path))
    st, sh = _st("105560"), _group("105560")["shares"]
    ttm = st["2025:FY:CFS"]["net_income_owners"] - st["2025:H1:CFS"]["net_income_owners"] + st["2026:H1:CFS"]["net_income_owners"]
    mcap = 100_000 * sh["2026:H1"]["common"]
    ce = st["2026:H1:CFS"]["equity_owners"] - st["2026:H1:CFS"]["hybrid_capital"]
    assert n["per"]["105560"] == pytest.approx(mcap / ttm) and n["pbr"]["105560"] == pytest.approx(mcap / ce)
    assert n["roe"]["105560"] == pytest.approx(ttm / ce * 100)
    peers = [n["pbr"][c] for c in FHC.values() if c != "105560"]
    assert n["peer_pbr"] == pytest.approx({"low": min(peers), "median": statistics.median(peers), "high": max(peers)})
    assert n["implied_mcap"]["low"] == pytest.approx(ce * min(peers))
    assert uncited_figures_ko(md) == []
    assert md.startswith("# 밸류에이션 스냅샷: KB금융(105560), 4대 금융지주 비교\n")
    assert f"**{NOTICE}.**" in md and "## 고지" in md and "## 방법" in md
    assert "100,000원 [사실 `105560.price.2026-06-30` · 종가 출처 test price]" in md
    assert "36.89% [사실 `105560.2026H1.CFS.operating_income`" in md                    # KB's Q2 cost-to-income ratio
    for sentence in re.split(r"(?<=[.!?])\s+", md):
        if any(w in sentence for w in RATING_WORDS_KO):
            assert "않" in sentence or "없" in sentence, sentence                        # only to say there is none


def test_valuation_ko_needs_a_sourced_price_and_a_known_group(tmp_path):
    data = _priced(tmp_path / "a")
    p = data / "316140.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["price_sources"].pop(PRICE_DATE)                          # a price without its source is not used
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValuationError, match="--price"):
        snapshot_ko("105560", data_dir=data)
    with pytest.raises(ValuationError, match="unknown group"):
        snapshot_ko("RY", data_dir=_priced(tmp_path / "b"))
    with pytest.raises(ValuationError, match="filed by"):
        snapshot_ko("우리금융", as_of="2026-09-30", data_dir=_priced(tmp_path / "c"))  # Shinhan's amended reports


def test_valuation_cli_kr_is_off_without_prices(tmp_path, capsys):
    out = tmp_path / "kb.md"
    assert finance_main(["valuation", "--market", "kr", "--bank", "KB금융", "--out", str(out)]) == 1
    assert not out.exists() and "ships no prices" in capsys.readouterr().err
    assert prices_ready(_priced(tmp_path))
