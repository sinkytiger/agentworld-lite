import pytest

from agentworld_lite.agents import BaseAgent, Decision
from agentworld_lite.blueprint import GenParams, build_blueprint
from agentworld_lite.gym import generate_gym, round_budget
from agentworld_lite.runner import make_factory, run_episode
from agentworld_lite.task import Task

KEPT, RECORDS = generate_gym(30, seed=1)


class WaitAgent(BaseAgent):
    def act(self, turn):
        return Decision("wait", {"reason": "idle"})


def test_yield_and_determinism():
    assert len(KEPT) / len(RECORDS) >= 0.8
    again, _ = generate_gym(30, seed=1)
    assert [t["id"] for t in again] == [t["id"] for t in KEPT]
    assert [t["generation"]["blueprint"]["target"] for t in again] == [t["generation"]["blueprint"]["target"] for t in KEPT]


@pytest.mark.parametrize("td", KEPT, ids=lambda d: d["id"])
def test_generated_task_is_certified(td):
    task = Task.from_dict(td)
    errors, warnings = task.validate()
    assert not errors and not [w for w in warnings if "alone" in w]
    r = run_episode(task, make_factory("scripted"))["result"]
    assert r["success"] and r["rounds_used"] <= task.max_rounds
    assert not run_episode(task, lambda t, n: WaitAgent(n))["result"]["success"]
    gt = td["generation"]["ground_truth"]["rounds"]
    assert task.max_rounds == round_budget(gt, td["generation"]["params"]["obfuscation"])


def test_obfuscation_moves_information_out_of_the_shared_doc():
    by_bucket = {}
    for td in KEPT:
        by_bucket.setdefault(td["generation"]["obfuscation_bucket"], td)
    low = by_bucket["low"]
    assert "Plan:" in low["relevant_game_context"] and low["world_guide"]
    for b in ("high", "max"):
        if b in by_bucket:
            assert "Recipes:" not in by_bucket[b]["relevant_game_context"]
    if "max" in by_bucket:
        mx = by_bucket["max"]
        assert not mx["world_guide"]
        assert any("What success means" in a.get("role_brief", "") for a in mx["agents"])


def test_staff_blueprint_expands_recipe_tree():
    for seed in range(200):
        p = GenParams(depth=2, topology="chain", obfuscation=0.0, quantity=1, extra_agents=0, seed=seed)
        bp = build_blueprint(p)
        if bp.target == "staff":
            break
    assert bp.crafts == {"staff": 1, "stick": 2}            # 5 sticks needed, 4 per craft
    assert bp.raw_need == {"logs": 2}
    assert bp.roles["client"].holds == {"bead": 1}          # the wizard brings the bead
    assert ["client", "crafter", "bead", 1] in bp.deliveries
    assert ["crafter", "client", "staff", 1] in bp.deliveries
