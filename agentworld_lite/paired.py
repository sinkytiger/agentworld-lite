"""Matched information-placement experiment over certified blueprint tasks.

Both variants keep the same world, agents, objective, reference solution and round
budget. Only the location of the generated task facts changes. The relay setting
delivers the recipient's hand-off facts to the giver before round one, while leaving
the agents' chat tool unavailable; it is a diagnostic information-rescue control.
"""

from __future__ import annotations

import copy
import json
import random
import re
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import mean

import yaml

from .blueprint import DEFAULT_WEIGHTS, sample_params
from .gym import generate_one, round_budget

SECTIONS = ("Recipes", "Team", "Plan", "Locations")


def _facts(context: str) -> dict[str, list[str]]:
    facts = {section: [] for section in SECTIONS}
    section = None
    for raw in context.splitlines():
        line = raw.strip()
        if line.endswith(":") and line[:-1] in facts:
            section = line[:-1]
        elif line:
            if section is None:
                raise ValueError(f"fact outside a section: {line}")
            facts[section].append(line)
    return facts


def make_variant(base: dict, placement: str) -> dict:
    """Redistribute the same fact lines without changing the playable task."""
    if placement not in ("shared", "private"):
        raise ValueError(f"unknown information placement: {placement}")
    if base["generation"]["obfuscation_bucket"] != "low":
        raise ValueError("matched variants require a fully documented base task")
    td = copy.deepcopy(base)
    pair_id = base["id"]
    td["id"] = f"{pair_id}_{placement}"
    td["max_rounds"] = round_budget(base["generation"]["ground_truth"]["rounds"], 1.0)
    td["world_guide"] = True
    facts = _facts(base["relevant_game_context"])
    names = {agent["username"] for agent in td["agents"]}
    briefs: dict[str, list[str]] = defaultdict(list)
    relays = []

    craft_owner = {}
    for role, details in base["generation"]["blueprint"]["roles"].items():
        for item, _ in details["crafts"]:
            craft_owner[item] = f"{pair_id}_{role}"
    for line in facts["Recipes"]:
        item = line.split(":", 1)[0]
        briefs[craft_owner[item]].append(line)
    for line in facts["Team"]:
        name = line.split(" (", 1)[0]
        if name not in names:
            raise ValueError(f"unknown team member in fact: {line}")
        briefs[name].append(line)
    for line in facts["Plan"]:
        match = re.fullmatch(r"(\S+) hands (.+) to (\S+) \(at .+\)\.", line)
        if match is None or match[1] not in names or match[3] not in names:
            raise ValueError(f"unrecognized hand-off fact: {line}")
        giver, receiver = match[1], match[3]
        briefs[receiver].append(line)
        relays.append({"from": receiver, "to": giver, "text": line})
    for line in facts["Locations"]:
        owners = [name for name in names if f"{name}'s" in line or f"{name} starts" in line]
        if len(owners) != 1:
            raise ValueError(f"location fact has {len(owners)} owners: {line}")
        briefs[owners[0]].append(line)

    if placement == "private":
        td["relevant_game_context"] = ""
        for agent in td["agents"]:
            existing = agent.get("role_brief", "").strip()
            assigned = "\n".join(briefs[agent["username"]])
            agent["role_brief"] = "\n".join(x for x in (existing, assigned) if x)
    td["generation"]["axes"]["info_asymmetry"] = placement == "private"
    td["generation"]["matched"] = {
        "pair_id": pair_id,
        "placement": placement,
        "fact_counts": {key: len(value) for key, value in facts.items()},
        "relay_messages": relays if placement == "private" else [],
    }
    return td


