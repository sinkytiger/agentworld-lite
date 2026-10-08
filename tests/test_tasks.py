import pytest

from agentworld_lite.agents import BaseAgent, Decision
from agentworld_lite.runner import make_factory, run_episode
from agentworld_lite.task import load_tasks
from agentworld_lite.verifier import evaluate

from conftest import TASK_DIR

TASKS = load_tasks(str(TASK_DIR))


class WaitAgent(BaseAgent):
    def act(self, turn):
        return Decision("wait", {"reason": "idle"})


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_task_is_valid_and_needs_collaboration(task):
    errors, warnings = task.validate()
    assert not errors
    assert not [w for w in warnings if "alone" in w]


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_reference_solution_solves_task(task):
    assert not evaluate(task.build_world(), task.verifier)["success"]
    r = run_episode(task, make_factory("scripted"))["result"]
    assert r["success"] and r["rounds_used"] <= task.max_rounds


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_idle_team_fails(task):
    r = run_episode(task, lambda t, n: WaitAgent(n))["result"]
    assert not r["success"]


def test_all_categories_covered():
    cats = {t.category for t in TASKS}
    assert cats == {"combat", "crafting", "gathering", "trading", "exploration", "survival", "construction", "coordination"}


def test_no_comm_setting_blocks_chat():
    task = next(t for t in TASKS if t.id == "t08_crystal_plates")
    traj = run_episode(task, make_factory("scripted"), setting="no_comm")
    chats = [a for a in traj["actions"] if a["tool"] == "send_chat"]
    assert chats and not any(a["ok"] for a in chats)
