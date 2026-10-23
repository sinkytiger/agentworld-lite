"""Canadian model leaderboard (leaderboard.py --market ca, traps_ca.py), offline. Trajectories come from the scripted
references of c03 and c04 with mistakes edited into them; runs/ is not needed."""

import copy
import json
from pathlib import Path

from agentworld_lite.finance import leaderboard as lb
from agentworld_lite.finance import traps_ca as tc
from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.catalog import SPECS_CA
from agentworld_lite.finance.run import make_factory, run_episode
from agentworld_lite.task import Task

ROOT = Path(__file__).resolve().parents[1]
C03 = Task.load(ROOT / "tasks" / "finance_ca" / "c03_big5_q3_sector_shared.yaml")
C04 = Task.load(ROOT / "tasks" / "finance_ca" / "c04_big5_roe_objectives_private.yaml")


def _classify(traj, task):
    return lb.classify(traj, task, tc.match_trap, "en")


def _run(task, edit=None, setting="full"):
    task = copy.deepcopy(task)
    if edit:
        edit(task.reference_solution)
    return run_episode(task, make_factory("scripted"), setting=setting)


def _calc(ref, name):
    return next(s for s in ref["analyst"] if s[0] == "calculate" and s[1]["name"] == name)


def test_every_listed_mistake_misses_its_answer():
    for spec in SPECS_CA:
        cps = {cp["field"]: cp for cp in spec["checkpoints"]}
        traps = tc.trap_values(spec["pair"])
        assert set(traps) == set(cps), spec["pair"]
        for fld, mistakes in traps.items():
            assert len(mistakes) >= 4, (spec["pair"], fld)
            for label, v in mistakes:
                assert abs(v - cps[fld]["expected"]) > cps[fld]["rel_tol"] * abs(cps[fld]["expected"]), (fld, label)
    assert tc.match_trap("c04", "td_roe_gap_pp", 16.0 - 17.8) == "the other basis (reported vs adjusted)"
    assert tc.match_trap("c03", "cet1_spread_pp", 1.3) is None                     # the right answer


def test_reference_runs_are_all_right_with_no_trap_on_the_way():
    for task in (C03, C04):
        outcomes, detail, hits = _classify(_run(task), task)
        assert set(outcomes.values()) == {"정답"} and detail == {} and hits == []


def test_wrong_answers_are_named_by_their_mistake_in_english():
    def edit(ref):
        _calc(ref, "ry_q3_net_income_common")[1]["formula"] = "q3"                    # nine months as the quarter
        _calc(ref, "cet1_spread_pp")[1]["formula"] = "(td - bmo) * 100"               # basis points
    outcomes, detail, hits = _classify(_run(C03, edit), C03)
    assert outcomes["ry_q3_net_income_common"] == "함정: nine months"
    assert outcomes["cet1_spread_pp"] == "함정: basis points"
    assert detail["cet1_spread_pp"] == "submitted 130 (right 1.3)"
    assert lb._cat_en(outcomes["cet1_spread_pp"]) == "trap: basis points"

    def reported(ref):                                                                 # TD measured on reported ROE
        for i in _calc(ref, "td_roe_gap_pp")[1]["inputs"]:
            if i["var"] == "r":
                i["fact_id"] = "TD.fy2025.roe_reported"
    outcomes, _, _ = _classify(_run(C04, reported), C04)
    assert outcomes["td_roe_gap_pp"] == "함정: the other basis (reported vs adjusted)"


def test_missing_inputs_are_described_in_english():
    def drop(ref):
        share = next(s for s in ref["collector"] if s[0] == "share_facts")
        share[1]["fact_ids"].remove("BMO.2026H1.CFS.net_income_common")
    outcomes, detail, _ = _classify(_run(C03, drop), C03)
    assert outcomes["bmo_q3_net_income_common"] == "입력 부족"
    assert detail["bmo_q3_net_income_common"].startswith("1 of the 2 figures needed never reached them")


def test_canadian_leaderboard_from_run_directories(tmp_path):
    def write(name, model, trajs, cost):
        d = tmp_path / name
        (d / "trajectories").mkdir(parents=True)
        for i, t in enumerate(trajs):
            t = dict(t, usage={"cost_usd": cost}, seed=7 + i)
            (d / "trajectories" / f"{t['task_id']}__{t['setting']}__s{t['seed']}.json").write_text(
                json.dumps(t), encoding="utf-8")
        (d / "run_config.json").write_text(json.dumps({"model": model, "provider": "test", "tasks": [C03.id],
                                                       "settings": ["full", "no_comm"], "seeds": [7]}), encoding="utf-8")
    write("finance_ca_good", "model-a", [_run(C03), _run(C03, setting="no_comm")], 0.5)
    write("finance_ca_bad", "model-b", [_run(C03, lambda ref: _calc(ref, "cet1_spread_pp")[1].update(
        formula="(td - bmo) * 100"))], 0.1)
    (tmp_path / "finance_kr_other").mkdir()                                            # the Korean desk: ignored
    out = tmp_path / "lb.md"
    assert finance_main(["leaderboard", "--market", "ca", "--runs-root", str(tmp_path), "--out", str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    assert md.startswith("# Canadian desk: model comparison\n")
    assert "| model-a | 2 | 2 (100%) | 100% | 7, 8 |" in md and md.index("| model-a |") < md.index("| model-b |")
    assert "| model-a | not run | not run | 2/2 | not run |" in md
    assert "| model-b | c03_big5_q3_sector_shared full s7 | 30/30 | cet1_spread_pp | trap: basis points |" in md
    assert "c01, c02, c04: not run with any model." in md
    assert not any("가" <= ch <= "힣" for ch in md)                           # no Hangul in the report
