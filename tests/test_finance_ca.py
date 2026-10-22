"""Canadian desk: filing sources (EDGAR, local documents, DDS stub), the Big 5 dataset, the c01-c03 tasks and the
sector note. No test touches the network: EDGAR responses are served from in-memory fixtures."""

import copy
import hashlib
import itertools
import json
import statistics
from pathlib import Path

import pytest

from agentworld_lite.agents import BaseAgent, Decision
from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.catalog import BIG5, SPECS_CA, build_task
from agentworld_lite.finance.dataset import Dataset, fmt_value
from agentworld_lite.finance.env import build_env, evaluate
from agentworld_lite.finance.note import NoteError, fmt_figure, sector_note, uncited_figures
from agentworld_lite.finance.run import make_factory, run_episode, validate_task
from agentworld_lite.finance.sources import SourceError, SourceUnavailable, get_source, parse_periods
from agentworld_lite.finance.sources import edgar
from agentworld_lite.finance.sources.edgar import (EdgarClient, EdgarSource, extract_period, parse_instance,
                                                   period_end_date)
from agentworld_lite.finance.sources.local_pdf import LocalPdfSource, check_citation
from agentworld_lite.finance.tools import SYSTEM_PROMPT_CA
from agentworld_lite.task import Task, load_tasks

ROOT = Path(__file__).resolve().parents[1]
CA_DIR = ROOT / "tasks" / "finance_ca"
BANKS = ROOT / "data" / "finance" / "edgar"


# ---------------------------------------------------------------------- tasks
def test_committed_ca_tasks_match_the_catalog():
    for spec in SPECS_CA:
        for placement in ("shared", "private"):
            built = Task.from_dict(build_task(spec, placement))
            on_disk = Task.load(CA_DIR / f"{built.id}.yaml")
            assert on_disk.to_dict() == built.to_dict(), f"{built.id}: run `python -m agentworld_lite.finance make-tasks`"


def test_ca_reference_solves_and_idle_team_fails():
    tasks = load_tasks(str(CA_DIR))
    assert len(tasks) == 2 * len(SPECS_CA) == 6
    for t in tasks:
        assert validate_task(t) == [], t.id
    c03 = Task.load(CA_DIR / "c03_big5_q3_sector_private.yaml")
    assert run_episode(c03, make_factory("scripted"))["result"]["rounds_used"] == 18 <= c03.max_rounds


def test_ca_pairs_differ_only_in_where_the_sentences_are():
    for spec in SPECS_CA:
        shared, private = build_task(spec, "shared"), build_task(spec, "private")
        moved = [s for owner in spec["facts"].values() for s in owner]
        assert all(s in shared["relevant_game_context"] for s in moved)
        assert not any(s in private["relevant_game_context"] for s in moved)
        strip = lambda d: {k: v for k, v in d.items() if k not in ("id", "relevant_game_context", "matched", "agents")}
        assert strip(shared) == strip(private)


def _bank(code):
    return json.loads((BANKS / f"{code}.json").read_text(encoding="utf-8"))["statements"]


def _off(got, cp):
    return abs(got - cp["expected"]) > cp["rel_tol"] * abs(cp["expected"])


def test_c01_common_mistakes_are_wrong_answers():
    ry = _bank("RY")
    fy, prev, cur = ry["2025:FY:CFS"], ry["2025:Q3:CFS"], ry["2026:Q3:CFS"]
    ni_cp, pe_cp = SPECS_CA[0]["checkpoints"]
    price = 293.41
    assert not _off(fy["net_income_common"] - prev["net_income_common"] + cur["net_income_common"], ni_cp)
    assert _off(fy["net_income_owners"] - prev["net_income_owners"] + cur["net_income_owners"], ni_cp)  # incl. preferred
    assert _off(fy["net_income"] - prev["net_income"] + cur["net_income"], ni_cp)                    # incl. NCI
    assert _off(cur["net_income_common"], ni_cp)                                                      # 9 months only
    ttm = lambda k: fy[k] - prev[k] + cur[k]
    assert not _off(price / ttm("eps_diluted"), pe_cp)
    assert _off(price / ttm("eps_basic"), pe_cp)                    # basic instead of diluted
    assert _off(price / cur["eps_diluted"], pe_cp)                  # year-to-date EPS as if annual
    assert _off(price / fy["eps_diluted"], pe_cp)                   # stale fiscal-year EPS
    q3 = cur["eps_diluted"] - ry["2026:H1:CFS"]["eps_diluted"]
    assert _off(price / (4 * q3), pe_cp)                            # one quarter annualized
    assert _off(177.79 / ttm("eps_diluted"), pe_cp)                 # a year-old price


