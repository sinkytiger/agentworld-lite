"""Communication failure taxonomy (paper §6): label chat messages with an LLM judge.

Paper categories and their share of flagged messages:
  stale_redundant (37.7%), misidentification (16.4%), factual_error (14.8%),
  premature_completion (11.5%), non_material (11.5%), logical_inconsistency (8.2%).
Each message is judged against the ground-truth game state at the time it was sent.
"""

from __future__ import annotations

import json

from .llm import ClaudeClient

CATEGORIES = {
    "stale_redundant": "Refers to work already completed, repeats information already shared, or asks a question that was already resolved.",
    "misidentification": "Wrong self-role label, addresses/requests something from the wrong teammate, or confuses who holds what.",
    "factual_error": "States incorrect item names, coordinates, quantities, or falsely reports readiness/possession.",
    "premature_completion": "Declares the task or a sub-goal finished before it actually is.",
    "non_material": "Idle chatter or pleasantries with no actionable coordination content.",
    "logical_inconsistency": "Proposes an invalid strategy, contradicts own earlier plan, or issues contradictory commands.",
    "none": "A useful, accurate, timely coordination message.",
}

SYSTEM = "You audit chat messages from a multi-agent game for coordination failures. Judge strictly against the ground-truth state provided."

PROMPT = """TASK: {task_name}
OBJECTIVE: {objective}
TEAM: {team}

FULL ACTION LOG (id | agent: tool call -> result). Chat messages are send_chat actions:
{log}

Classify each of the following chat messages. Use the ground-truth state at send time shown under each message.
Categories:
{cats}

MESSAGES:
{messages}

Return one label per message id. Pick the single most fitting category ("none" if the message is fine)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_id": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "explanation": {"type": "string"},
                },
                "required": ["action_id", "category", "explanation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}


def _state_at(traj: dict, rnd: int, sender: str) -> str:
    snap = traj["snapshots"][min(rnd - 1, len(traj["snapshots"]) - 1)]  # state at the start of that round
    me = snap["agents"][sender]
    cps = ", ".join(f"{c['name']} {c['have']}/{c['need']}" for c in snap["checkpoints"])
    others = "; ".join(f"{n}@{tuple(s['pos'])} inv={s['inventory']}" for n, s in snap["agents"].items() if n != sender)
    return f"checkpoints: {cps} | sender@{tuple(me['pos'])} inv={me['inventory']} | others: {others}"


def classify_failures(traj: dict, client: ClaudeClient, batch: int = 20) -> dict:
    chats = [a for a in traj["actions"] if a["tool"] == "send_chat" and a["ok"]]
    out = {"labels": [], "counts": {k: 0 for k in CATEGORIES}}
    if not chats:
        return out
    log = "\n".join(f"{a['id']} | {a['description'][:220]}" for a in traj["actions"])
    team = ", ".join(f"{a['username']} ({a['role']})" for a in traj["agents"])
    cats = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    for i in range(0, len(chats), batch):
        chunk = chats[i:i + batch]
        msgs = "\n".join(
            f"{a['id']} [{a['agent']} -> {a['args'].get('to')}] {json.dumps(a['args'].get('message', ''), ensure_ascii=False)}\n"
            f"    state: {_state_at(traj, a['round'], a['agent'])}"
            for a in chunk
        )
        data, _ = client.json_call(SYSTEM, PROMPT.format(
            task_name=traj["task_name"], objective=traj["objective"], team=team, log=log, cats=cats, messages=msgs), SCHEMA)
        valid = {a["id"] for a in chunk}
        for lab in data["labels"]:
            if lab["action_id"] in valid:
                out["labels"].append(lab)
                out["counts"][lab["category"]] += 1
    return out
