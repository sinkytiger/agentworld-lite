import yaml

from agentworld_lite.augment import augment_task
from agentworld_lite.failures import classify_failures
from agentworld_lite.llm import ClaudeClient
from agentworld_lite.runner import make_factory, run_episode
from agentworld_lite.task import Task

from conftest import TASK_DIR, FakeSDK, json_response


def test_failure_classification_counts():
    traj = run_episode(Task.load(TASK_DIR / "t08_crystal_plates.yaml"), make_factory("scripted"))
    chat_ids = [a["id"] for a in traj["actions"] if a["tool"] == "send_chat"]

    def responder(kw):
        assert "state: checkpoints:" in kw["messages"][0]["content"]
        labels = [{"action_id": i, "category": "none" if k else "factual_error", "explanation": ""}
                  for k, i in enumerate(chat_ids)] + [{"action_id": "bogus", "category": "none", "explanation": ""}]
        return json_response({"labels": labels})

    out = classify_failures(traj, ClaudeClient(model="claude-opus-5-5", sdk_client=FakeSDK(responder)))
    assert out["counts"]["factual_error"] == 1
    assert out["counts"]["none"] == len(chat_ids) - 1
    assert len(out["labels"]) == len(chat_ids)


def test_augment_keeps_only_verified_variants():
    src = Task.load(TASK_DIR / "t01_magic_staff.yaml")
    good = src.to_dict()
    good["agents"][2]["spawn"] = [18, 27]
    broken = dict(good, reference_solution={"t01_wizard": [["equip_item", {"item": "staff"}]]})
    replies = iter([broken, good])

    def responder(kw):
        return json_response({"task_yaml": yaml.safe_dump(next(replies)), "changes": ["moved wizard"],
                              "collaboration_rationale": "axe + fletching + bead are split"})

    sdk = FakeSDK(responder)
    task, log = augment_task(src, ClaudeClient(model="claude-opus-5-5", sdk_client=sdk), k=1, previous=[])
    assert task is not None and task.id == "t01_magic_staff_v1"
    assert len(log["attempts"]) == 2 and "Reference solution failed" in log["attempts"][0]["problem"]
    assert task.meta["needs_human_review"] and task.meta["augmented_from"] == "t01_magic_staff"
    assert "Reference solution failed" in sdk.all_calls[1]["messages"][0]["content"]
