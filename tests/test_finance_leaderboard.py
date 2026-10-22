"""모델 비교 리더보드와 함정 분류 (agentworld_lite/finance/leaderboard.py, traps_kr.py), offline. Trajectories come from
the scripted reference of k06 with mistakes edited into it, one per failure category; runs/ is not needed."""

import copy
import json
from pathlib import Path

import pytest

from agentworld_lite.finance import leaderboard as lb
from agentworld_lite.finance import traps_kr as tk
from agentworld_lite.finance.catalog import SPECS_KR_FHC
from agentworld_lite.finance.run import make_factory, run_episode
from agentworld_lite.task import Task

ROOT = Path(__file__).resolve().parents[1]
K06 = Task.load(ROOT / "tasks" / "finance_kr" / "k06_valueup_roe_gap_private.yaml")


def test_every_listed_mistake_misses_its_answer():
    for spec in SPECS_KR_FHC:
        cps = {cp["field"]: cp for cp in spec["checkpoints"]}
        traps = tk.trap_values(spec["pair"])
        assert set(traps) == set(cps), spec["pair"]
        for fld, mistakes in traps.items():
            assert len(mistakes) >= 4, (spec["pair"], fld)
            for label, v in mistakes:
                assert abs(v - cps[fld]["expected"]) > cps[fld]["rel_tol"] * abs(cps[fld]["expected"]), (fld, label)
    roe = dict(tk.trap_values("k06")["shinhan_ttm_roe"])
    assert tk.match_trap("k06", "shinhan_ttm_roe", roe["기말 자본만"] * 1.0004) == "기말 자본만"
    assert tk.match_trap("k06", "shinhan_ttm_roe", 9.168491) is None                 # the right answer


def test_reference_leaves_follow_the_calculations():
    assert lb.reference_leaves(K06, "hana_roe_gap_pp") == [
        "086790.valueup.roe_target", "086790.2025FY.CFS.net_income_owners", "086790.2025H1.CFS.net_income_owners",
        "086790.2026H1.CFS.net_income_owners", "086790.2025H1.CFS.equity_owners", "086790.2026H1.CFS.equity_owners"]


def _run(edit=None, setting="full"):
    task = copy.deepcopy(K06)
    if edit:
        edit(task.reference_solution)
    return run_episode(task, make_factory("scripted"), setting=setting)


def _step(ref, agent, tool, name=None):
    return next(s for s in ref[agent] if s[0] == tool and (name is None or s[1].get("name") == name))


def _drop(ref, fid):
    """The analyst stops sharing `fid` and the reporter neither waits for nor submits it."""
    share = _step(ref, "analyst", "share_facts")
    share[1]["fact_ids"].remove(fid)
    wait = _step(ref, "reporter", "wait")
    wait[2]["until_have"].pop(fid)
    sub = _step(ref, "reporter", "submit_answer")
    sub[1]["answers"] = [a for a in sub[1]["answers"] if a["fact_id"] != fid]


def test_reference_run_is_all_right_with_no_trap_on_the_way():
    outcomes, detail, hits = lb.classify(_run(), K06)
    assert set(outcomes.values()) == {"정답"} and detail == {} and hits == []


def test_wrong_answers_are_named_by_their_mistake():
    def edit(ref):
        _step(ref, "analyst", "calculate", "shinhan_ttm_roe")[1]["formula"] = "(fy - h1p + h1) / e1 * 100"
        _step(ref, "analyst", "calculate", "hana_ttm_roe")[1]["formula"] = "(fy - h1p + h1) / ((e0 + e1) / 2) * 90"
    outcomes, _, hits = lb.classify(_run(edit), K06)
    assert outcomes == {"shinhan_ttm_roe": "함정: 기말 자본만", "hana_ttm_roe": "기타 오답",
                        "shinhan_roe_gap_pp": "함정: ROE를 기말 자본만(으)로 계산", "hana_roe_gap_pp": "기타 오답"}
    assert ("shinhan_ttm_roe", "기말 자본만", False) in hits


