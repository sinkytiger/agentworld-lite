import io
import json
import urllib.error
from pathlib import Path

import pytest

from agentworld_lite.agents import Decision
from agentworld_lite.cce import RuleJudge
from agentworld_lite.failures import _state_at
from agentworld_lite.finance import dart
from agentworld_lite.finance.catalog import SPECS, build_task
from agentworld_lite.finance.env import TOOL_NAMES, build_env, evaluate, safe_eval
from agentworld_lite.finance.run import make_factory, run_episode, validate_task
from agentworld_lite.local_llm import OllamaAgent, parse_text_tool_call
from agentworld_lite.task import Task, load_tasks

from test_local_llm import FakeOllama

FIN_DIR = Path(__file__).resolve().parents[1] / "tasks" / "finance"
C = "999990"


def load(task_id):
    return Task.load(FIN_DIR / f"{task_id}.yaml")


def test_committed_tasks_match_the_catalog():
    for spec in SPECS:
        for placement in ("shared", "private"):
            built = Task.from_dict(build_task(spec, placement))
            on_disk = load(built.id)
            assert on_disk.to_dict() == built.to_dict(), f"{built.id}: run `python -m agentworld_lite.finance make-tasks`"


def test_reference_solves_and_idle_team_fails():
    for t in load_tasks(str(FIN_DIR)):
        assert validate_task(t) == [], t.id


def test_pairs_differ_only_in_where_the_sentences_are():
    for spec in SPECS:
        shared, private = build_task(spec, "shared"), build_task(spec, "private")
        moved = [s for owner in spec["facts"].values() for s in owner]
        assert all(s in shared["relevant_game_context"] for s in moved)
        assert not any(s in private["relevant_game_context"] for s in moved)
        briefs = " ".join(a.get("role_brief", "") for a in private["agents"])
        assert all(s in briefs for s in moved)
        strip = lambda d: {k: v for k, v in d.items() if k not in ("id", "relevant_game_context", "matched", "agents")}
        assert strip(shared) == strip(private)
        assert [{k: v for k, v in a.items() if k != "role_brief"} for a in private["agents"]] == shared["agents"]


def act(env, agent, tool, **args):
    return env.execute(agent, tool, args)


def test_roles_notebooks_and_sharing():
    env = build_env(load("f01_ttm_per_shared"))
    assert not act(env, "analyst", "fetch_financials", company=C, year=2025, period="H1", basis="CFS").ok
    r = act(env, "collector", "fetch_financials", company="샘플전자", year=2025, period="H1", basis="CFS")
    assert r.ok and r.effects["inv"]["collector"][f"{C}.2025H1.CFS.net_income_owners"] == 1
    fid = f"{C}.2025H1.CFS.net_income_owners"
    bad = act(env, "analyst", "calculate", name="x", formula="a * 2", unit="KRW", inputs=[{"var": "a", "fact_id": fid}])
    assert not bad.ok and "not in your notebook" in bad.message
    assert act(env, "collector", "share_facts", to_agent="analyst", fact_ids=[fid]).ok
    ok = act(env, "analyst", "calculate", name="x", formula="a * 2", unit="KRW", inputs=[{"var": "a", "fact_id": fid}])
    assert ok.ok and env.facts["calc.x"].value == 52e9 and ok.effects["uses"] == {"analyst": [fid]}
    assert not act(env, "reporter", "submit_answer", answers=[{"field": "per", "fact_id": "calc.x"}]).ok
    assert not act(env, "collector", "fetch_financials", company="없는회사", year=2025, period="H1", basis="CFS").ok


def test_stringified_list_arguments_are_accepted():
    env = build_env(load("f02_q2_margin_shared"))
    act(env, "collector", "fetch_financials", company=C, year="2025", period="Q1", basis="CFS")
    a, b = f"{C}.2025Q1.CFS.revenue", f"{C}.2025Q1.CFS.operating_income"
    assert act(env, "collector", "share_facts", to_agent="analyst", fact_ids=json.dumps([a])).ok
    assert act(env, "collector", "share_facts", to_agent="analyst", fact_ids=f"{a}, {b}").ok
    r = act(env, "collector", "share_facts", to_agent="analyst", fact_ids="[]")
    assert not r.ok and "no ids given" in r.message
    r = act(env, "analyst", "calculate", name="m", formula="o / r * 100", unit="%",
            inputs=json.dumps([{"var": "o", "fact_id": b}, {"var": "r", "fact_id": a}]))
    assert r.ok and abs(env.facts["calc.m"].value - 16 / 210 * 100) < 1e-9
    r = act(env, "analyst", "calculate", name="m2", formula="o * 2", unit="KRW",
            inputs=str([{"var": "o", "fact_id": b}]))  # Python-style quotes
    assert r.ok and env.facts["calc.m2"].value == 32e9