def test_c02_common_mistakes_are_wrong_answers():
    for code, cp in zip(("RY", "TD"), SPECS_CA[1]["checkpoints"]):
        st = _bank(code)
        h1, q3 = st["2026:H1:CFS"], st["2026:Q3:CFS"]
        right = (q3["non_interest_expense"] - h1["non_interest_expense"]) / (q3["revenue"] - h1["revenue"]) * 100
        assert not _off(right, cp)
        assert _off(q3["non_interest_expense"] / q3["revenue"] * 100, cp)   # 9-month ratio
        assert _off(h1["non_interest_expense"] / h1["revenue"] * 100, cp)   # previous year-to-date
        assert _off(right / 100, cp)                                        # fraction, not percent
    ry, td = SPECS_CA[1]["checkpoints"]
    assert _off(td["expected"], ry)                                          # banks swapped


def test_c03_common_mistakes_are_wrong_answers():
    cps = {cp["field"]: cp for cp in SPECS_CA[2]["checkpoints"]}
    for code in BIG5:
        st = _bank(code)
        q1, h1, q3 = st["2026:Q1:CFS"], st["2026:H1:CFS"], st["2026:Q3:CFS"]
        cp = cps[f"{code.lower()}_q3_net_income_common"]
        q = lambda k: q3[k] - h1[k]
        assert not _off(q("net_income_common"), cp)
        assert _off(q3["net_income_common"], cp)                                    # nine months
        assert _off(h1["net_income_common"], cp)                                    # six months
        assert _off(q("net_income"), cp)                                            # incl. NCI and preferred
        if "net_income_owners" in q3:                                               # TD does not tag it
            assert _off(q("net_income_owners"), cp)                                 # incl. preferred
        assert _off(q3["net_income_common"] / 3, cp)                                # average quarter
        assert _off(h1["net_income_common"] - q1["net_income_common"], cp)          # fiscal Q2 alone
        fq4 = st["2025:FY:CFS"]["net_income_common"] - st["2025:Q3:CFS"]["net_income_common"]
        assert _off(fq4, cp)                                                        # fiscal Q4 2025
        assert _off(q("net_income_common") / 1e6, cp)                               # millions, not CAD
    for a, b in itertools.permutations([f"{c.lower()}_q3_net_income_common" for c in BIG5], 2):
        assert _off(cps[a]["expected"], cps[b])                                     # banks swapped
    cet1 = lambda key: [_bank(c)[key]["cet1_ratio"] for c in BIG5]
    spread = lambda v: max(v) - min(v)
    avg_cp, spread_cp = cps["cet1_average"], cps["cet1_spread_pp"]
    now = cet1("2026:Q3:CFS")
    assert not _off(statistics.mean(now), avg_cp) and not _off(spread(now), spread_cp)
    for key in ("2026:H1:CFS", "2026:Q1:CFS", "2025:FY:CFS", "2025:Q3:CFS"):        # wrong period
        assert _off(statistics.mean(cet1(key)), avg_cp)
    for key in ("2026:Q1:CFS", "2025:FY:CFS", "2025:Q3:CFS"):
        assert _off(spread(cet1(key)), spread_cp)
    assert not _off(spread(cet1("2026:H1:CFS")), spread_cp)  # TD and BMO unchanged: the average catches H1 instead
    assert _off(statistics.median(now), avg_cp)                                     # median, not mean
    assert _off(statistics.mean([v for c, v in zip(BIG5, now) if c != "TD"]), avg_cp)  # a bank left out
    assert _off(statistics.mean(now) / 100, avg_cp) and _off(spread(now) / 100, spread_cp)  # fraction
    assert _off(spread(now) * 100, spread_cp)                                       # basis points
    assert _off((max(now) / min(now) - 1) * 100, spread_cp)                         # relative spread
    assert _off(max(now) - statistics.mean(now), spread_cp)                         # highest minus average


