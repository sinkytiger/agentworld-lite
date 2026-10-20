"""Offline, descriptive diagnostics for executed actions and controller decisions.

These counts describe a recorded run, not collaboration quality or causal effects.
Missing controller traces and model usage are explicitly reported as unavailable.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict


def _cell(value: object, limit: int = 140) -> str:
    text = " ".join(str(value).split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text.replace("|", "\\|").replace("`", "'").replace("<", "&lt;")


def _number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _chat_key(action: dict) -> tuple[str, str, str]:
    args = action.get("args") or {}
    chat = (action.get("effects") or {}).get("chat") or {}
    recipient = chat.get("to", args.get("to", "all"))
    message = chat.get("text", args.get("message", ""))
    return (str(action.get("agent", "?")), str(recipient).strip().casefold(),
            " ".join(str(message).split()).casefold())


def _call(action: dict) -> str:
    args = json.dumps(action.get("args") or {}, ensure_ascii=False, sort_keys=True, default=str)
    return f"{action.get('tool', '?')} {args}"


def _trace_counts(actions: list[dict]) -> tuple[str, str, int]:
    traces = [a.get("decision_trace") for a in actions]
    valid = [t for t in traces if isinstance(t, dict)
             and _number(t.get("extra_decisions"))
             and t.get("outcome") in ("accepted", "replanned", "suppressed")]
    replans = sum(t["extra_decisions"] for t in valid)
    suppressed = sum(t["outcome"] == "suppressed" for t in valid)
    if len(valid) != len(actions):
        return "N/A", "N/A", len(valid)
    return str(replans), str(suppressed), len(valid)


def _calls(actions: list[dict]) -> str:
    counts = []
    for action in actions:
        usage = action.get("usage")
        count = action.get("model_calls")
        if count is None and isinstance(usage, dict):
            count = usage.get("calls")
        if not _number(count):
            return "N/A"
        counts.append(count)
    return str(sum(counts))


def _row(label: str, actions: list[dict], calls: str, success: str = "-") -> str:
    chats = [a for a in actions if a.get("tool") == "send_chat" and a.get("ok") is True]
    duplicate_count = len(chats) - len({_chat_key(a) for a in chats})
    replans, suppressed, traced = _trace_counts(actions)
    values = [label, success, len(actions), sum(a.get("ok") is False for a in actions),
              sum(a.get("tool") == "wait" for a in actions), len(chats), duplicate_count,
              calls, replans, suppressed, f"{traced}/{len(actions)}"]
    return "| " + " | ".join(_cell(v) for v in values) + " |"


def _examples(actions: list[dict]) -> list[str]:
    """At most three concrete symptoms; none presumes a wait is unnecessary."""
    examples = []
    failed: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for action in actions:
        if action.get("ok") is False:
            failed[(_call(action), str(action.get("message", "")))].append(action)
    if failed:
        (call, message), group = max(failed.items(), key=lambda item: len(item[1]))
        examples.append(f"Failure: {_cell(call, 100)} x{len(group)}; "
                        f"{_cell(message, 130)} (first {_cell(group[0].get('id', '?'))}).")

    chats = [a for a in actions if a.get("tool") == "send_chat" and a.get("ok") is True]
    chat_counts = Counter(_chat_key(a) for a in chats)
    if chat_counts and chat_counts.most_common(1)[0][1] > 1:
        (_, recipient, message), count = chat_counts.most_common(1)[0]
        examples.append(f"Repeated chat: to {_cell(recipient)}, '{_cell(message, 120)}' "
                        f"sent {count} times ({count - 1} duplicates).")
    else:
        calls = Counter(_call(a) for a in actions if a.get("tool") not in ("wait", "send_chat"))
        if calls and calls.most_common(1)[0][1] > 1:
            call, count = calls.most_common(1)[0]
            examples.append(f"Repeated action: {_cell(call, 130)} x{count}; repetition may be required.")

    best: list[dict] = []
    streak: list[dict] = []
    for action in actions:
        if action.get("tool") == "wait" and action.get("ok") is True:
            streak.append(action)
            if len(streak) > len(best):
                best = list(streak)
        else:
            streak = []
    if len(best) >= 3:
        examples.append(f"Wait streak: {len(best)} own turns, "
                        f"{_cell(best[0].get('id', '?'))} to {_cell(best[-1].get('id', '?'))}; "
                        f"last reason: {_cell((best[-1].get('args') or {}).get('reason', '(none)'), 110)}.")
    if not examples:
        for action in actions:
            trace = action.get("decision_trace") or {}
            if trace.get("outcome") in ("replanned", "suppressed"):
                proposals = trace.get("proposals") or []
                objections = [p.get("objection") for p in proposals if isinstance(p, dict) and p.get("objection")]
                if objections:
                    objection = objections[0]
                    examples.append(f"Controller {trace['outcome']} at {_cell(action.get('id', '?'))}: "
                                    f"{_cell(objection.get('reason', objection.get('code', '?')), 160)}.")
                    break
    return examples[:3]


def diagnose_run(trajs: list[dict]) -> str:
    """Return a Markdown report from loaded trajectories; no model/API calls.

    New action records may contain ``usage.calls`` (or ``model_calls``) and
    ``decision_trace``. Old runs remain readable, with unavailable counts marked
    N/A. Replans count extra decisions, including attempts ending in suppression.
    """
    lines = ["# Agent run diagnostics", "",
             "Counts describe executed actions. Failed = engine `ok=false`; waits are shown separately. "
             "Fewer failures alone do not demonstrate progress or higher success.", "",
             "Duplicate chats count successful sends after the first with the same sender, recipient, "
             "and text (case and whitespace normalized). This is an exact-text diagnostic, not a semantic judgment.", "",
             "Replans count extra controller decisions, including attempts ending in suppression. "
             "Trace coverage is recorded actions with a complete controller outcome / all actions. "
             "N/A means the required records are missing; zero means a known zero. "
             "Success is an episode outcome, not an individual agent score.", ""]
    if not trajs:
        return "\n".join(lines + ["No trajectories found.", ""])
    for traj in trajs:
        actions = traj.get("actions") or []
        result = traj.get("result") or {}
        meta = traj.get("meta") or {}
        success = "yes" if result.get("success") is True else "no" if result.get("success") is False else "N/A"
        title = f"{traj.get('task_id', '?')} / {traj.get('setting', '?')} / seed {traj.get('seed', '?')}"
        lines.extend([f"## {_cell(title, 220)}", "",
                      f"Model: {_cell(meta.get('model') or meta.get('agent') or 'N/A')}; "
                      f"coordination: {_cell(meta.get('coordination', 'N/A'))}; "
                      f"rounds: {_cell(result.get('rounds_used', 'N/A'))}/{_cell(traj.get('max_rounds', '?'))}.", "",
                      "| Scope | Success | Actions | Failed | Waits | Chats | Duplicates | Calls | Replans | Suppressed | Trace |",
                      "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
        total_calls = (traj.get("usage") or {}).get("calls")
        lines.append(_row("Episode", actions, str(total_calls) if _number(total_calls) else _calls(actions), success))
        by_agent: dict[str, list[dict]] = {str(a.get("username", "?")): [] for a in (traj.get("agents") or [])}
        for action in actions:
            by_agent.setdefault(str(action.get("agent", "?")), []).append(action)
        for agent, own_actions in by_agent.items():
            lines.append(_row(agent, own_actions, _calls(own_actions)))
        lines.append("")
        missing_trace = [a for a in actions if not isinstance(a.get("decision_trace"), dict)]
        legacy = Counter()
        for action in missing_trace:
            note = str(action.get("note", ""))
            for tag in note.split(","):
                if tag.strip().endswith(("_replanned", "_suppressed")):
                    legacy[tag.strip()] += 1
        if legacy:
            notes = ", ".join(f"{_cell(tag)} x{count}" for tag, count in sorted(legacy.items()))
            lines.extend([f"Legacy controller notes: {notes}. These do not supply complete decision or call counts.", ""])
        for agent, own_actions in by_agent.items():
            for example in _examples(own_actions):
                lines.append(f"- **{_cell(agent)}**: {example}")
        if not actions:
            lines.append("No executed actions were recorded.")
        lines.append("")
    return "\n".join(lines)
