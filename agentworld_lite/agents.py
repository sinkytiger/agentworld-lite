"""Agents: Claude tool-use agent (blackbox setting), scripted reference agent, random baseline."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import content as C
from .engine import ActionResult, World
from .llm import ClaudeClient
from .tools import tool_specs

SYSTEM_PROMPT = """You are an intelligent AI agent that plays the AgentWorld MMORPG game. Your goal is to explore, interact, collect resources, and engage with the game world intelligently while collaborating with your party to complete the task.

CRITICAL RESPONSE RULE: You MUST call exactly ONE tool function in every response. Never respond without calling a tool function.

How the game works:
- The game is turn-based. Each round every party member acts once, in a fixed order. One tool call is your whole turn.
- You only see your own status, what is near you, and the party chat. You cannot see teammates' inventories, plans, or reasoning. Coordinate through send_chat.
- The attack_entity function handles ALL aspects of combat automatically: movement, attack, and loot collection. harvest_resource, transfer_items, buy_item and sell_item also walk to their target if it is within 12 steps.
- move_to moves at most 12 tiles per call. Targets can be an entity id (e.g. tree#4) or a type name (the nearest one is used).
- Smithing requires standing within 2 tiles of the anvil; cooking requires a stove or a campfire within 2 tiles.
- To hand items to a teammate you must be within 2 tiles of them, so agree on a meeting point.
- At 0 HP you are knocked out for 3 rounds and respawn in the village. Eat food to heal.
- The round budget is limited: act efficiently, and do not send redundant messages."""

NUDGE = "You did not call a tool. You MUST call exactly ONE tool function now."


@dataclass
class TurnInput:
    world: World
    agent_name: str
    round: int
    observation: str
    context: str
    allowed_tools: list[str]


@dataclass
class Decision:
    tool: str
    args: dict
    reasoning: str = ""
    note: str = ""
    usage: dict = field(default_factory=dict)


class BaseAgent:
    kind = "base"

    def __init__(self, name: str) -> None:
        self.name = name

    def act(self, turn: TurnInput) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError

    def observe_result(self, result: ActionResult) -> None:
        pass


class LLMAgent(BaseAgent):
    """Stateless per turn: every turn rebuilds [task context | observation], as in the paper's
    'fresh observation each round' protocol. The task context block is prompt-cached."""

    kind = "llm"

    def __init__(self, name: str, client: ClaudeClient, strict_tools: bool = True) -> None:
        super().__init__(name)
        self.client = client
        self.strict_tools = strict_tools

    def act(self, turn: TurnInput) -> Decision:
        tools = tool_specs(turn.allowed_tools, strict=self.strict_tools)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": turn.context, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": turn.observation},
            ],
        }]
        usage_total: dict = {}
        for attempt in range(2):
            resp, usage = self.client.create(
                system=SYSTEM_PROMPT,
                tools=tools,
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                messages=messages,
            )
            for k, v in usage.items():
                usage_total[k] = usage_total.get(k, 0) + v
            if resp.stop_reason == "refusal":
                return Decision("wait", {"reason": "model refusal"}, note="refusal", usage=usage_total)
            text = " ".join(b.text for b in resp.content if b.type == "text").strip()
            call = next((b for b in resp.content if b.type == "tool_use"), None)
            if call is not None:
                return Decision(call.name, dict(call.input), reasoning=text, usage=usage_total,
                                note="" if attempt == 0 else "needed_nudge")
            messages = messages + [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": NUDGE},
            ]
        return Decision("wait", {"reason": "no tool call"}, reasoning=text, note="no_tool_call", usage=usage_total)


class ScriptedAgent(BaseAgent):
    """Replays a reference solution: a list of [tool, args, opts] steps.

    opts (all optional):
      retries: n                  - retry a failed step up to n times (e.g. waiting for a teammate)
      until_have: {item: n}       - repeat the step until the agent holds n of item; skipped if already true.
                                    For craft_item, `count` is recomputed to craft only what is still missing.
      until_near: [x, y, r]       - repeat (e.g. move_to) until within r tiles of (x, y); skipped if already true.
    """

    kind = "scripted"

    def __init__(self, name: str, steps: list, default_retries: int = 6) -> None:
        super().__init__(name)
        self.steps = steps or []
        self.idx = 0
        self.fails = 0
        self.default_retries = default_retries
        self._world = None

    @staticmethod
    def _opts(step) -> dict:
        return (step[2] or {}) if len(step) > 2 else {}

    def _done(self, step) -> bool:
        if self._world is None:
            return False
        me = self._world.agents[self.name]
        opts = self._opts(step)
        if "until_have" in opts:
            have = lambda item: me.inventory[item] + (1 if item in me.equipment.values() else 0)
            return all(have(i) >= n for i, n in opts["until_have"].items())
        if "until_near" in opts:
            x, y, r = opts["until_near"]
            return max(abs(me.x - x), abs(me.y - y)) <= r
        return False

    def act(self, turn: TurnInput) -> Decision:
        self._world = turn.world
        while self.idx < len(self.steps) and self._done(self.steps[self.idx]):
            self.idx += 1
            self.fails = 0
        if self.idx >= len(self.steps):
            return Decision("wait", {"reason": "script finished"})
        step = self.steps[self.idx]
        args = dict(step[1]) if len(step) > 1 and step[1] else {}
        opts = self._opts(step)
        if step[0] == "craft_item" and "until_have" in opts:
            from .content import RECIPES
            item = args["item_key"]
            missing = opts["until_have"][item] - turn.world.agents[self.name].inventory[item]
            args["count"] = max(1, -(-missing // RECIPES[item]["output"]))
        return Decision(step[0], args)

    def observe_result(self, result: ActionResult) -> None:
        if self.idx >= len(self.steps):
            return
        step = self.steps[self.idx]
        opts = self._opts(step)
        retries = opts.get("retries", self.default_retries)
        conditional = "until_have" in opts or "until_near" in opts
        if conditional and self._done(step):
            self.idx, self.fails = self.idx + 1, 0
        elif result.ok and not conditional:
            self.idx, self.fails = self.idx + 1, 0
        elif self.fails >= retries:
            self.idx, self.fails = self.idx + 1, 0
        elif not result.ok or conditional:
            self.fails += 1


class RandomAgent(BaseAgent):
    """Uniform random tool with plausible arguments (the paper's 'random actions' baseline)."""

    kind = "random"

    def __init__(self, name: str, seed: int = 0) -> None:
        super().__init__(name)
        self.rng = random.Random(f"{seed}-{name}")

    def act(self, turn: TurnInput) -> Decision:
        w, me, rng = turn.world, turn.world.agents[self.name], self.rng
        tool = rng.choice(turn.allowed_tools)
        near = w.entities_near(me.pos, me.view_radius)

        def pick(kind, fallback):
            es = [e for e in near if e.kind == kind]
            return rng.choice(es).id if es else fallback

        inv_items = [k for k, v in me.inventory.items() if v > 0] or ["apple"]
        mates = [n for n in w.agents if n != self.name]
        if tool == "move_to":
            args = {"x": max(0, min(63, me.x + rng.randint(-12, 12))), "y": max(0, min(47, me.y + rng.randint(-12, 12)))}
        elif tool == "attack_entity":
            args = {"target": pick("mob", rng.choice(list(C.MOBS)))}
        elif tool == "harvest_resource":
            args = {"target": pick("resource", rng.choice(list(C.RESOURCES)))}
        elif tool == "craft_item":
            item = rng.choice(list(C.RECIPES))
            args = {"skill": C.RECIPES[item]["skill"], "item_key": item, "count": 1}
        elif tool == "transfer_items":
            args = {"to_agent": rng.choice(mates), "item": rng.choice(inv_items), "count": 1}
        elif tool == "send_chat":
            args = {"to": "all", "message": rng.choice(["hello", "where are you?", "I am busy", "ready"])}
        elif tool in ("eat_food", "equip_item"):
            args = {"item": rng.choice(inv_items)}
        elif tool in ("buy_item", "sell_item"):
            npc = pick("npc", rng.choice(list(C.SHOPS)))
            args = {"npc": npc, "item": rng.choice(inv_items if tool == "sell_item" else list(C.ITEMS)), "count": 1}
        elif tool == "build_structure":
            args = {"structure": rng.choice(list(C.STRUCTURES))}
        elif tool == "wait":
            args = {"reason": "random"}
        else:
            args = {}
        return Decision(tool, args)