def test_ca_desk_uses_the_canadian_prompt_and_drops_share_counts():
    seen = []

    class Probe(BaseAgent):
        def act(self, turn):
            seen.append((turn.system_prompt, list(turn.allowed_tools), [s["name"] for s in turn.tool_specs(turn.allowed_tools)]))
            return Decision("wait", {"reason": "probe"})

    task = Task.load(CA_DIR / "c01_ry_ttm_pe_shared.yaml")
    traj = run_episode(task, lambda t, n: Probe(n))
    prompt, allowed, specs = seen[0]
    assert prompt == SYSTEM_PROMPT_CA and "fetch_shares" not in allowed and "fetch_shares" not in specs
    assert traj["market"] == "ca" and not traj["result"]["success"]


def test_episode_counts_each_model_call_once():
    class Billed(BaseAgent):
        def act(self, turn):
            return Decision("wait", {"reason": "x"}, usage={"calls": 2, "input_tokens": 10, "cost_usd": 0.5})

    task = Task.load(CA_DIR / "c01_ry_ttm_pe_shared.yaml")
    traj = run_episode(task, lambda t, n: Billed(n))
    turns = len(traj["actions"])
    assert traj["usage"]["calls"] == 2 * turns and traj["usage"]["input_tokens"] == 10 * turns
    assert abs(traj["usage"]["cost_usd"] - 0.5 * turns) < 1e-9


def test_ca_fetch_reports_source_fiscal_periods_and_as_of():
    env = build_env(Task.load(CA_DIR / "c01_ry_ttm_pe_shared.yaml"))
    ok = env.execute("collector", "fetch_financials", {"company": "RBC", "year": 2026, "period": "Q3", "basis": "CFS"})
    assert ok.ok and "EDGAR 6-K 0001193125-26-369461" in ok.message and "C$12.10 per share" in ok.message
    fact = env.facts["RY.2026Q3.CFS.net_income_common"]
    assert fact.unit == "CAD" and fact.meta["filed"] == "2026-08-27" and "9 months to 2026-07-31" in fact.label
    assert fact.meta["concept"] == "ifrs-full:ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity"
    q2 = env.execute("collector", "fetch_financials", {"company": "RY", "year": 2026, "period": "Q2", "basis": "CFS"})
    assert not q2.ok and "fiscal year-to-date" in q2.message
    env.as_of = "2026-08-26"  # the day before the Q3 6-K
    late = env.execute("collector", "fetch_financials", {"company": "RY", "year": 2026, "period": "Q3", "basis": "CFS"})
    assert not late.ok and "after the as-of date" in late.message


def test_bank_dataset_names_aliases_and_formatting():
    ds = Dataset(BANKS)
    assert {c.stock_code for c in ds.companies.values()} == {"RY", "TD", "BNS", "BMO", "CM"}
    assert ds.find("CIBC").stock_code == "CM" and ds.find("scotiabank").stock_code == "BNS"
    assert ds.find("royal bank of canada").stock_code == "RY" and ds.lang == "en" and ds.market == "ca"
    assert fmt_value(18.5, "x", "en") == "18.50x" and fmt_value(18.5, "x") == "18.50배"
    assert fmt_value(5_879e6, "CAD", "en").startswith("C$5,879.0 million")


def test_every_bank_tags_total_assets_and_rwa_in_every_period():
    for code in BIG5:
        for key, st in _bank(code).items():
            assert st["concepts"]["total_assets"] == "ifrs-full:Assets", (code, key)
            assert 0.25 < st["rwa"] / st["total_assets"] < 0.40, (code, key)   # RWA density of a Canadian bank
            assert st["cet1_ratio"] / 100 * st["rwa"] > 30e9, (code, key)        # implied CET1 capital, C$ billions


# ---------------------------------------------------------------------- sector note
def _reference(task_id, mutate=None):
    task = Task.load(CA_DIR / f"{task_id}.yaml")
    if mutate:
        d = copy.deepcopy(task.to_dict())
        mutate(d)
        task = Task.from_dict(d)
    return task, run_episode(task, make_factory("scripted"), meta={"agent": "scripted", "model": "scripted"})


