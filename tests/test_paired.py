from collections import Counter

import yaml

from agentworld_lite.agents import BaseAgent, Decision
from agentworld_lite.paired import _facts, compare_pairs, generate_pairs
from agentworld_lite.runner import make_factory, run_episode, run_suite
from agentworld_lite.task import Task


class ObserveAndWait(BaseAgent):
    def __init__(self, name, first_turns):
        super().__init__(name)
        self.first_turns = first_turns

    def act(self, turn):
        self.first_turns.setdefault(self.name, turn)
        return Decision("wait", {"reason": "observe relay"})


def test_matched_variants_preserve_the_task_and_all_facts():
    pairs, records = generate_pairs(2, seed=7)
    assert len(pairs) == 2 and sum(r["status"] == "kept" for r in records) == 2
    for shared, private in pairs:
        assert shared["id"] != private["id"]
        assert shared["max_rounds"] == private["max_rounds"]
        for key in ("name", "description", "objectives", "verifier", "extra_entities", "reference_solution"):
            assert shared[key] == private[key]
        assert shared["world_guide"] is private["world_guide"] is True
        assert Task.from_dict(shared).build_world().snapshot() == Task.from_dict(private).build_world().snapshot()
        assert [a["username"] for a in shared["agents"]] == [a["username"] for a in private["agents"]]
        for a, b in zip(shared["agents"], private["agents"]):
            assert {k: v for k, v in a.items() if k != "role_brief"} == {
                k: v for k, v in b.items() if k != "role_brief"}
        source = Counter(line for lines in _facts(shared["relevant_game_context"]).values() for line in lines)
        moved = Counter(line for a in private["agents"] for line in a["role_brief"].splitlines() if line in source)
        assert source == moved
        assert private["relevant_game_context"] == ""
        assert private["generation"]["matched"]["relay_messages"]


def test_reference_succeeds_under_every_information_condition():
    pairs, _ = generate_pairs(1, seed=7)
    shared, private = [Task.from_dict(t) for t in pairs[0]]
    trajectories = []
    for task in (shared, private):
        for setting in ("full", "no_comm"):
            traj = run_episode(task, make_factory("scripted"), setting=setting)
            assert traj["result"]["success"]
            trajectories.append(traj)
    relay = run_episode(private, make_factory("scripted"), setting="relay")
    assert relay["result"]["success"]
    assert relay["result"]["n_chats"] == 0
    trajectories.append(relay)
    report = compare_pairs(trajectories)
    assert "1 distinct blueprints" in report
    assert "Private relay success: 1/1" in report
    assert "rescue could not be assessed" in report
    private_no_chat = next(t for t in trajectories if t["matched"]["placement"] == "private"
                           and t["setting"] == "no_comm")
    private_no_chat["result"]["success"] = False
    assert "Of 1 private no-chat failures, 1 succeeded with relay" in compare_pairs(trajectories)


def test_relay_delivers_only_predeclared_plan_facts_with_chat_disabled():
    pairs, _ = generate_pairs(1, seed=7)
    private_dict = pairs[0][1]
    private = Task.from_dict(private_dict)
    first_turns = {}
    run_episode(private, lambda task, name: ObserveAndWait(name, first_turns), setting="relay")
    for msg in private_dict["generation"]["matched"]["relay_messages"]:
        turn = first_turns[msg["to"]]
        assert msg["text"] in turn.observation
        assert "send_chat" not in turn.allowed_tools


def test_shuffled_suite_skips_redundant_shared_relay(tmp_path):
    pairs, _ = generate_pairs(1, seed=7)
    task_dir = tmp_path / "tasks"
    task_dir.mkdir()
    for td in pairs[0]:
        (task_dir / f"{td['id']}.yaml").write_text(yaml.safe_dump(td), encoding="utf-8")
    runs = run_suite([task_dir], tmp_path / "run", agent="scripted",
                     settings=("full", "no_comm", "relay"), seeds=(7,), workers=1,
                     shuffle_jobs=True, job_seed=7)
    assert len(runs) == 5
    assert not any(t["matched"]["placement"] == "shared" and t["setting"] == "relay" for t in runs)
    assert "Complete blocks: 1" in compare_pairs(runs)