def test_as_of_guard_blocks_look_ahead():
    env = build_env(load("f01_ttm_per_shared"))
    r = act(env, "collector", "fetch_financials", company=C, year=2025, period="Q3", basis="CFS")
    assert not r.ok and "after the as-of date" in r.message
    assert not env.agents["collector"].inventory
    r = act(env, "collector", "fetch_financials", company=C, year=2025, period="FY", basis="CFS")
    assert not r.ok and "2025 H1 (CFS/OFS)" in r.message and "Q3" not in r.message  # filing list stops at as-of


def test_calculated_fact_ids_never_change_value():
    env = build_env(load("f02_q2_margin_shared"))
    act(env, "collector", "fetch_financials", company=C, year=2025, period="H1", basis="CFS")
    act(env, "collector", "share_facts", to_agent="analyst", fact_ids=[f"{C}.2025H1.CFS.revenue"])
    inp = [{"var": "a", "fact_id": f"{C}.2025H1.CFS.revenue"}]
    act(env, "analyst", "calculate", name="half", formula="a / 2", unit="KRW", inputs=inp)
    again = act(env, "analyst", "calculate", name="half", formula="a / 2", unit="KRW", inputs=inp)
    assert again.ok and "inv" not in again.effects  # same value: reuses calc.half
    other = act(env, "analyst", "calculate", name="half", formula="a / 4", unit="KRW", inputs=inp)
    assert "calc.half_2" in other.message and env.facts["calc.half"].value == 220e9


@pytest.mark.parametrize("formula", ["__import__('os')", "a.real", "abs(a)", "a ** 2", "[a]"])
def test_safe_eval_rejects_anything_but_arithmetic(formula):
    with pytest.raises(ValueError):
        safe_eval(formula, {"a": 1.0})


def test_distractor_answers_are_wrong():
    """Each common mistake gives a PER outside the 0.5% tolerance."""
    task = load("f01_ttm_per_shared")
    cases = {
        "net income incl. non-controlling": ("CFS", "net_income", "fy - h1_prev + h1"),
        "separate statements": ("OFS", "net_income", "fy - h1_prev + h1"),
        "last fiscal year only": ("CFS", "net_income_owners", "fy"),
        "H1 annualized": ("CFS", "net_income_owners", "h1 * 2"),
    }
    for label, (basis, account, formula) in cases.items():
        env = build_env(task)
        ids = {}
        for key, (year, period) in {"fy": (2024, "FY"), "h1_prev": (2024, "H1"), "h1": (2025, "H1")}.items():
            act(env, "collector", "fetch_financials", company=C, year=year, period=period, basis=basis)
            ids[key] = f"{C}.{year}{period}.{basis}.{account}"
        act(env, "collector", "fetch_shares", company=C, year=2025, period="H1")
        act(env, "collector", "share_facts", to_agent="analyst", fact_ids=[*ids.values(), f"{C}.2025H1.shares_common"])
        act(env, "reporter", "share_facts", to_agent="analyst", fact_ids=[f"{C}.price.2025-09-30"])
        act(env, "analyst", "calculate", name="ni", formula=formula, unit="KRW",
            inputs=[{"var": k, "fact_id": v} for k, v in ids.items()])
        act(env, "analyst", "calculate", name="mcap", formula="p * s", unit="KRW",
            inputs=[{"var": "p", "fact_id": f"{C}.price.2025-09-30"}, {"var": "s", "fact_id": f"{C}.2025H1.shares_common"}])
        act(env, "analyst", "calculate", name="per", formula="m / n", unit="x",
            inputs=[{"var": "m", "fact_id": "calc.mcap"}, {"var": "n", "fact_id": "calc.ni"}])
        act(env, "analyst", "share_facts", to_agent="reporter", fact_ids=["calc.per", "calc.ni"])
        assert act(env, "reporter", "submit_answer", answers=[{"field": "per", "fact_id": "calc.per"},
                                                              {"field": "ttm_net_income", "fact_id": "calc.ni"}]).ok
        assert not evaluate(env, task.verifier)["success"], label