def test_sector_note_cites_every_figure_with_its_filing():
    task, traj = _reference("c03_big5_q3_sector_shared")
    note = sector_note(traj, task)
    assert uncited_figures(note) == []
    assert sector_note(traj, task) == note                                     # deterministic
    ds = Dataset(BANKS)
    for code in BIG5:
        q3 = ds.companies[code].statement(2026, "Q3", "CFS")
        h1 = ds.companies[code].statement(2026, "H1", "CFS")
        for fid, value, doc in ((f"{code}.2026Q3.CFS.net_income_common", q3["net_income_common"], q3["doc_id"]),
                                (f"{code}.2026H1.CFS.net_income_common", h1["net_income_common"], h1["doc_id"]),
                                (f"{code}.2026Q3.CFS.cet1_ratio", q3["cet1_ratio"], q3["doc_id"])):
            unit = "%" if fid.endswith("cet1_ratio") else "CAD"
            assert f"{fmt_figure(value, unit)} [fact `{fid}` from 6-K {doc}]" in note
        single = fmt_figure(q3["net_income_common"] - h1["net_income_common"], "CAD")
        assert (f"{single} [fact `calc.{code.lower()}_q3_net_income_common` from 6-K {q3['doc_id']}; "
                f"6-K {h1['doc_id']}]") in note
    assert "13.46% [fact `calc.cet1_average`" in note and "1.30 pp [fact `calc.cet1_spread_pp`" in note
    table = note.split("## Comparison")[1].split("##")[0]
    order = [table.index(f"({c})") for c in ("RY", "TD", "BNS", "CM", "BMO")]
    assert order == sorted(order)                                              # highest first
    assert "every answer is within the task's tolerance" in note and note.count("(within tolerance)") == 7


def test_uncited_figures_flags_bare_numbers_only():
    ok = ("Fiscal 2026 Q3 (May-July 2026), as of 2026-09-30, for the Big 5 banks. "
          "C$5,879 million [fact `calc.x` from 6-K 0001193125-26-369461] and **13.46%** [fact `calc.y` from 6-K 1]")
    assert uncited_figures(ok) == []
    bad = "RBC earned C$5,879 million; CET1 averaged 13.46%, a spread of 1.30 pp, at 18.50x and 1,667 in total."
    assert uncited_figures(bad) == ["C$5,879 million", "13.46%", "1.30 pp", "18.50x", "1,667"]
    assert uncited_figures("13.46% [fact `calc.y` from 6-K 1] then 2.5 [fact `calc.z` from 6-K 2] and 0.4") == ["0.4"]


class _Wait(BaseAgent):
    def act(self, turn):
        return Decision("wait", {"reason": "idle"})


def test_sector_note_refuses_wrong_or_tampered_trajectories():
    def swap(d):  # the reporter files RBC's quarter as TD's
        d["reference_solution"]["reporter"][-1][1]["answers"][1]["fact_id"] = "calc.ry_q3_net_income_common"
    task, traj = _reference("c03_big5_q3_sector_shared", swap)
    assert not traj["result"]["success"]
    with pytest.raises(NoteError, match="6 of 7"):
        sector_note(traj, task)
    note = sector_note(traj, task, require_success=False)
    assert "### `td_q3_net_income_common` (outside tolerance)" in note and uncited_figures(note) == []
    task, traj = _reference("c03_big5_q3_sector_shared")
    tampered = copy.deepcopy(traj)
    tampered["submission"]["cet1_spread_pp"]["value"] = 1.5
    with pytest.raises(NoteError, match="recorded"):
        sector_note(tampered, task)
    idle = run_episode(task, lambda t, n: _Wait(n))
    with pytest.raises(NoteError, match="no answers"):
        sector_note(idle, task, require_success=False)
    with pytest.raises(NoteError, match="not c01"):
        sector_note(traj, Task.load(CA_DIR / "c01_ry_ttm_pe_shared.yaml"))


def test_note_cli_from_reference_and_from_trajectory(tmp_path, capsys):
    out = tmp_path / "c03.md"
    assert finance_main(["note", "--reference", "c03_big5_q3_sector_private", "--out", str(out)]) == 0
    raw = out.read_bytes()
    assert b"\r\n" not in raw and uncited_figures(raw.decode("utf-8")) == []
    _, traj = _reference("c01_ry_ttm_pe_shared")
    path = tmp_path / "traj.json"
    path.write_text(json.dumps(traj), encoding="utf-8")
    capsys.readouterr()
    assert finance_main(["note", "--trajectory", str(path)]) == 0
    printed = capsys.readouterr().out
    assert "18.50x [fact `calc.pe` from" in printed and "40-F 0001193125-25-305927" in printed
    traj["submission"]["pe"]["value"] = 20.0
    path.write_text(json.dumps(traj), encoding="utf-8")
    assert finance_main(["note", "--trajectory", str(path)]) == 1