def test_missing_answers_are_placed_at_the_step_that_failed():
    def not_submitted(ref):
        sub = _step(ref, "reporter", "submit_answer")
        sub[1]["answers"] = [a for a in sub[1]["answers"] if a["field"] != "hana_roe_gap_pp"]
    assert lb.classify(_run(not_submitted), K06)[0]["hana_roe_gap_pp"] == "제출 누락"
    assert lb.classify(_run(lambda ref: _drop(ref, "calc.hana_roe_gap_pp")), K06)[0]["hana_roe_gap_pp"] == "공유 누락"

    def trap_only(ref):
        _step(ref, "analyst", "calculate", "shinhan_ttm_roe")[1].update(name="shinhan_roe_end",
                                                                        formula="(fy - h1p + h1) / e1 * 100")
        for fid in ("calc.shinhan_ttm_roe", "calc.shinhan_roe_gap_pp"):
            _drop(ref, fid)
    outcomes, detail, _ = lb.classify(_run(trap_only), K06)
    assert outcomes["shinhan_ttm_roe"] == "함정 값만 계산: 기말 자본만"
    assert outcomes["shinhan_roe_gap_pp"] == "계산 안 함" and outcomes["hana_ttm_roe"] == "정답"

    def input_missing(ref):
        _step(ref, "collector", "share_facts")[1]["fact_ids"].remove("086790.2025H1.CFS.equity_owners")
    outcomes, detail, _ = lb.classify(_run(input_missing), K06)
    assert outcomes["hana_ttm_roe"] == outcomes["hana_roe_gap_pp"] == "입력 부족"
    assert detail["hana_ttm_roe"] == "필요한 수치 5개 중 1개 없음: 086790.2025H1.CFS.equity_owners"
    assert outcomes["shinhan_ttm_roe"] == "공유 누락"          # the analyst's share call failed as a whole


def _write_run(root: Path, name: str, model: str, trajs: list[dict], cost: float, planned_seeds=(7,)):
    d = root / name
    (d / "trajectories").mkdir(parents=True)
    for i, t in enumerate(trajs):
        t = dict(t, usage={"cost_usd": cost}, seed=7 + i)
        (d / "trajectories" / f"{t['task_id']}__{t['setting']}__s{t['seed']}.json").write_text(
            json.dumps(t, ensure_ascii=False), encoding="utf-8")
    (d / "run_config.json").write_text(json.dumps({"model": model, "provider": "test", "tasks": [K06.id],
                                                   "settings": ["full", "no_comm"], "seeds": list(planned_seeds)}),
                                       encoding="utf-8")


def test_leaderboard_from_run_directories(tmp_path):
    good = [_run(), _run(setting="no_comm")]
    bad = [_run(lambda ref: _drop(ref, "calc.hana_roe_gap_pp"))]
    _write_run(tmp_path, "finance_kr_good", "model-a", good, 0.5)
    _write_run(tmp_path, "finance_kr_bad", "model-b", bad, 0.1)
    (tmp_path / "finance_ca_other").mkdir()                                  # another desk: ignored
    runs = lb.discover(tmp_path)
    assert {m: [d.name for d in ds] for m, ds in runs.items()} == {"model-b": ["finance_kr_bad"],
                                                                   "model-a": ["finance_kr_good"]}
    episodes, skipped, planned = lb.load_episodes(runs)
    assert skipped == [] and planned == {"model-a": 2, "model-b": 2}
    md = lb.render(episodes, skipped, planned=planned)
    assert md.startswith("# 한국 데스크 모델 비교\n")
    assert "| model-a | 2 | 2 (100%) | 100% | 7, 8 | 55% | 0.00 | 0% | - | $1.00 | $0.500 | $0.500 |" in md
    assert "| model-b | 1 | 0 (0%) | 75% | 7 |" in md and md.index("| model-a |") < md.index("| model-b |")
    assert "| model-a | 실행 안 함 | 실행 안 함 | 실행 안 함 | 실행 안 함 | 실행 안 함 | 2/2 |" in md
    assert "| model-b | 공유 누락 |" not in md and "| model-b | 1 | 1 |" in md   # category counts
    assert "| model-b | k06_valueup_roe_gap_private full s7 | 20/20 | hana_roe_gap_pp | 공유 누락 |" in md
    assert "k01, k02, k03, k04, k05은(는) 어느 모델로도 실행하지 않았습니다." in md
    assert "model-b: 실행 설정상 2판 중 1판만 기록되어 있습니다" in md


def test_a_trajectory_that_no_longer_replays_is_listed_not_counted(tmp_path):
    t = _run()
    t["actions"][0]["ok"] = not t["actions"][0]["ok"]
    _write_run(tmp_path, "finance_kr_x", "model-x", [t], 0.0)
    episodes, skipped, _ = lb.load_episodes(lb.discover(tmp_path))
    assert episodes == [] and len(skipped) == 1 and "does not replay" in skipped[0]
