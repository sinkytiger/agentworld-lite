import json

import pytest

from agentworld_lite.cce import LLMJudge
from agentworld_lite.local_llm import OllamaAgent, OllamaClient, OllamaError, parse_text_tool_call
from agentworld_lite.runner import make_factory, run_episode
from agentworld_lite.task import Task

from conftest import TASK_DIR


class FakeOllama(OllamaClient):
    """OllamaClient with the HTTP layer replaced by a responder function."""

    def __init__(self, responder, models=("qwen3:8b",)):
        super().__init__("qwen3:8b")
        self.responder = responder
        self.models = models
        self.requests = []

    def _request(self, path, payload=None):
        self.requests.append((path, payload))
        if path == "/api/tags":
            return {"models": [{"name": m, "model": m, "details": {"parameter_size": "8.2B"}} for m in self.models]}
        out = self.responder(payload)
        out.setdefault("prompt_eval_count", 120)
        out.setdefault("eval_count", 15)
        return out


def plan_responder(task, style="structured"):
    plans = {n: [list(s) for s in steps] for n, steps in task.reference_solution.items()}

    def responder(payload):
        user = payload["messages"][1]["content"]
        me = user.split("You: ")[1].split(" ")[0]
        tool, args = plans[me].pop(0)[:2] if plans[me] else ("wait", {"reason": "done"})
        if style == "structured":
            return {"message": {"role": "assistant", "content": "",
                                "tool_calls": [{"function": {"name": tool, "arguments": args}}]}}
        if style == "string_args":
            return {"message": {"role": "assistant", "content": "",
                                "tool_calls": [{"function": {"name": tool, "arguments": json.dumps(args)}}]}}
        return {"message": {"role": "assistant", "content": f"I will act now: {json.dumps({'name': tool, 'arguments': args})}"}}
    return responder


@pytest.mark.parametrize("style", ["structured", "string_args", "text"])
def test_ollama_agent_plays_an_episode(style):
    task = Task.load(TASK_DIR / "t08_crystal_plates.yaml")
    client = FakeOllama(plan_responder(task, style))
    traj = run_episode(task, lambda t, n: OllamaAgent(n, client))
    assert traj["result"]["success"]
    assert traj["usage"]["calls"] == len(traj["actions"]) and traj["usage"]["cost_usd"] == 0
    if style == "text":
        assert all(a["note"] == "text_tool_call" for a in traj["actions"])
    payload = client.requests[0][1]
    assert payload["stream"] is False and payload["options"]["num_ctx"] == 8192
    assert payload["messages"][0]["role"] == "system" and "MUST call exactly ONE tool" in payload["messages"][0]["content"]
    names = [t["function"]["name"] for t in payload["tools"]]
    assert len(names) == 13 and payload["tools"][0]["type"] == "function"
    assert "parameters" in payload["tools"][0]["function"]


def test_sampling_seed_is_sent_when_set():
    client = FakeOllama(lambda p: {"message": {"role": "assistant", "content": "{}"}})
    client.chat([{"role": "user", "content": "hi"}])
    assert "seed" not in client.requests[-1][1]["options"]
    client.seed = 8
    client.chat([{"role": "user", "content": "hi"}])
    assert client.requests[-1][1]["options"]["seed"] == 8


def test_no_tool_call_is_nudged_then_waits():
    client = FakeOllama(lambda p: {"message": {"role": "assistant", "content": "Let me think about the plan."}})
    task = Task.load(TASK_DIR / "t01_magic_staff.yaml")
    task.max_rounds = 1
    traj = run_episode(task, lambda t, n: OllamaAgent(n, client))
    assert all(a["tool"] == "wait" and a["note"] == "no_tool_call" for a in traj["actions"])
    second = client.requests[1][1]["messages"]
    assert second[-1]["content"].startswith("You did not call a tool")


def test_text_tool_call_parser():
    assert parse_text_tool_call('{"name": "move_to", "arguments": {"x": 3, "y": 4}}') == ("move_to", {"x": 3, "y": 4})
    assert parse_text_tool_call('```json\n{"function": {"name": "wait"}, "parameters": {"reason": "x"}}\n```') == ("wait", {"reason": "x"})
    assert parse_text_tool_call('{"name": "fly", "arguments": {}}') is None
    assert parse_text_tool_call("no json here") is None


def test_json_call_drives_the_cce_judge():
    traj = run_episode(Task.load(TASK_DIR / "t01_magic_staff.yaml"), make_factory("scripted"))
    final = traj["actions"][-1]["id"]

    def responder(payload):
        assert payload["format"]["type"] == "object"
        prompt = payload["messages"][1]["content"]
        if "FINAL actions" in prompt:
            body = {"success_action_ids": [final], "reasoning": "equip"}
        else:
            body = {"judgments": []}
        return {"message": {"role": "assistant", "content": json.dumps(body)}}

    res = LLMJudge(FakeOllama(responder)).compute(traj)
    assert res["success_ids"] == [final] and res["n_contributing"] == 1


def test_cloud_model_skips_format_and_extracts_json():
    seen = []

    def responder(payload):
        seen.append(payload)
        return {"message": {"role": "assistant", "content": 'Sure! ```json\n{"labels": []}\n```'}}

    client = FakeOllama(responder, models=())
    client.model = "gemma4:cloud"
    assert client.check().startswith("cloud model")
    data, _ = client.json_call("sys", "classify", {"type": "object", "properties": {"labels": {"type": "array"}}})
    assert data == {"labels": []}
    assert "format" not in seen[0] and "JSON schema" in seen[0]["messages"][1]["content"]


def test_local_model_falls_back_when_format_reply_is_not_json():
    replies = iter(["not json", '{"ok": true}'])
    client = FakeOllama(lambda p: {"message": {"role": "assistant", "content": next(replies)}})
    data, _ = client.json_call("sys", "q", {"type": "object"})
    assert data == {"ok": True}
    assert "format" in client.requests[0][1] and "format" not in client.requests[1][1]


def test_rate_limit_is_retried_with_backoff(monkeypatch):
    import agentworld_lite.local_llm as ll
    monkeypatch.setattr(ll.time, "sleep", lambda s: None)
    calls = []

    class Flaky(OllamaClient):
        def _send(self, path, payload=None):
            calls.append(path)
            if len(calls) < 3:
                err = OllamaError("HTTP 429")
                err.status = 429
                raise err
            return {"message": {"role": "assistant", "content": "{}"}, "prompt_eval_count": 1, "eval_count": 1}

    resp, _ = Flaky("gemma4:cloud").chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 3 and resp["message"]["content"] == "{}"

    class Denied(OllamaClient):
        def _send(self, path, payload=None):
            err = OllamaError("HTTP 401")
            err.status = 401
            raise err

    with pytest.raises(OllamaError):
        Denied("gemma4:cloud").chat([{"role": "user", "content": "hi"}])


def test_check_reports_missing_model_and_unreachable_server():
    with pytest.raises(OllamaError, match="ollama pull qwen3:8b"):
        FakeOllama(lambda p: {}, models=("llama3.2:3b",)).check()
    assert FakeOllama(lambda p: {}).check() == "8.2B"
    with pytest.raises(OllamaError, match="Cannot reach Ollama"):
        OllamaClient("qwen3:8b", host="http://127.0.0.1:9", timeout=2).check()