def test_rule_cce_follows_fact_provenance():
    traj = run_episode(load("f01_ttm_per_private"), make_factory("scripted"))
    res = RuleJudge().compute(traj)
    by_id = {a["id"]: a for a in traj["actions"]}
    assert res["success_ids"] == [traj["actions"][-1]["id"]]
    assert all(by_id[i]["tool"] != "wait" for i in res["contributing_ids"])
    assert res["n_contributing"] == sum(1 for a in traj["actions"] if a["tool"] != "wait")
    price_share = next(a["id"] for a in traj["actions"] if a["agent"] == "reporter" and a["tool"] == "share_facts")
    mcap = next(a["id"] for a in traj["actions"] if a["tool"] == "calculate" and a["args"]["name"] == "market_cap")
    assert [price_share, mcap] in res["edges"]
    assert len(res["edges"]) == len({tuple(e) for e in res["edges"]})
    assert "inv=" in _state_at(traj, 3, "analyst")  # failure taxonomy works without positions


def test_random_baseline_runs_and_fails():
    traj = run_episode(load("f02_q2_margin_shared"), make_factory("random", seed=8))
    assert not traj["result"]["success"] and traj["result"]["n_actions"] == 3 * 16


def test_no_comm_removes_chat_tool():
    seen = []

    def responder(payload):
        seen.append(payload)
        return {"message": {"role": "assistant", "content": "",
                            "tool_calls": [{"function": {"name": "send_chat", "arguments": {"to": "all", "message": "hi"}}}]}}

    task = load("f02_q2_margin_private")
    task.max_rounds = 1
    traj = run_episode(task, lambda t, n: OllamaAgent(n, FakeOllama(responder)), setting="no_comm")
    assert all(not a["ok"] for a in traj["actions"])
    assert "send_chat" not in [t["function"]["name"] for t in seen[0]["tools"]]


def test_ollama_agent_gets_finance_prompt_and_tools():
    seen = []

    def responder(payload):
        seen.append(payload)
        call = {"name": "fetch_financials", "arguments": {"company": C, "year": 2025, "period": "Q1", "basis": "CFS"}}
        return {"message": {"role": "assistant", "content": json.dumps(call)}}  # text-style tool call

    task = load("f02_q2_margin_shared")
    task.max_rounds = 1
    traj = run_episode(task, lambda t, n: OllamaAgent(n, FakeOllama(responder)))
    system = seen[0]["messages"][0]["content"]
    assert "research desk" in system and "MUST call exactly ONE tool" in system
    assert [t["function"]["name"] for t in seen[0]["tools"]] == list(TOOL_NAMES)
    assert "Your notebook" in seen[0]["messages"][1]["content"]
    first = traj["actions"][0]
    assert first["agent"] == "collector" and first["ok"] and first["note"] == "text_tool_call"
    assert not traj["actions"][1]["ok"]  # analyst's role cannot fetch
    assert parse_text_tool_call('{"name": "move_to", "arguments": {}}', TOOL_NAMES) is None


# ---------------------------------------------------------------------- DART normalization
def row(sj, account_id, name, amount, add=None, rcept="20250814000123"):
    return {"sj_div": sj, "account_id": account_id, "account_nm": name, "thstrm_amount": amount,
            "thstrm_add_amount": add, "rcept_no": rcept}