# ---------------------------------------------------------------------- EDGAR parsing (offline fixtures)
NS = ('xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
      'xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full" xmlns:dei="http://xbrl.sec.gov/dei/2024" '
      'xmlns:bank="http://www.example-bank.ca/20260731" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"')


def _ctx(cid, start=None, end=None, instant=None, member=None):
    seg = (f'<xbrli:segment><xbrldi:explicitMember dimension="ifrs-full:SegmentsAxis">{member}</xbrldi:explicitMember>'
           f'</xbrli:segment>') if member else ""
    period = (f"<xbrli:instant>{instant}</xbrli:instant>" if instant
              else f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>")
    return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>'
            f'{seg}</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>')


def instance(focus, start, end, values):
    """values: revenue_ytd, revenue_3m, nie_ytd, eps_ytd, cet1, equity."""
    body = "".join([
        _ctx("ytd", start, end), _ctx("q", "2026-05-01", end), _ctx("end", instant=end),
        _ctx("seg", start, end, member="bank:PersonalBankingMember"),
        '<xbrli:unit id="CAD"><xbrli:measure>iso4217:CAD</xbrli:measure></xbrli:unit>',
        '<xbrli:unit id="CADps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:CAD</xbrli:measure>'
        '</xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure>'
        '</xbrli:unitDenominator></xbrli:divide></xbrli:unit>',
        '<xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>',
        f'<dei:DocumentFiscalPeriodFocus contextRef="ytd">{focus}</dei:DocumentFiscalPeriodFocus>',
        f'<dei:DocumentPeriodEndDate contextRef="ytd">{end}</dei:DocumentPeriodEndDate>',
        f'<ifrs-full:Revenue contextRef="ytd" unitRef="CAD" decimals="-6">{values[0]}</ifrs-full:Revenue>',
        f'<ifrs-full:Revenue contextRef="q" unitRef="CAD" decimals="-6">{values[1]}</ifrs-full:Revenue>',
        '<ifrs-full:Revenue contextRef="seg" unitRef="CAD" decimals="-6">1</ifrs-full:Revenue>',
        f'<bank:NonInterestExpense1 contextRef="ytd" unitRef="CAD" decimals="-6">{values[2]}</bank:NonInterestExpense1>',
        f'<ifrs-full:DilutedEarningsLossPerShare contextRef="ytd" unitRef="CADps" decimals="2">{values[3]}'
        '</ifrs-full:DilutedEarningsLossPerShare>',
        f'<bank:CommonEquityTierOneCapitalRatio1 contextRef="end" unitRef="pure" decimals="3">{values[4]}'
        '</bank:CommonEquityTierOneCapitalRatio1>',
        f'<ifrs-full:Equity contextRef="end" unitRef="CAD" decimals="-6">{values[5]}</ifrs-full:Equity>',
    ])
    return f'<?xml version="1.0" encoding="utf-8"?><xbrli:xbrl {NS}>{body}</xbrli:xbrl>'.encode()


def test_extract_period_uses_ytd_contexts_without_dimensions_and_extension_fallbacks():
    facts = parse_instance(instance("Q3", "2025-11-01", "2026-07-31", (900e6, 300e6, 500e6, 2.5, 0.131, 1e9)))
    values, used, currency = extract_period(facts, "2026-07-31", "Q3")
    assert values["revenue"] == 900e6 and values["non_interest_expense"] == 500e6  # not the 3-month or segment value
    assert used["non_interest_expense"] == "ext:NonInterestExpense1"
    assert values["eps_diluted"] == 2.5 and values["cet1_ratio"] == 13.1 and values["equity_total"] == 1e9
    assert "net_income" not in values and currency == "CAD"


def test_fiscal_period_end_dates():
    assert str(period_end_date("1031", 2026, "Q1")) == "2026-01-31"
    assert str(period_end_date("1031", 2026, "H1")) == "2026-04-30"
    assert str(period_end_date("1031", 2026, "Q3")) == "2026-07-31"
    assert str(period_end_date("1231", 2026, "H1")) == "2026-06-30"
    assert parse_periods(["2026:q3"]) == [(2026, "Q3")]
    with pytest.raises(SourceError):
        parse_periods(["2026:Q2"])


