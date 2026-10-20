from __future__ import annotations

import pytest

from agentworld_lite.agents import BaseAgent, Decision, TurnInput
from agentworld_lite.coordination import CoordinationAgent
from agentworld_lite.engine import ActionResult
from agentworld_lite.runner import make_factory, run_suite


class SequenceAgent(BaseAgent):
    def __init__(self, decisions):
        super().__init__("worker")
        self.decisions = iter(decisions)
        self.observations = []
        self.allowed = []
        self.results = []

    def act(self, turn):
        self.observations.append(turn.observation)
        self.allowed.append(turn.allowed_tools)
        return next(self.decisions)

    def observe_result(self, result):
        self.results.append(result)


def turn(round_number, tools=("send_chat", "wait", "move_to")):
    return TurnInput(None, "worker", round_number, "visible observation", "private context", list(tools))


def chat(message="Bring 1 leather", to="smith", tokens=1):
    return Decision("send_chat", {"to": to, "message": message}, usage={"input_tokens": tokens})


def test_duplicate_chat_gets_one_new_decision_and_counts_both_calls():
    inner = SequenceAgent([chat(), chat(" Bring   1  leather ", " smith ", 2),
                           Decision("move_to", {"x": 3, "y": 4}, usage={"input_tokens": 3})])
    agent = CoordinationAgent(inner)
    assert agent.act(turn(1)).tool == "send_chat"
    agent.observe_result(ActionResult(True, "sent"))

    chosen = agent.act(turn(2))
    assert chosen.tool == "move_to"
    assert chosen.usage["input_tokens"] == 5
    assert chosen.usage["calls"] == 2
    assert "duplicate_chat_replanned" in chosen.note
    assert "already sent this message to that recipient" in inner.observations[-1]
    assert len(inner.observations) == 3


def test_second_duplicate_is_suppressed_but_later_resend_is_allowed():
    inner = SequenceAgent([chat(), chat(), chat(), chat()])
    agent = CoordinationAgent(inner, chat_cooldown=4)
    agent.act(turn(1))
    agent.observe_result(ActionResult(True, "sent"))
    duplicate = agent.act(turn(2))
    assert duplicate.tool == "wait" and duplicate.note == "duplicate_chat_suppressed"
    agent.observe_result(ActionResult(True, "waited"))
    assert agent.act(turn(6)).tool == "send_chat"


def test_dedup_is_case_sensitive_and_ignores_teammate_chat():
    # Policy v2: usernames and item ids can be case-sensitive, and a teammate's
    # identical line may be an acknowledgment, so only this agent's own sends count.
    inner = SequenceAgent([chat("Need 2 coal", "smith"), chat("Need 2 COAL", "smith")])
    agent = CoordinationAgent(inner)
    visible = turn(2)
    visible.observation += "\nChat log:\n *[R1] miner -> smith: Need 2 coal"
    first = agent.act(visible)
    assert first.tool == "send_chat" and first.note == ""
    agent.observe_result(ActionResult(True, "sent"))
    assert agent.act(visible).tool == "send_chat"  # different case is a different message


def test_failed_chat_can_be_retried_and_stalled_waits_are_visible():
    inner = SequenceAgent([chat(), chat(), Decision("wait", {"reason": "need ore"}),
                           Decision("wait", {"reason": "need ore"}),
                           Decision("wait", {"reason": "need ore"}),
                           Decision("move_to", {"x": 1, "y": 2})])
    agent = CoordinationAgent(inner, stall_threshold=3)
    agent.act(turn(1))
    agent.observe_result(ActionResult(False, "blocked"))
    assert agent.act(turn(2)).tool == "send_chat"
    agent.observe_result(ActionResult(True, "sent"))
    for round_number in (3, 4, 5):
        assert agent.act(turn(round_number)).tool == "wait"
        agent.observe_result(ActionResult(True, "waited"))
    assert agent.act(turn(6)).tool == "move_to"
    assert "waited 3 consecutive turns" in inner.observations[-1]
    assert "need ore" in inner.observations[-1]


def test_full_health_food_is_replanned_before_execution():
    inner = SequenceAgent([Decision("eat_food", {"item": "apple"}, usage={"input_tokens": 2}),
                           Decision("harvest_resource", {"target": "tree#1"}, usage={"input_tokens": 3})])
    agent = CoordinationAgent(inner)
    visible = turn(1, tools=("eat_food", "harvest_resource", "wait"))
    visible.observation = "You: worker at (4,5) HP 75/75\nInventory: apple x3"
    chosen = agent.act(visible)
    assert chosen.tool == "harvest_resource"
    assert chosen.usage == {"input_tokens": 5, "calls": 2}
    assert chosen.note == "ineffective_action_replanned"
    assert "HP is already full" in inner.observations[-1]
    assert "eat_food" not in inner.allowed[0]


def test_restricted_and_blocked_tools_are_not_offered():
    inner = SequenceAgent([Decision("wait", {"reason": "ready"})])
    agent = CoordinationAgent(inner)
    visible = turn(1, tools=("wait", "harvest_resource", "send_chat"))
    visible.observation = "You: worker at (4,5) HP 70/75\nYour role CANNOT use: harvest_resource"
    assert agent.act(visible).tool == "wait"
    assert inner.allowed[0] == ["wait", "send_chat"]


def test_repeated_failed_action_is_suppressed_after_one_retry():
    bad = Decision("craft_item", {"item_key": "sword"}, usage={"input_tokens": 1})
    inner = SequenceAgent([bad, bad, bad])
    agent = CoordinationAgent(inner)
    assert agent.act(turn(1, tools=("craft_item", "wait"))).tool == "craft_item"
    agent.observe_result(ActionResult(False, "Missing 2 iron bars."))
    chosen = agent.act(turn(2, tools=("craft_item", "wait")))
    assert chosen.tool == "wait"
    assert chosen.note == "ineffective_action_suppressed"
    assert chosen.usage["calls"] == 2
    assert "Missing 2 iron bars" in inner.observations[-1]


def test_replanning_budget_limits_extra_model_calls():
    inner = SequenceAgent([chat(), chat(), chat(), chat()])
    agent = CoordinationAgent(inner, max_replans=1)
    agent.act(turn(1))
    agent.observe_result(ActionResult(True, "sent"))
    second = agent.act(turn(2))
    agent.observe_result(ActionResult(True, "waited"))
    third = agent.act(turn(3))
    assert second.usage["calls"] == 2
    assert third.tool == "wait" and third.usage == {"input_tokens": 1}
    assert len(inner.observations) == 4  # one original and one extra decision at round 2


def test_coordination_rejects_non_llm_agents(tmp_path):
    with pytest.raises(ValueError, match="only available for LLM"):
        make_factory("scripted", coordination=True)
    with pytest.raises(ValueError, match="only available for LLM"):
        run_suite([], tmp_path, agent="scripted", coordination=True)
