"""Optional local decision support using only an agent's visible information."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from .agents import BaseAgent, Decision, TurnInput
from .engine import ActionResult

POLICY_VERSION = "2"


@dataclass(frozen=True)
class CoordinationConfig:
    chat_cooldown: int = 8
    failure_cooldown: int = 3
    stall_threshold: int = 3
    max_replans: int = 8

    def __post_init__(self):
        for key in ("chat_cooldown", "failure_cooldown", "stall_threshold"):
            if not isinstance(getattr(self, key), int) or getattr(self, key) < 1:
                raise ValueError(f"{key} must be a positive integer")
        if not isinstance(self.max_replans, int) or self.max_replans < 0:
            raise ValueError("max_replans must be a nonnegative integer")

    def to_dict(self) -> dict:
        return asdict(self)


def _message_key(args: dict) -> tuple[str, str]:
    # Usernames and item IDs can be case-sensitive. Normalize whitespace only.
    return str(args.get("to", "all")).strip(), " ".join(str(args.get("message", "")).split())[:600]


def _add_usage(*decisions: Decision) -> dict:
    usage: dict = {}
    for decision in decisions:
        for key, value in decision.usage.items():
            usage[key] = usage.get(key, 0) + value
        if decision.usage and "calls" not in decision.usage:
            usage["calls"] = usage.get("calls", 0) + 1
    return usage


def _action_key(decision: Decision) -> tuple[str, str]:
    return decision.tool, json.dumps(decision.args, sort_keys=True, default=str)


def _physical_text(turn: TurnInput) -> str:
    # Compatibility for callers without structured state. Chat/history cannot
    # supply authoritative HP, position, or restrictions.
    text = turn.observation.split("\nChat log", 1)[0].split("\nYour recent actions", 1)[0]
    return re.sub(r"^=== Round .*?===\s*", "", text).strip()


def _own_state(turn: TurnInput) -> dict:
    if turn.visible_state is not None:
        return turn.visible_state.get("self", {})
    text = _physical_text(turn)
    own_line = next((line for line in text.splitlines() if line.startswith("You: ")), "")
    state: dict = {}
    hp = re.search(r"\bHP (\d+)/(\d+)\b", own_line)
    if hp:
        state.update(hp=int(hp[1]), max_hp=int(hp[2]))
    pos = re.search(r" at \((-?\d+),\s*(-?\d+)\)", own_line)
    if pos:
        state["position"] = [int(pos[1]), int(pos[2])]
    restricted = re.search(r"^Your role CANNOT use: ([^\n]+)", text, re.M)
    if restricted:
        state["restricted_tools"] = [tool.strip() for tool in restricted[1].split(",")]
    return state


def _fingerprint(turn: TurnInput) -> str:
    visible = turn.visible_state if turn.visible_state is not None else _physical_text(turn)
    return json.dumps(visible, sort_keys=True, default=str)


def _available_tools(turn: TurnInput, own: dict) -> list[str]:
    forbidden = set(own.get("restricted_tools", []))
    if own.get("max_hp", 0) > 0 and own.get("hp") == own["max_hp"]:
        forbidden.add("eat_food")
    return [tool for tool in turn.allowed_tools if tool not in forbidden]


def _no_progress(message: str) -> bool:
    if message.startswith("Already at "):
        return True
    eaten = re.search(r"\bHP (\d+) -> (\d+)/\d+", message)
    return bool(eaten and eaten[1] == eaten[2])


class CoordinationAgent(BaseAgent):
    """Offer compact feedback, revise bad proposals, and retain an audit trail.

    Failure suppression expires when visible physical state changes. Chat
    deduplication only applies to this agent's own successful sends, preserving
    distinct teammates' acknowledgments. At most one extra decision per turn and
    max_replans per episode; an inner decision may retry a malformed model reply,
    so actual API calls are recorded separately in usage.calls.
    """

    kind = "llm_coordination"

    def __init__(self, inner: BaseAgent, chat_cooldown: int = 8, stall_threshold: int = 3,
                 max_replans: int = 8, failure_cooldown: int = 3) -> None:
        super().__init__(inner.name)
        self.inner = inner
        self.config = CoordinationConfig(chat_cooldown, failure_cooldown, stall_threshold, max_replans)
        self.replans = 0
        self.sent: dict[tuple[str, str], tuple[int, str]] = {}
        self.ineffective: dict[tuple[str, str], tuple[int, str, str]] = {}
        self.wait_streak = 0
        self.last_wait_reason = ""
        self._decision: Decision | None = None
        self._round = 0
        self._state = ""

    def _recent_duplicate(self, decision: Decision, turn: TurnInput, state: str) -> bool:
        if decision.tool != "send_chat":
            return False
        recipient, message = _message_key(decision.args)
        return any(
            prior_message == message and prior_recipient in (recipient, "all")
            and 0 <= turn.round - prior_round <= self.config.chat_cooldown and prior_state == state
            for (prior_recipient, prior_message), (prior_round, prior_state) in self.sent.items()
        )

    def _objection(self, decision: Decision, turn: TurnInput, own: dict, state: str) -> dict | None:
        def objection(code, reason):
            return {"code": code, "reason": reason}

        if (decision.tool == "eat_food" and own.get("max_hp", 0) > 0
                and own.get("hp") == own["max_hp"]):
            return objection("ineffective_action", "Your visible HP is already full; eating cannot heal you.")
        if decision.tool not in _available_tools(turn, own):
            return objection("invalid_action", f"{decision.tool} is unavailable in this setting or role.")
        if self._recent_duplicate(decision, turn, state):
            return objection("duplicate_chat", "You already sent this message to that recipient and visible state is unchanged.")
        if decision.tool == "move_to" and "position" in own:
            try:
                if [int(decision.args["x"]), int(decision.args["y"])] == own["position"]:
                    return objection("ineffective_action", "You are already at that coordinate.")
            except (KeyError, TypeError, ValueError):
                pass
        inventory = own.get("inventory")
        if inventory is not None and decision.tool in ("transfer_items", "eat_food", "equip_item", "sell_item"):
            item = decision.args.get("item")
            try:
                count = int(decision.args.get("count", 1)) if decision.tool in ("transfer_items", "sell_item") else 1
                if item is not None and (count < 1 or inventory.get(item, 0) < count):
                    return objection("invalid_action", f"Your inventory has {inventory.get(item, 0)} {item}; this action needs {count}.")
            except (TypeError, ValueError):
                return objection("invalid_action", "The item count must be an integer.")
        if decision.tool not in ("wait", "send_chat"):
            previous = self.ineffective.get(_action_key(decision))
            if (previous and 0 <= turn.round - previous[0] <= self.config.failure_cooldown
                    and previous[2] == state):
                return objection("ineffective_action", f"This action failed or did nothing with the same visible state: {previous[1][:160]}")
        return None

    def _feedback(self, turn: TurnInput, state: str) -> str:
        notes = [
            "Choose an executable step toward your assigned task using your own inventory and the recipes/instructions you know."
            " When a dependency is missing, identify its owner from your known context and ask for the specific item/count/location"
            " if chat is available. A chat promise is not a completed transfer; check the action result."
        ]
        recent = [record for record in self.ineffective.values()
                  if record[2] == state and 0 <= turn.round - record[0] <= self.config.failure_cooldown]
        for round_number, message, _ in sorted(recent, reverse=True)[:2]:
            notes.append(f"Recent unresolved result (R{round_number}): {message[:200]}")
        if self.wait_streak >= self.config.stall_threshold:
            action = ("Ask the responsible teammate once for the missing dependency, or do a ready local step."
                      if "send_chat" in turn.allowed_tools else
                      "Chat is disabled. Check for a ready local step; wait only if no useful action is available.")
            notes.append(f"You have waited {self.wait_streak} consecutive turns: {self.last_wait_reason[:120]}. {action}")
        return "\n\nLocal decision support:\n- " + "\n- ".join(notes)

    def act(self, turn: TurnInput) -> Decision:
        own, state = _own_state(turn), _fingerprint(turn)
        available = _available_tools(turn, own)
        observation = turn.observation + self._feedback(turn, state)
        first = self.inner.act(replace(turn, observation=observation, allowed_tools=available))
        objection = self._objection(first, turn, own, state)
        proposals = [{"tool": first.tool, "args": deepcopy(first.args), "objection": objection}]
        chosen, outcome, extra = first, "accepted", 0
        if objection:
            tag = objection["code"]
            rejected = first
            final_objection = objection
            if self.replans < self.config.max_replans:
                self.replans += 1
                extra = 1
                retry = self.inner.act(replace(
                    turn, allowed_tools=available, observation=observation
                    + "\n\nRevise your proposed " + first.tool + " " + json.dumps(first.args, ensure_ascii=False)
                    + ": " + objection["reason"] + " Choose a different useful tool call."
                ))
                final_objection = self._objection(retry, turn, own, state)
                proposals.append({"tool": retry.tool, "args": deepcopy(retry.args), "objection": final_objection})
                rejected = replace(retry, usage=_add_usage(first, retry))
            if final_objection:
                chosen = Decision("wait", {"reason": final_objection["reason"]}, reasoning=rejected.reasoning,
                                  note=f"{final_objection['code']}_suppressed", usage=rejected.usage)
                outcome = "suppressed"
            else:
                chosen = replace(rejected, note=",".join(filter(None, (rejected.note, f"{tag}_replanned"))))
                outcome = "replanned"
        chosen = replace(chosen, decision_trace={
            "policy_version": POLICY_VERSION, "proposals": proposals, "outcome": outcome,
            "extra_decisions": extra, "replans_remaining": self.config.max_replans - self.replans,
            "masked_tools": [tool for tool in turn.allowed_tools if tool not in available],
        })
        self._decision, self._round, self._state = chosen, turn.round, state
        return chosen

    def observe_result(self, result: ActionResult) -> None:
        self.inner.observe_result(result)
        decision = self._decision
        if decision is None:
            return
        if result.ok and decision.tool == "send_chat":
            delivered = result.effects.get("chat")
            args = {"to": delivered["to"], "message": delivered["text"]} if delivered else decision.args
            self.sent[_message_key(args)] = (self._round, self._state)
        if decision.tool not in ("wait", "send_chat"):
            key = _action_key(decision)
            if not result.ok or _no_progress(result.message):
                self.ineffective[key] = (self._round, result.message, self._state)
            else:
                self.ineffective.pop(key, None)
        if result.ok and decision.tool == "wait":
            self.wait_streak += 1
            self.last_wait_reason = str(decision.args.get("reason", ""))
        else:
            self.wait_streak, self.last_wait_reason = 0, ""

