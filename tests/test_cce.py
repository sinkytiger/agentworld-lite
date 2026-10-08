from agentworld_lite.cce import LLMJudge, RuleJudge, agreement, ancestors
from agentworld_lite.llm import ClaudeClient
from agentworld_lite.runner import make_factory, run_episode
from agentworld_lite.task import Task

from conftest import TASK_DIR, FakeSDK, json_response


def ref_traj(tid):
    return run_episode(Task.load(TASK_DIR / f"{tid}.yaml"), make_factory("scripted"))


def test_ancestors():
    edges = [("a", "b"), ("b", "c"), ("x", "y")]
    assert ancestors({"c"}, edges) == {"a", "b"}


def test_rule_cce_staff_chain():
    traj = ref_traj("t01_magic_staff")
    res = RuleJudge().compute(traj)
    # harvest -> transfer -> craft sticks -> transfer -> craft staff -> equip
    assert res["n_contributing"] == 6
    assert res["cce"] == 6 / len(traj["actions"])
    assert set(res["pac"]) == {"t01_lumberjack", "t01_woodworker", "t01_wizard"}
    assert all(v > 0 for v in res["pac"].values())


def test_failed_episode_has_zero_cce():
    traj = ref_traj("t01_magic_staff")
    traj["result"]["success"] = False
    assert RuleJudge().compute(traj)["cce"] == 0.0


def test_gathering_success_actions_are_deliveries():
    traj = ref_traj("t02_supply_run")
    s = RuleJudge().success_actions(traj)
    by_id = {a["id"]: a for a in traj["actions"]}
    assert {by_id[i]["tool"] for i in s} == {"transfer_items"} and len(s) == 3


def test_llm_judge_backward_sweep():
    traj = ref_traj("t01_magic_staff")
    acts = traj["actions"]
    final = acts[-1]["id"]

    def responder(kw):
        prompt = kw["messages"][0]["content"]
        if "FINAL actions" in prompt:
            return json_response({"success_action_ids": [final], "reasoning": "equip"})
        r = int(prompt.split("Now consider the actions from Round ")[1].split(" ")[0])
        judg = [{"action_id": a["id"], "contributes": a["ok"], "enables": [], "reason": ""}
                for a in acts if a["round"] == r and a["id"] != final]
        return json_response({"judgments": judg})

    sdk = FakeSDK(responder)
    judge = LLMJudge(ClaudeClient(model="claude-opus-5-5", sdk_client=sdk))
    res = judge.compute(traj)
    assert res["success_ids"] == [final]
    assert res["n_contributing"] == sum(a["ok"] for a in acts)
    call = sdk.all_calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    agree = agreement(traj, res, res)
    assert agree["raw"] == 1.0 and agree["kappa"] == 1.0


def test_repeated_chat_rate():
    from agentworld_lite.report import repeated_chats
    chat = lambda i, msg, ok=True: {"id": i, "tool": "send_chat", "ok": ok, "args": {"to": "all", "message": msg}}
    traj = {"actions": [chat("a", "Need an AXE"), chat("b", "need an  axe"), chat("c", "on my way"),
                        chat("d", "need an axe", ok=False), {"id": "e", "tool": "wait", "ok": True, "args": {}}]}
    assert repeated_chats(traj) == (1, 3)