def _fake_edgar():
    acc_h1, acc_q3, acc_other = "0000000001-26-000010", "0000000001-26-000020", "0000000001-26-000019"
    recent = {"form": ["6-K", "6-K", "6-K", "424B2"], "filingDate": ["2026-05-28", "2026-08-20", "2026-08-27", "2026-08-28"],
              "reportDate": ["2026-04-30", "2026-07-31", "2026-07-31", ""], "isXBRL": [1, 1, 1, 0],
              "accessionNumber": [acc_h1, acc_other, acc_q3, "0000000001-26-000030"]}
    urls = {
        edgar.TICKERS_URL: json.dumps({"0": {"cik_str": 1, "ticker": "XB", "title": "EXAMPLE BANK /CAN/"}}).encode(),
        edgar.SUBMISSIONS_URL.format(cik=1): json.dumps({"name": "EXAMPLE BANK /CAN/", "tickers": ["XB"],
                                                         "fiscalYearEnd": "1031",
                                                         "filings": {"recent": recent, "files": []}}).encode(),
    }
    for acc, focus, start, end, vals in ((acc_h1, "Q2", "2025-11-01", "2026-04-30", (600e6, 300e6, 330e6, 1.6, 0.13, 9e8)),
                                         (acc_q3, "Q3", "2025-11-01", "2026-07-31", (900e6, 300e6, 500e6, 2.5, 0.131, 1e9))):
        a = acc.replace("-", "")
        urls[edgar.ARCHIVE_URL.format(cik=1, acc=a, name="index.json")] = json.dumps(
            {"directory": {"item": [{"name": "FilingSummary.xml"}, {"name": "xb-6k_htm.xml"}]}}).encode()
        urls[edgar.ARCHIVE_URL.format(cik=1, acc=a, name="xb-6k_htm.xml")] = instance(focus, start, end, vals)
    # an earlier XBRL 6-K with the same period end that is not the quarterly report (no fiscal-period focus)
    a = acc_other.replace("-", "")
    urls[edgar.ARCHIVE_URL.format(cik=1, acc=a, name="index.json")] = json.dumps(
        {"directory": {"item": [{"name": "other_htm.xml"}]}}).encode()
    urls[edgar.ARCHIVE_URL.format(cik=1, acc=a, name="other_htm.xml")] = instance("", "2025-11-01", "2026-07-31",
                                                                                  (1, 1, 1, 1, 0.1, 1))
    calls = []

    def fetch(url):
        calls.append(url)
        return urls[url]
    return fetch, calls


