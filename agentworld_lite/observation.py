"""Structured text observation given to each agent on its turn."""

from __future__ import annotations

from .engine import World
from .gamemap import biome_at, chebyshev

MAX_PER_KIND = 10
CHAT_WINDOW = 20
HISTORY_WINDOW = 8


def _fmt_counter(d: dict) -> str:
    items = [f"{k} x{v}" for k, v in sorted(d.items()) if v]
    return ", ".join(items) if items else "(empty)"


def render_observation(
    world: World,
    agent_name: str,
    *,
    max_rounds: int,
    history: list[dict],
    chat_log: list[dict],
    seen_chat: int,
    whitebox: bool = False,
    last_actions: dict[str, str] | None = None,
) -> str:
    a = world.agents[agent_name]
    lines = [f"=== Round {world.round} / {max_rounds} ==="]
    lines.append(f"You: {a.name} (role: {a.role}) at ({a.x},{a.y}) [{biome_at(*a.pos)}]  HP {a.hp}/{a.max_hp}")
    skills = ", ".join(f"{k} {v}" for k, v in sorted(a.skills.items()))
    lines.append(f"Skills: {skills} (unlisted skills are level 1)")
    equip = ", ".join(f"{s}={i}" for s, i in sorted(a.equipment.items())) or "(none)"
    lines.append(f"Equipment: {equip}")
    lines.append(f"Inventory: {_fmt_counter(a.inventory)}")
    if a.restricted_tools:
        lines.append(f"Your role CANNOT use: {', '.join(sorted(a.restricted_tools))}")

    lines.append("")
    lines.append(f"Nearby (view radius {a.view_radius}):")
    groups = {"resource": [], "mob": [], "npc": [], "station": [], "structure": [], "landmark": [], "plate": []}
    for e in world.entities_near(a.pos, a.view_radius, viewer=a):
        groups.setdefault(e.kind, []).append(e)
    any_seen = False
    for kind, es in groups.items():
        if not es:
            continue
        any_seen = True
        shown = ", ".join(f"{e.label()} d={chebyshev(a.pos, e.pos)}" for e in es[:MAX_PER_KIND])
        more = f" (+{len(es) - MAX_PER_KIND} more)" if len(es) > MAX_PER_KIND else ""
        lines.append(f"  {kind}s: {shown}{more}")
    mates = [o for o in world.agents.values() if o is not a and chebyshev(a.pos, o.pos) <= a.view_radius]
    if mates:
        any_seen = True
        lines.append("  teammates: " + ", ".join(
            f"{o.name} at ({o.x},{o.y}) d={chebyshev(a.pos, o.pos)}" + (" [knocked out]" if o.knocked_out else "")
            for o in mates))
    if not any_seen:
        lines.append("  (nothing of note)")

    if whitebox:
        lines.append("")
        lines.append("Team status (oracle view of every teammate):")
        for o in world.agents.values():
            if o is a:
                continue
            last = (last_actions or {}).get(o.name, "-")
            lines.append(f"  {o.name} ({o.role}) at ({o.x},{o.y}) HP {o.hp}/{o.max_hp} | inv: {_fmt_counter(o.inventory)} | last: {last}")

    lines.append("")
    if chat_log:
        window = chat_log[-CHAT_WINDOW:]
        start = len(chat_log) - len(window)
        lines.append(f"Chat log (latest {len(window)} of {len(chat_log)}; * = new since your last turn):")
        for i, m in enumerate(window, start=start):
            mark = "*" if i >= seen_chat and m["from"] != agent_name else " "
            lines.append(f" {mark}[R{m['round']}] {m['from']} -> {m['to']}: {m['text']}")
    else:
        lines.append("Chat log: (no messages yet)")

    lines.append("")
    if history:
        lines.append("Your recent actions:")
        for h in history[-HISTORY_WINDOW:]:
            status = "OK" if h["ok"] else "FAILED"
            lines.append(f"  [R{h['round']}] {h['call']} -> {status}: {h['message']}")
    else:
        lines.append("Your recent actions: (none yet)")
    lines.append("")
    lines.append("Choose exactly ONE tool call for this turn.")
    return "\n".join(lines)
