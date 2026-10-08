"""Gym generation loop (AutoGym stages 5-6, multi-agent adaptation).

    sample GenParams -> blueprint -> place + materialize -> ground-truth run of the compiled reference
    solution -> diagnose + repair (missing resources, unsafe spawns; at most MAX_REPAIRS times)
    -> round budget from the ground-truth length -> keep, with capability-axis predicates recorded

Agent failures never trigger repair here: only the reference solution is used to certify a task.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from pathlib import Path

import yaml

from . import content as C
from .blueprint import (DEFAULT_WEIGHTS, RAW_SOURCE, Blueprint, BlueprintError, GenParams, _aggressive,
                        _default_world, _free_near, build_blueprint, materialize, place, sample_params)
from .cce import RuleJudge
from .gamemap import chebyshev
from .runner import make_factory, run_episode
from .task import Task

MAX_REPAIRS = 3
GT_ROUNDS = 90          # generous budget for the ground-truth run only
MIN_BUDGET, MAX_BUDGET = 20, 55   # AgentWorld's round-budget range
LONGEST_GT = 40         # reject tasks whose reference solution alone needs more rounds than this


def round_budget(gt_rounds: int, obfuscation: float) -> int:
    """Slack over the reference length grows with hidden information (discovery + chat costs rounds)."""
    return max(MIN_BUDGET, min(MAX_BUDGET, math.ceil(gt_rounds * (2.0 + obfuscation))))


def diagnose(traj: dict, bp: Blueprint, task_id: str, layout: dict) -> dict | None:
    """Map a failed ground-truth run to a repair (AutoGym repair categories), or None if unrepairable."""
    key_of = lambda name: name[len(task_id) + 1:]
    repair: dict = {"extra_nodes": Counter(), "spawn": {}}
    for a in traj["actions"]:
        if a["tool"] == "harvest_resource" and not a["ok"] and ("No active" in a["message"] or "steps away" in a["message"]):
            key = key_of(a["agent"])
            rtype = a["args"].get("target")
            item = C.RESOURCES.get(rtype, {}).get("item")
            if item and item in bp.roles[key].gathers:
                repair["extra_nodes"][f"{key}:{item}"] += 1   # missing resource
    world = _default_world()
    taken = {tuple(p) for p in layout["spawns"].values()}
    for ev in traj["env_events"]:
        if ev["type"] != "knocked_out":
            continue
        key = key_of(ev["agent"])
        role = bp.roles[key]
        if role.kind != "gather":
            return None  # a crafter/client died on the road: no local fix
        rtype = RAW_SOURCE[sorted(role.gathers)[0]]
        mobs = _aggressive(world)
        nodes = sorted((e for e in world.entities.values() if e.type == rtype),
                       key=lambda e: -min((chebyshev(m, e.pos) - r for m, r in mobs), default=99))
        rng = random.Random(len(taken))
        for node in nodes:
            pos = _free_near(world, node.pos, taken, rng, max_r=2)
            if pos and pos != tuple(layout["spawns"][key]):
                repair["spawn"][key] = list(pos)   # unsafe spawn -> relocate
                break
    if not repair["extra_nodes"] and not repair["spawn"]:
        return None
    return {"extra_nodes": dict(repair["extra_nodes"]), "spawn": repair["spawn"]}


def axes(traj: dict, bp: Blueprint, layout: dict) -> dict:
    """Capability-axis predicates measured on the materialized task (AutoGym: axes must be genuinely exercised)."""
    dists = [chebyshev(layout["spawns"][g], layout["spawns"][r]) for g, r, _, _ in bp.deliveries]
    return {
        "handoffs": sum(1 for a in traj["actions"] if a["tool"] == "transfer_items" and a["ok"]),
        "max_handoff_distance": max(dists, default=0),
        "rendezvous_travel": max(dists, default=0) > 12,
        "info_asymmetry": bp.params.obf_bucket != "low",
        "permission_asymmetry": any(r.restricted for r in bp.roles.values()),
        "station_constraint": any(r.stations for r in bp.roles.values()),
        "team_size": len(bp.roles),
        "recipe_depth": bp.params.depth,
        "goal_quantity": bp.goal_qty,
    }


def generate_one(params: GenParams, task_id: str) -> tuple[dict | None, dict]:
    record = {"id": task_id, "params": params.__dict__.copy()}
    try:
        bp = build_blueprint(params)
    except BlueprintError as exc:
        return None, record | {"status": "rejected_blueprint", "reason": str(exc)}
    record["target"] = bp.target
    repairs = {"spawn": {}, "extra_nodes": {}}
    history = []
    traj = None
    tightened = False
    for attempt in range(MAX_REPAIRS + 1):
        try:
            layout = place(bp, repairs)
        except BlueprintError as exc:
            return None, record | {"status": "rejected_placement", "reason": str(exc)}
        td = materialize(bp, layout, task_id)
        task = Task.from_dict(td)
        errors, warnings = task.validate()
        if errors:
            return None, record | {"status": "invalid", "reason": "; ".join(errors)}
        alone = [w for w in warnings if "alone" in w]
        if alone and not tightened:
            # permission tightening: gatherers who could do everything themselves lose craft_item
            tightened = True
            for w in alone:
                key = w.split(" ")[0][len(task_id) + 1:]
                if bp.roles[key].kind == "gather" and "craft_item" not in bp.roles[key].restricted:
                    bp.roles[key].restricted.append("craft_item")
            history.append({"permission_tightening": [w.split(" ")[0] for w in alone]})
            td = materialize(bp, layout, task_id)
            task = Task.from_dict(td)
            alone = [w for w in task.validate()[1] if "alone" in w]
        if alone:
            return None, record | {"status": "no_collaboration", "reason": alone[0]}
        task.max_rounds = GT_ROUNDS
        traj = run_episode(task, make_factory("scripted"))
        if traj["result"]["success"]:
            break
        fix = diagnose(traj, bp, task_id, layout)
        if fix is None or attempt == MAX_REPAIRS:  # agent-independent failure that repair cannot fix
            return None, record | {"status": "ground_truth_failed", "reason": traj["result"]["message"],
                                   "repairs": history}
        history.append(fix)
        for k, v in fix["extra_nodes"].items():
            repairs["extra_nodes"][k] = repairs["extra_nodes"].get(k, 0) + v
        repairs["spawn"].update(fix["spawn"])
    gt_rounds = traj["result"]["rounds_used"]
    if gt_rounds > LONGEST_GT:
        return None, record | {"status": "too_long", "reason": f"reference needs {gt_rounds} rounds"}
    td["max_rounds"] = round_budget(gt_rounds, params.obfuscation)
    gt_cce = RuleJudge().compute(traj)
    td["generation"].update({
        "ground_truth": {"rounds": gt_rounds, "actions": traj["result"]["n_actions"],
                         "useful_actions": gt_cce["n_contributing"], "cce_rule": round(gt_cce["cce"], 3)},
        "repairs": history,
        "axes": axes(traj, bp, layout),
    })
    return td, record | {"status": "kept", "repairs": len(history), "gt_rounds": gt_rounds,
                         "budget": td["max_rounds"], "agents": len(td["agents"])}


def generate_gym(n: int, seed: int = 0, weights: dict | None = None, out_dir: str | None = None,
                 prefix: str = "g") -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    kept, records = [], []
    for i in range(n):
        params = sample_params(rng, weights or DEFAULT_WEIGHTS)
        td, rec = generate_one(params, f"{prefix}{seed:02d}{i:03d}")
        records.append(rec)
        if td:
            kept.append(td)
            if out_dir:
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                (Path(out_dir) / f"{td['id']}.yaml").write_text(
                    yaml.safe_dump(td, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if out_dir:
        (Path(out_dir) / "generation_log.json").write_text(json.dumps(records, indent=1), encoding="utf-8")
    return kept, records


def summarize(records: list[dict]) -> str:
    status = Counter(r["status"] for r in records)
    kept = [r for r in records if r["status"] == "kept"]
    lines = [f"attempts={len(records)} kept={len(kept)} yield={len(kept) / max(1, len(records)):.0%}  "
             + "  ".join(f"{k}={v}" for k, v in sorted(status.items()) if k != "kept")]
    if kept:
        repaired = sum(1 for r in kept if r["repairs"])
        lines.append(f"repaired before keeping: {repaired}  | mean reference rounds: "
                     f"{sum(r['gt_rounds'] for r in kept) / len(kept):.1f}  | mean team size: "
                     f"{sum(r['agents'] for r in kept) / len(kept):.1f}")
        for dim in ("depth", "topology", "obf_bucket"):
            if dim == "obf_bucket":
                vals = Counter(GenParams(**r["params"]).obf_bucket for r in kept)
            else:
                vals = Counter(r["params"][dim] for r in kept)
            lines.append(f"  {dim:<10} " + ", ".join(f"{k}: {v}" for k, v in sorted(vals.items())))
    return "\n".join(lines)
