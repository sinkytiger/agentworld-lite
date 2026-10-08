"""Causal Collaboration Effectiveness (CCE) and Per-Agent Contribution (PAC).

Paper definition:
    T  = every action taken by every agent (tool calls AND chat messages)
    S  = success actions: the final action(s) that directly achieved the objective
    C  = S ∪ ancestors(S) in the causal action graph
    CCE   = |C| / |T|
    PAC_i = |C ∩ T_i| / |T_i|
The graph is built by iterative backward tracing: identify S, then sweep backward round by
round, asking an LLM which actions in that round causally ENABLED an already-contributing
action (inclusive labelling, so CCE is an upper bound on genuinely useful effort).

A failed episode has no success action, so C = ∅ and CCE = 0. We report both the mean over
all episodes and the mean over successful episodes only.

Two judges:
  LLMJudge  - the paper's procedure (two prompt stages, one call per round).
  RuleJudge - deterministic approximation from logged effects (item provenance, movement
              chains, damage on the same target, chat-to-action token overlap). Free to run;
              useful as a sanity check and for judge-agreement analysis.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque

from . import content as C
from .llm import ClaudeClient

PRODUCTIVE_TOOLS = {"attack_entity", "harvest_resource", "craft_item", "transfer_items", "eat_food",
                    "equip_item", "buy_item", "sell_item", "build_structure"}


# ---------------------------------------------------------------------- graph utilities
def ancestors(targets: set[str], edges: list[tuple[str, str]]) -> set[str]:
    parents: dict[str, set[str]] = defaultdict(set)
    for src, dst in edges:
        parents[dst].add(src)
    seen: set[str] = set()
    q = deque(targets)
    while q:
        n = q.popleft()
        for p in parents[n]:
            if p not in seen and p not in targets:
                seen.add(p)
                q.append(p)
    return seen


def summarize(traj: dict, success_ids: set[str], contributing: set[str], edges: list, judge: str) -> dict:
    actions = traj["actions"]
    by_agent: dict[str, list[str]] = defaultdict(list)
    for a in actions:
        by_agent[a["agent"]].append(a["id"])
    n = len(actions)
    pac = {ag: (len(contributing & set(ids)) / len(ids) if ids else 0.0) for ag, ids in by_agent.items()}
    for ag in (x["username"] for x in traj["agents"]):
        pac.setdefault(ag, 0.0)
    order = {a["id"]: i for i, a in enumerate(actions)}
    return {
        "judge": judge,
        "cce": len(contributing) / n if n else 0.0,
        "n_actions": n,
        "n_contributing": len(contributing),
        "success_ids": sorted(success_ids, key=order.get),
        "contributing_ids": sorted(contributing, key=order.get),
        "edges": [list(e) for e in edges if e[0] in contributing and e[1] in contributing],
        "pac": pac,
    }


def empty_result(traj: dict, judge: str) -> dict:
    return summarize(traj, set(), set(), [], judge)


# ---------------------------------------------------------------------- rule-based judge
_COORD = re.compile(r"\(?\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)?")
_VOCAB = set(C.ITEMS) | set(C.RESOURCES) | set(C.MOBS) | set(C.SHOPS) | set(C.STRUCTURES)


def _tokens(text: str, agent_names: set[str]) -> set[str]:
    text = text.lower()
    toks = {w for w in re.findall(r"[a-z_#0-9]+", text) if w in _VOCAB or w in agent_names or "#" in w or "_" in w}
    toks |= {f"{x},{y}" for x, y in _COORD.findall(text)}
    return toks


def _action_tokens(a: dict, agent_names: set[str]) -> set[str]:
    vals = " ".join(str(v) for v in (a.get("args") or {}).values())
    toks = _tokens(vals, agent_names)
    args = a.get("args") or {}
    if "x" in args and "y" in args:
        toks.add(f"{args['x']},{args['y']}")
    toks.add(a["agent"].lower())
    return toks


def _overlap(chat_toks: set[str], act_toks: set[str], coord_slack: int = 3) -> bool:
    if chat_toks & act_toks:
        return True
    def coords(ts):
        return [tuple(map(int, t.split(","))) for t in ts if re.fullmatch(r"\d+,\d+", t)]
    return any(max(abs(x1 - x2), abs(y1 - y2)) <= coord_slack
               for x1, y1 in coords(chat_toks) for x2, y2 in coords(act_toks))


class RuleJudge:
    name = "rule"

    def success_actions(self, traj: dict) -> set[str]:
        """Actions that raised a checkpoint's progress in its final non-decreasing run
        (e.g. each agent's arrival for 'all agents near X', each delivery for 'hold 8 logs').
        For tasks with an all_alive invariant, healing and killing aggressive mobs are added as
        maintenance actions, since survival is achieved by preventing damage rather than one final act."""
        acts = traj["actions"]
        if not acts:
            return set()
        out: set[str] = set()
        initial = [c["have"] for c in traj["snapshots"][0]["checkpoints"]]
        for j in range(len(initial)):
            prev, run = initial[j], []
            for a in acts:
                cur = a["cp_have"][j]
                if cur < prev:
                    run = []
                elif cur > prev:
                    run.append(a["id"])
                prev = cur
            out.update(run)
        if "all_alive" in traj.get("checkpoint_types", []):
            for a in acts:
                eff = a.get("effects") or {}
                if a["ok"] and (a["tool"] == "eat_food" or eff.get("killed_aggressive")):
                    out.add(a["id"])
        if not out:
            ok = [a for a in acts if a["ok"] and a["tool"] in PRODUCTIVE_TOOLS | {"move_to"}]
            if ok:
                out.add(ok[-1]["id"])
        return out

    def edges(self, traj: dict) -> list[tuple[str, str]]:
        acts = traj["actions"]
        names = {a["username"] for a in traj["agents"]}
        lower_names = {n.lower() for n in names}
        edges: list[tuple[str, str]] = []
        lots: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
        for ag, st in traj["snapshots"][0]["agents"].items():
            for item, n in st["inventory"].items():
                lots[ag][item].append([None, n])
        pending_moves: dict[str, list[str]] = defaultdict(list)
        last_eat: dict[str, str] = {}
        hits: dict[str, list[str]] = defaultdict(list)
        builds_fire: list[str] = []
        open_chats: list[tuple[str, set[str], set[str]]] = []  # (chat id, recipients, tokens)

        for a in acts:
            aid, ag, tool = a["id"], a["agent"], a["tool"]
            if not a["ok"]:
                continue
            eff = a.get("effects") or {}
            # item provenance: consumption links to producers, production creates new lots
            for who, delta in sorted((eff.get("inv") or {}).items(), key=lambda kv: sum(kv[1].values())):
                for item, d in delta.items():
                    if d < 0:
                        need = -d
                        q = lots[who][item]
                        while need > 0 and q:
                            prod, qty = q[0]
                            take = min(qty, need)
                            if prod and prod != aid:
                                edges.append((prod, aid))
                            need -= take
                            q[0][1] -= take
                            if q[0][1] <= 0:
                                q.popleft()
            for who, delta in (eff.get("inv") or {}).items():
                for item, d in delta.items():
                    if d > 0:
                        lots[who][item].append([aid, d])
            # movement chains: moves enable the agent's next productive action / next move
            if tool == "move_to":
                for m in pending_moves[ag]:
                    edges.append((m, aid))
                pending_moves[ag] = [aid]
            elif tool in PRODUCTIVE_TOOLS:
                for m in pending_moves[ag]:
                    edges.append((m, aid))
                pending_moves[ag] = []
            # healing enables the agent's next fight
            if tool == "eat_food":
                last_eat[ag] = aid
            if tool == "attack_entity":
                if ag in last_eat:
                    edges.append((last_eat.pop(ag), aid))
                for target in (eff.get("damage_dealt") or {}):
                    for prev in hits[target]:
                        edges.append((prev, aid))
                    hits[target].append(aid)
                    if eff.get("killed") == target:
                        hits[target] = []
            # campfires enable later cooking
            if tool == "build_structure" and str((a.get("args") or {}).get("structure")) == "campfire":
                builds_fire.append(aid)
            if tool == "craft_item" and C.RECIPES.get(str((a.get("args") or {}).get("item_key")), {}).get("station") == "fire":
                for b in builds_fire:
                    edges.append((b, aid))
            # chat -> first later action of a recipient that shares a concrete token with the message
            if tool == "send_chat":
                chat = eff.get("chat") or {}
                rec = {n for n in names if n != ag} if chat.get("to") == "all" else {chat.get("to")}
                open_chats.append((aid, rec, _tokens(chat.get("text", ""), lower_names)))
            elif tool in PRODUCTIVE_TOOLS | {"move_to"}:
                toks = _action_tokens(a, lower_names)
                still = []
                for cid, rec, ctoks in open_chats:
                    if ag in rec and _overlap(ctoks, toks):
                        edges.append((cid, aid))
                        rec = rec - {ag}
                    if rec:
                        still.append((cid, rec, ctoks))
                open_chats = still
        return edges

    def compute(self, traj: dict) -> dict:
        if not traj["result"]["success"]:
            return empty_result(traj, self.name)
        s = self.success_actions(traj)
        e = self.edges(traj)
        contributing = s | ancestors(s, e)
        return summarize(traj, s, contributing, e, self.name)


# ---------------------------------------------------------------------- LLM judge (paper procedure)
# Prompt wording follows the backward-tracing procedure described in AgentWorld (Shu et al., 2026, arXiv:2609.31590).
JUDGE_SYSTEM = "You are a careful analyst of multi-agent game trajectories. Answer only from the log provided."

SUCCESS_PROMPT = """You are analyzing a multi-agent collaboration task.
TASK: {task_name}
OBJECTIVE: {objective}
FINAL VERIFIER RESULT: {verifier}

ACTION LOG (one line per action: id | agent: tool call -> result):
{log}

Which actions are the FINAL actions that directly accomplished the objective? Do NOT include enabling or
prerequisite actions. Only the very last action(s) that fulfill the objective.
Respond with JSON: {{"success_action_ids": [...], "reasoning": "..."}}"""

TRACE_PROMPT = """You are tracing causal dependencies in a multi-agent collaboration task.
TASK: {task_name}
OBJECTIVE: {objective}

Actions already identified as CONTRIBUTING to the task's success:
{contributing}

Now consider the actions from Round {R} (listed in turn order):
{candidates}

For each action in Round {R}, does it causally ENABLE any of the contributing actions above, or a later
action in Round {R} that you also mark as contributing? An action contributes if:
- It produces resources a later action uses
- It moves the agent to a needed location
- It communicates info that helps coordinate
- It is a prerequisite step
Be INCLUSIVE: if an action is even somewhat helpful, mark it as contributing.
Return one judgment per Round {R} action. For contributing actions, list the ids of the actions it enables."""

SUCCESS_SCHEMA = {
    "type": "object",
    "properties": {
        "success_action_ids": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["success_action_ids", "reasoning"],
    "additionalProperties": False,
}
TRACE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_id": {"type": "string"},
                    "contributes": {"type": "boolean"},
                    "enables": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["action_id", "contributes", "enables", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["judgments"],
    "additionalProperties": False,
}


def _line(a: dict, max_len: int = 260) -> str:
    return f"{a['id']} | {a['description'][:max_len]}"


class LLMJudge:
    name = "llm"

    def __init__(self, client: ClaudeClient) -> None:
        self.client = client
        self.calls: list[dict] = []

    def _ask(self, prompt: str, schema: dict) -> dict:
        data, usage = self.client.json_call(JUDGE_SYSTEM, prompt, schema)
        self.calls.append(usage)
        return data

    def compute(self, traj: dict) -> dict:
        if not traj["result"]["success"]:
            return empty_result(traj, self.name)
        acts = traj["actions"]
        by_id = {a["id"]: a for a in acts}
        order = {a["id"]: i for i, a in enumerate(acts)}
        head = {"task_name": traj["task_name"], "objective": traj["objective"]}

        data = self._ask(SUCCESS_PROMPT.format(
            **head, verifier=traj["result"]["message"], log="\n".join(_line(a) for a in acts)), SUCCESS_SCHEMA)
        success = {i for i in data["success_action_ids"] if i in by_id}
        if not success:  # fall back to the action after which the verifier passed
            success = {acts[-1]["id"]}
        contributing = set(success)
        edges: list[tuple[str, str]] = []
        last_round = max(by_id[i]["round"] for i in success)
        for r in range(last_round, 0, -1):
            cands = [a for a in acts if a["round"] == r and a["id"] not in contributing]
            if not cands:
                continue
            earliest = min(order[c["id"]] for c in cands)
            later = [by_id[i] for i in sorted(contributing, key=order.get) if order[i] > earliest]
            if not later:
                continue
            data = self._ask(TRACE_PROMPT.format(
                **head, R=r,
                contributing="\n".join(_line(a) for a in later),
                candidates="\n".join(_line(a) for a in cands)), TRACE_SCHEMA)
            cand_ids = {c["id"] for c in cands}
            for j in data["judgments"]:
                aid = j["action_id"]
                if aid not in cand_ids or not j["contributes"]:
                    continue
                contributing.add(aid)
                for dst in j["enables"]:
                    if dst in by_id and order[dst] > order[aid]:
                        edges.append((aid, dst))
        return summarize(traj, success, contributing, edges, self.name)


# ---------------------------------------------------------------------- judge agreement
def agreement(traj: dict, a: dict, b: dict) -> dict:
    """Raw agreement and Cohen's kappa between two judges' contributing labels over T."""
    ids = [x["id"] for x in traj["actions"]]
    if not ids:
        return {"n": 0, "raw": None, "kappa": None}
    sa, sb = set(a["contributing_ids"]), set(b["contributing_ids"])
    la = [i in sa for i in ids]
    lb = [i in sb for i in ids]
    n = len(ids)
    po = sum(x == y for x, y in zip(la, lb)) / n
    pa, pb = sum(la) / n, sum(lb) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return {"n": n, "raw": po, "kappa": kappa}


def to_mermaid(traj: dict, res: dict, max_label: int = 48) -> str:
    """Causal action graph (contributing actions only) as a Mermaid flowchart."""
    by_id = {a["id"]: a for a in traj["actions"]}
    lines = ["flowchart LR"]
    for i in res["contributing_ids"]:
        a = by_id[i]
        label = f"{i} {a['agent']}<br/>{a['tool']}".replace('"', "'")[:max_label * 2]
        shape = f'{i.replace(".", "_")}(["{label}"])' if i in res["success_ids"] else f'{i.replace(".", "_")}["{label}"]'
        lines.append(f"  {shape}")
    for src, dst in res["edges"]:
        lines.append(f"  {src.replace('.', '_')} --> {dst.replace('.', '_')}")
    return "\n".join(lines)