def generate_pairs(n: int, seed: int = 0, out_dir: str | Path | None = None,
                   prefix: str = "p", weights: dict | None = None,
                   max_attempts: int | None = None) -> tuple[list[dict], list[dict]]:
    """Keep n certified blueprint pairs, or record why the attempt cap stopped us."""
    if n < 1:
        raise ValueError("n must be positive")
    cap = max_attempts if max_attempts is not None else 5 * n
    if cap < n:
        raise ValueError("max_attempts must be at least n")
    rng = random.Random(seed)
    kept, records = [], []
    out = Path(out_dir) if out_dir is not None else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    for attempt in range(cap):
        if len(kept) == n:
            break
        params = replace(sample_params(rng, weights or DEFAULT_WEIGHTS), obfuscation=0.0)
        pair_id = f"{prefix}{seed:02d}{attempt:03d}"
        base, record = generate_one(params, pair_id)
        records.append(record)
        if base is None:
            continue
        variants = [make_variant(base, placement) for placement in ("shared", "private")]
        kept.append(variants)
        if out:
            for td in variants:
                (out / f"{td['id']}.yaml").write_text(
                    yaml.safe_dump(td, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if out:
        (out / "generation_log.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return kept, records


def compare_pairs(trajs: list[dict]) -> str:
    """Report complete matched blocks, taking the blueprint as the sampling unit."""
    cells = {}
    for traj in trajs:
        matched = traj.get("matched") or {}
        if not matched:
            continue
        key = (matched["pair_id"], traj["seed"], matched["placement"], traj["setting"])
        if key in cells:
            raise ValueError(f"duplicate matched episode: {key}")
        cells[key] = traj
    required = (("shared", "full"), ("shared", "no_comm"),
                ("private", "full"), ("private", "no_comm"))
    blocks = {}
    for pair_id, seed in sorted({key[:2] for key in cells}):
        if all((pair_id, seed, *condition) in cells for condition in required):
            blocks[(pair_id, seed)] = {condition: cells[(pair_id, seed, *condition)] for condition in required}
    if not blocks:
        raise ValueError("no complete matched blocks (shared/private x full/no_comm)")
    by_pair: dict[str, list[dict]] = defaultdict(list)
    for (pair_id, _), block in blocks.items():
        by_pair[pair_id].append(block)
    success = lambda t: int(t["result"]["success"])
    rates = {condition: mean(mean(success(block[condition]) for block in pair_blocks)
                             for pair_blocks in by_pair.values()) for condition in required}
    chat_gap = {placement: rates[(placement, "full")] - rates[(placement, "no_comm")]
                for placement in ("shared", "private")}
    interaction = chat_gap["private"] - chat_gap["shared"]
    task_interactions = []
    for pair_blocks in by_pair.values():
        task_interactions.append(mean(
            success(block[("private", "full")]) - success(block[("private", "no_comm")])
            - success(block[("shared", "full")]) + success(block[("shared", "no_comm")])
            for block in pair_blocks))
    rescues = [(blocks[(pair_id, seed)][("private", "no_comm")],
                cells[(pair_id, seed, "private", "relay")])
               for pair_id, seed in blocks if (pair_id, seed, "private", "relay") in cells]
    lines = ["# Matched information-placement experiment", "",
             f"Complete blocks: {len(blocks)} task-seed pairs from {len(by_pair)} distinct blueprints.",
             "The blueprint is the independent task unit; rates first average seeds within each blueprint.", "",
             "| Information | Chat allowed | Chat blocked | Gap |", "|---|---:|---:|---:|"]
    for placement in ("shared", "private"):
        a, b = rates[(placement, "full")], rates[(placement, "no_comm")]
        n_a = sum(success(block[(placement, "full")]) for block in blocks.values())
        n_b = sum(success(block[(placement, "no_comm")]) for block in blocks.values())
        lines.append(f"| {placement} | {a:.1%} ({n_a}/{len(blocks)}) | "
                     f"{b:.1%} ({n_b}/{len(blocks)}) | {a-b:+.1%} |")
    lines += ["", f"Difference in chat gaps (private minus shared): {interaction:+.1%}."]
    for placement in ("shared", "private"):
        avg_chats = mean(mean(block[(placement, "full")]["result"]["n_chats"] for block in pair_blocks)
                         for pair_blocks in by_pair.values())
        lines.append(f"{placement} chats per allowed episode: {avg_chats:.1f}.")
    if len(task_interactions) >= 2:
        rng = random.Random(0)
        boot = sorted(mean(rng.choices(task_interactions, k=len(task_interactions))) for _ in range(5000))
        lines.append(f"Blueprint bootstrap 95% interval: {boot[124]:+.1%} to {boot[4874]:+.1%} "
                     "(uncertainty over these sampled blueprints).")
    if rescues:
        failed = [(blocked, relay) for blocked, relay in rescues if not success(blocked)]
        lines.append(f"Private relay success: {sum(success(relay) for _, relay in rescues)}/{len(rescues)} "
                     "complete blocks with a relay run.")
        lines.append((f"Of {len(failed)} private no-chat failures, {sum(success(relay) for _, relay in failed)} "
                      "succeeded with relay.") if failed else
                     "No private no-chat failures had a relay run, so rescue could not be assessed.")
        lines.append("The relay injects plan facts at round 0 without consuming agent turns; "
                     "it is a diagnostic upper bound, not an equal-cost chat policy.")
    lines += ["", "| Blueprint | Seeds | Shared full/no chat | Private full/no chat | Private relay |",
              "|---|---:|---:|---:|---:|"]
    for pair_id, pair_blocks in sorted(by_pair.items()):
        n = len(pair_blocks)
        shared = [sum(success(b[("shared", setting)]) for b in pair_blocks) for setting in ("full", "no_comm")]
        private = [sum(success(b[("private", setting)]) for b in pair_blocks) for setting in ("full", "no_comm")]
        relay_ts = [cells[(pair_id, seed, "private", "relay")] for pid, seed in blocks
                    if pid == pair_id and (pair_id, seed, "private", "relay") in cells]
        relay_str = f"{sum(success(t) for t in relay_ts)}/{len(relay_ts)}" if relay_ts else "-"
        lines.append(f"| {pair_id} | {n} | {shared[0]}/{n} / {shared[1]}/{n} | "
                     f"{private[0]}/{n} / {private[1]}/{n} | {relay_str} |")
    lines += ["", "Descriptive results for the tested model and tasks; no population claim follows from these counts.", ""]
    return "\n".join(lines)