def test_dart_statement_normalization():
    rows = [
        row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업 소유주지분", "625,000,000,000"),
        row("BS", "ifrs-full_Equity", "자본총계", "656000000000"),
        row("CIS", "ifrs-full_Revenue", "매출액", "230000000000", "440000000000"),
        row("CIS", "-표준계정코드 미사용-", "영업이익", "19000000000", "35000000000"),
        row("CIS", "ifrs-full_ProfitLoss", "반기순이익", "15000000000", "27000000000"),
        row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", "14000000000", "26000000000"),
        row("CIS", "ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업 소유주지분", "1", "2"),
        row("CF", "ifrs-full_ProfitLoss", "반기순이익", "999", "999"),
    ]
    st = dart.normalize_statement(rows, "H1")
    assert st == {"filed": "2025-08-14", "rcept_no": "20250814000123", "revenue": 440e9, "operating_income": 35e9,
                  "net_income": 27e9, "net_income_owners": 26e9, "equity_owners": 625e9, "equity_total": 656e9}
    fy = dart.normalize_statement([row("IS", "ifrs-full_Revenue", "매출액", "800000000000", None, "20250318000001")], "FY")
    assert fy["revenue"] == 800e9 and fy["filed"] == "2025-03-18"
    q1 = dart.normalize_statement([row("IS", "ifrs-full_Revenue", "매출액", "210000000000", "")], "Q1")
    assert q1["revenue"] == 210e9
    assert dart.parse_amount("-") is None and dart.parse_amount("-1,200") == -1200


def test_dart_share_normalization():
    rows = [{"se": "보통주", "istc_totqy": "20,000,000", "tesstk_co": "500,000", "rcept_no": "20250814000123"},
            {"se": "우선주", "istc_totqy": "1,000,000", "tesstk_co": "-", "rcept_no": "20250814000123"},
            {"se": "합계", "istc_totqy": "21,000,000", "tesstk_co": "500,000", "rcept_no": "20250814000123"}]
    assert dart.normalize_shares(rows) == {"filed": "2025-08-14", "rcept_no": "20250814000123", "common": 20_000_000,
                                           "preferred": 1_000_000, "treasury_common": 500_000}


def test_dart_client_needs_a_key_and_never_leaks_it(monkeypatch):
    monkeypatch.delenv(dart.KEY_ENV, raising=False)
    with pytest.raises(dart.DartError, match="DART_API_KEY"):
        dart.DartClient()

    def fail(url, timeout):
        raise urllib.error.HTTPError(url, 500, "err", {}, io.BytesIO(b""))

    monkeypatch.setattr(dart.urllib.request, "urlopen", fail)
    client = dart.DartClient(api_key="SECRET-KEY-123", pause=0)
    with pytest.raises(dart.DartError) as exc:
        client.statement("00126380", 2025, "H1", "CFS")
    assert "SECRET" not in str(exc.value) and "SECRET" not in repr(exc.value.__cause__)

    replies = iter([{"status": "013", "message": "no data"}, {"status": "020", "message": "limit"}])
    monkeypatch.setattr(dart.DartClient, "_get", lambda self, ep, **p: json.dumps(next(replies)).encode())
    assert client.statement("00126380", 2025, "H1", "CFS") is None
    with pytest.raises(dart.DartError, match="daily request limit"):
        client.shares("00126380", 2025, "H1")


def test_fetch_company_writes_a_loadable_dataset(tmp_path, monkeypatch):
    from agentworld_lite.finance.dataset import Dataset, statement_facts
    client = dart.DartClient(api_key="k", cache_dir=tmp_path / "cache", pause=0)
    monkeypatch.setattr(client, "corp_codes", lambda refresh=False: [
        {"corp_code": "00000001", "corp_name": "테스트", "stock_code": "123450", "modify_date": ""}])
    monkeypatch.setattr(client, "statement", lambda cc, y, p, b: {"filed": "2025-08-14", "rcept_no": "x", "revenue": 1e9} if b == "CFS" else None)
    monkeypatch.setattr(client, "shares", lambda cc, y, p: {"filed": "2025-08-14", "rcept_no": "x", "common": 10,
                                                            "preferred": 0, "treasury_common": 0})
    path = dart.fetch_company(client, "123450", ["2025:H1"], prices={"2025-09-30": 1000.0}, out_dir=tmp_path, log=lambda s: None)
    ds = Dataset(tmp_path)
    co = ds.find("테스트")
    assert path.name == "123450.json" and co.price("2025-09-30") == 1000.0
    assert [f.id for f in statement_facts(co, 2025, "H1", "CFS")] == ["123450.2025H1.CFS.revenue"]