def test_edgar_source_builds_a_loadable_dataset_offline(tmp_path):
    fetch, calls = _fake_edgar()
    src = EdgarSource(EdgarClient(cache_dir=tmp_path / "cache", fetch=fetch), log=lambda m: None)
    path = src.build_company("XB", ["2026:H1", "2026:Q3"], out_dir=tmp_path / "out", exchange="TSX",
                             prices={"2026-07-31": 50.0}, aliases=["Example"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "Example Bank" and data["fiscal_year_end"] == "10-31" and data["currency"] == "CAD"
    q3 = data["statements"]["2026:Q3:CFS"]
    assert q3["doc_id"] == "0000000001-26-000020" and q3["filed"] == "2026-08-27"  # skipped the non-report 6-K
    assert data["statements"]["2026:H1:CFS"]["revenue"] == 600e6                    # Q2 focus = H1 period
    ds = Dataset(tmp_path / "out")
    company = ds.find("Example")
    assert company.source == "EDGAR" and company.price("2026-07-31") == 50.0
    n = len(calls)
    EdgarSource(EdgarClient(cache_dir=tmp_path / "cache", fetch=fetch), log=lambda m: None).build_company(
        "XB", ["2026:Q3"], out_dir=tmp_path / "out2")
    assert all("xb-6k_htm.xml" not in u for u in calls[n:])  # parsed facts come from the cache


def test_edgar_client_needs_a_declared_contact(monkeypatch):
    monkeypatch.delenv(edgar.UA_ENV, raising=False)
    with pytest.raises(SourceError, match="SEC_USER_AGENT"):
        EdgarClient()
    with pytest.raises(SourceError):
        EdgarClient(user_agent="no contact here")
    assert EdgarClient(user_agent="Jane Doe jane@example.com").user_agent.endswith("example.com")
    with pytest.raises(SourceError, match="plain ASCII"):                  # HTTP headers cannot carry Hangul
        EdgarClient(user_agent="홍길동 gildong@example.com")


def test_edgar_filing_documents_are_listed_and_cached(tmp_path):
    calls = []
    index = {"directory": {"item": [{"name": "d95203dex2.htm", "type": "text.gif", "size": "17246847"}]}}

    def fetch(url):
        calls.append(url)
        return json.dumps(index).encode() if url.endswith("index.json") else b"<html>MD&A</html>"

    client = EdgarClient(cache_dir=tmp_path, fetch=fetch)
    for _ in range(2):
        assert client.documents(1000275, "0001193125-25-305927")[0]["name"] == "d95203dex2.htm"
        assert client.document(1000275, "0001193125-25-305927", "d95203dex2.htm") == b"<html>MD&A</html>"
    assert calls == ["https://www.sec.gov/Archives/edgar/data/1000275/000119312525305927/index.json",
                     "https://www.sec.gov/Archives/edgar/data/1000275/000119312525305927/d95203dex2.htm"]
    assert (tmp_path / "docs" / "000119312525305927" / "d95203dex2.htm").exists()


# ---------------------------------------------------------------------- local documents and the DDS stub
def _manifest(tmp_path, text_value="1,234.5", value=1_234_500_000, page=2, sha=None):
    doc = tmp_path / "report.txt"
    doc.write_text("Cover page\fConsolidated statement of income\nTotal revenue 1,234.5\nNet income 210.0\f"
                   "Share price close $41.25", encoding="utf-8")
    m = tmp_path / "manifest.yaml"
    sha_line = f"\n    sha256: {sha}" if sha else ""
    m.write_text(f"""company: {{name: Example Corp, code: EXM, currency: CAD, fiscal_year_end: "12-31"}}
documents:
  q2:
    path: report.txt
    filed: "2026-08-05"
    title: Q2 2026 interim report{sha_line}
statements:
  "2026:H1:CFS":
    document: q2
    period_end: "2026-06-30"
    accounts:
      revenue: {{value: {value}, page: {page}, text: "{text_value}"}}
      net_income: {{value: 210000000, page: 2, text: "210.0"}}
prices:
  "2026-06-30": {{value: 41.25, document: q2, page: 3, text: "$41.25"}}
""", encoding="utf-8")
    return m, doc


def test_local_pdf_accepts_cited_figures(tmp_path):
    m, doc = _manifest(tmp_path)
    path = LocalPdfSource(m, log=lambda s: None).build_company(out_dir=tmp_path / "out")
    data = json.loads(path.read_text(encoding="utf-8"))
    st = data["statements"]["2026:H1:CFS"]
    assert st["revenue"] == 1_234_500_000 and st["pages"]["revenue"] == 2 and st["filed"] == "2026-08-05"
    assert data["documents"]["q2"]["sha256"] == hashlib.sha256(doc.read_bytes()).hexdigest()
    assert data["prices"]["2026-06-30"] == 41.25
    company = Dataset(tmp_path / "out").find("EXM")
    assert company.source == "local documents"


@pytest.mark.parametrize("kw, match", [
    ({"page": 1}, "does not appear on page 1"),
    ({"value": 1_234_000_000}, "not the printed"),
    ({"text_value": "9,999.9"}, "does not appear"),
    ({"page": 9}, "outside the document"),
    ({"sha": "0" * 64}, "file changed"),
])
def test_local_pdf_rejects_bad_citations(tmp_path, kw, match):
    m, _ = _manifest(tmp_path, **kw)
    with pytest.raises(SourceError, match=match):
        LocalPdfSource(m, log=lambda s: None).build_company(out_dir=tmp_path / "out")


def test_check_citation_handles_parentheses_negatives():
    check_citation(["Net loss (12.5)"], {"page": 1, "text": "(12.5)", "value": -12_500_000}, "loss")


def test_sedar_dds_is_an_explicit_placeholder_and_registry_knows_all_sources():
    with pytest.raises(SourceUnavailable, match="Data Distribution Services"):
        get_source("sedar_dds").build_company("RY", ["2026:Q3"])
    assert get_source("local_pdf").name == "local_pdf"
    with pytest.raises(SourceError):
        get_source("sedar_crawler")
