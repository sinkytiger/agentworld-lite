from types import SimpleNamespace

from agentworld_lite.agents import LLMAgent
from agentworld_lite.llm import ClaudeClient
from agentworld_lite.runner import run_episode
from agentworld_lite.task import Task

from conftest import TASK_DIR, FakeSDK, text, tool_use, usage


def test_llm_agent_request_shape_and_episode():
    task = Task.load(TASK_DIR / "t08_crystal_plates.yaml")
    plans = {n: [list(s) for s in steps] for n, steps in task.reference_solution.items()}

    def responder(kw):
        obs = kw["messages"][0]["content"][1]["text"]
        me = obs.split("You: ")[1].split(" ")[0]
        call = plans[me].pop(0) if plans[me] else ["wait", {"reason": "done"}]
        return SimpleNamespace(content=[text("plan"), tool_use(call[0], dict(call[1]))], stop_reason="tool_use", usage=usage())

    sdk = FakeSDK(responder)
    client = ClaudeClient(model="claude-opus-5-5", effort="low", sdk_client=sdk)
    traj = run_episode(task, lambda t, n: LLMAgent(n, client))
    assert traj["result"]["success"]
    assert traj["usage"]["calls"] == len(traj["actions"])

    call = sdk.beta.messages.calls[0]  # Opus 5.5 -> beta endpoint with server-side fallbacks
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert call["output_config"] == {"effort": "low"}
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in call["tools"])
    ctx, obs = call["messages"][0]["content"]
    assert ctx["cache_control"] == {"type": "ephemeral"} and "**Team Information:**" in ctx["text"]
    assert "=== Round 1" in obs["text"]
    assert "MUST call exactly ONE tool" in call["system"]
    assert traj["actions"][0]["reasoning"] == "plan"


def test_haiku_has_no_effort_or_fallbacks():
    sdk = FakeSDK(lambda kw: SimpleNamespace(content=[tool_use("wait", {"reason": "x"})], stop_reason="tool_use", usage=usage()))
    client = ClaudeClient(model="claude-haiku-4-5", effort="low", sdk_client=sdk)
    client.create(messages=[{"role": "user", "content": "hi"}])
    call = sdk.messages.calls[0]
    assert "output_config" not in call and "fallbacks" not in call and not sdk.beta.messages.calls


def test_no_tool_call_gets_nudged_then_waits():
    sdk = FakeSDK(lambda kw: SimpleNamespace(content=[text("I will think about it")], stop_reason="end_turn", usage=usage()))
    client = ClaudeClient(model="claude-opus-5-5", sdk_client=sdk)
    task = Task.load(TASK_DIR / "t01_magic_staff.yaml")
    task.max_rounds = 1
    traj = run_episode(task, lambda t, n: LLMAgent(n, client))
    assert all(a["tool"] == "wait" and a["note"] == "no_tool_call" for a in traj["actions"])
    second = sdk.all_calls[1]["messages"]
    assert second[-1]["content"].startswith("You did not call a tool")
