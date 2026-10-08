"""Programmatic success verification over the game state.

The paper attaches a Python verifier to each task that inspects the final game state
and reports checkpoints such as "Logs: 3/5, Kills: 0/3, All alive: True". Here the
checkpoints are declared in the task YAML and compiled into the same kind of checks,
which also yields the Partial Success Rate (PSR) directly.
"""

from __future__ import annotations

from .engine import World
from .gamemap import chebyshev, in_rect

CHECKPOINT_TYPES = (
    "has_item", "team_total", "equipped", "kills", "all_alive",
    "in_region", "near", "structure", "plates", "entity_defeated", "rounds_survived",
)


def _agents(world: World, sel) -> list:
    if sel in (None, "all"):
        return list(world.agents.values())
    if isinstance(sel, str):
        sel = [sel]
    return [world.agents[n] for n in sel]


def _item_count(agent, item: str, include_equipped: bool = True) -> int:
    n = agent.inventory[item]
    if include_equipped and item in agent.equipment.values():
        n += 1
    return n


def measure(world: World, cp: dict) -> tuple[int, int, bool]:
    """Returns (have, need, is_boolean)."""
    t = cp["type"]
    if t == "has_item":
        need = int(cp.get("count", 1))
        who = cp.get("agent", "any")
        if who == "any":
            have = max(_item_count(a, cp["item"]) for a in world.agents.values())
        else:
            have = _item_count(world.agents[who], cp["item"])
        return have, need, False
    if t == "team_total":
        return sum(_item_count(a, cp["item"]) for a in world.agents.values()), int(cp["count"]), False
    if t == "equipped":
        a = world.agents[cp["agent"]]
        return int(cp["item"] in a.equipment.values()), 1, True
    if t == "kills":
        have = sum(a.kills[cp["mob"]] for a in _agents(world, cp.get("agent")))
        return have, int(cp.get("count", 1)), False
    if t == "all_alive":
        ok = all(a.deaths == 0 and not a.knocked_out for a in world.agents.values())
        return int(ok), 1, True
    if t == "in_region":
        ags = _agents(world, cp.get("agents"))
        return sum(in_rect(a.x, a.y, tuple(cp["rect"])) and not a.knocked_out for a in ags), len(ags), False
    if t == "near":
        ags = _agents(world, cp.get("agents"))
        pos, r = tuple(cp["pos"]), int(cp.get("radius", 2))
        return sum(chebyshev(a.pos, pos) <= r and not a.knocked_out for a in ags), len(ags), False
    if t == "structure":
        built = [e for e in world.entities.values() if e.kind == "structure" and e.type == cp["structure"]]
        if "near" in cp:
            built = [e for e in built if chebyshev(e.pos, tuple(cp["near"])) <= int(cp.get("radius", 4))]
        return len(built), int(cp.get("count", 1)), False
    if t == "plates":
        plates = [e for e in world.entities.values() if e.kind == "plate"]
        have = 0
        for p in plates:
            crystal = f"{p.meta['color']}_crystal"
            if any(a.pos == p.pos and a.inventory[crystal] > 0 and not a.knocked_out for a in world.agents.values()):
                have += 1
        return have, len(plates), False
    if t == "rounds_survived":
        return world.completed_rounds, int(cp["rounds"]), False
    if t == "entity_defeated":
        e = world.entities[cp["entity"]]
        return int(bool(e.meta.get("killed_by"))), 1, True
    raise ValueError(f"unknown checkpoint type {t!r}")


def default_name(cp: dict) -> str:
    t = cp["type"]
    if t in ("has_item", "team_total"):
        return cp["item"]
    if t == "equipped":
        return f"{cp['agent']} equips {cp['item']}"
    if t == "kills":
        return f"{cp['mob']} kills"
    if t == "structure":
        return cp["structure"]
    return t


def evaluate(world: World, spec: dict) -> dict:
    cps = []
    for cp in spec["checkpoints"]:
        have, need, is_bool = measure(world, cp)
        done = have >= need
        cps.append({
            "name": cp.get("name") or default_name(cp),
            "have": have, "need": need, "done": done, "bool": is_bool,
            "frac": 1.0 if done else (have / need if need else 0.0),
        })
    mode = spec.get("success", "all")
    success = all(c["done"] for c in cps) if mode == "all" else any(c["done"] for c in cps)
    psr = sum(c["frac"] for c in cps) / len(cps) if cps else float(success)
    message = ", ".join(
        f"{c['name']}: {bool(c['have'])}" if c["bool"] else f"{c['name']}: {min(c['have'], c['need'])}/{c['need']}"
        for c in cps
    )
    return {"success": success, "psr": psr, "checkpoints": cps, "message": message}
