"""Tool definitions (Claude tool-use format) for the 13 high-level API tools.

The paper exposes 13 high-level tools (move, attack, harvest, craft, transfer, chat, ...)
but does not list all of them. This set is a reconstruction that covers every task
category: movement/exploration, combat, gathering, crafting, trading, construction,
survival, and coordination.
"""

from __future__ import annotations

from .engine import TOOL_NAMES


def _schema(props: dict) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOL_DEFS: dict[str, dict] = {
    "move_to": {
        "description": "Walk toward tile (x, y) along a shortest path. Moves at most 12 tiles per call; call again to continue.",
        "input_schema": _schema({"x": _INT, "y": _INT}),
    },
    "attack_entity": {
        "description": ("Fight a mob. Handles everything: walks to it (if within 12 steps), exchanges up to 4 hits, "
                        "auto-disengages before a lethal hit, and collects loot if it dies. target = mob id (e.g. 'goblin#2') or mob type."),
        "input_schema": _schema({"target": _STR}),
    },
    "harvest_resource": {
        "description": ("Harvest one unit from a resource node (walks to it if within 12 steps). Requires the node's skill level and tool "
                        "(axe for trees, pickaxe for rocks, fishing_rod for fishing spots). target = node id (e.g. 'tree#4') or node type."),
        "input_schema": _schema({"target": _STR}),
    },
    "craft_item": {
        "description": ("Craft an item from a recipe in the task context. skill must match the recipe skill (e.g. fletching for stick). "
                        "count = number of crafts (each consumes one set of inputs). Smithing needs the anvil, cooking needs a stove or campfire."),
        "input_schema": _schema({"skill": _STR, "item_key": _STR, "count": _INT}),
    },
    "transfer_items": {
        "description": "Hand items to a teammate. You must be within 2 tiles of them (auto-walks if they are within 12 steps).",
        "input_schema": _schema({"to_agent": _STR, "item": _STR, "count": _INT}),
    },
    "send_chat": {
        "description": "Send a chat message to the party ('all') or to one teammate username. This is the ONLY way to share information with teammates.",
        "input_schema": _schema({"to": _STR, "message": _STR}),
    },
    "eat_food": {
        "description": "Eat a food or potion item from your inventory to restore HP.",
        "input_schema": _schema({"item": _STR}),
    },
    "equip_item": {
        "description": "Equip a weapon or armor item from your inventory.",
        "input_schema": _schema({"item": _STR}),
    },
    "buy_item": {
        "description": "Buy items from an NPC shop (walks to it if within 12 steps). npc = NPC id or type (general_store, blacksmith, trader).",
        "input_schema": _schema({"npc": _STR, "item": _STR, "count": _INT}),
    },
    "sell_item": {
        "description": "Sell items to an NPC shop for coins (walks to it if within 12 steps).",
        "input_schema": _schema({"npc": _STR, "item": _STR, "count": _INT}),
    },
    "build_structure": {
        "description": "Build a structure (campfire, palisade_wall, watchtower) on your current tile using materials in your inventory. Not allowed inside the village.",
        "input_schema": _schema({"structure": _STR}),
    },
    "scan_area": {
        "description": "Survey a wide area (twice your view radius) and list entity types, counts and coordinates. Useful for exploration.",
        "input_schema": _schema({}),
    },
    "wait": {
        "description": "Do nothing this turn (e.g. waiting for a teammate to arrive).",
        "input_schema": _schema({"reason": _STR}),
    },
}

assert tuple(TOOL_DEFS) == TOOL_NAMES


def tool_specs(allowed: list[str] | tuple[str, ...] | None = None, strict: bool = True) -> list[dict]:
    """Claude `tools=[...]` payload, in a fixed order (keeps the prompt-cache prefix stable)."""
    names = [n for n in TOOL_NAMES if allowed is None or n in allowed]
    out = []
    for n in names:
        spec = {"name": n, "description": TOOL_DEFS[n]["description"], "input_schema": TOOL_DEFS[n]["input_schema"]}
        if strict:
            spec["strict"] = True
        out.append(spec)
    return out


def format_call(tool: str, args: dict) -> str:
    inner = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
    return f"{tool}({inner})"
