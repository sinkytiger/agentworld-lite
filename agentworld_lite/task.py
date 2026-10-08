"""Task specification: loading, world construction, prompt context, static validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import content as C
from .engine import TOOL_NAMES, World
from .gamemap import WORLD_GUIDE
from .verifier import CHECKPOINT_TYPES

CATEGORIES = ("combat", "crafting", "gathering", "trading", "exploration", "survival", "construction", "coordination")


@dataclass
class Task:
    id: str
    name: str
    category: str
    description: str
    primary_objective: str
    max_rounds: int
    agents: list[dict]
    verifier: dict
    relevant_game_context: str = ""
    secondary_objectives: list[str] = field(default_factory=list)
    extra_entities: list[dict] = field(default_factory=list)
    reference_solution: dict | None = None
    meta: dict = field(default_factory=dict)
    path: str | None = None

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_dict(cls, d: dict, path: str | None = None) -> "Task":
        obj = d.get("objectives") or {}
        known = {"id", "name", "category", "description", "objectives", "max_rounds", "agents", "verifier",
                 "relevant_game_context", "extra_entities", "reference_solution"}
        return cls(
            id=d["id"], name=d["name"], category=d["category"], description=d.get("description", "").strip(),
            primary_objective=obj.get("primary", "").strip(), secondary_objectives=list(obj.get("secondary") or []),
            max_rounds=int(d["max_rounds"]), agents=list(d["agents"]), verifier=d["verifier"],
            relevant_game_context=(d.get("relevant_game_context") or "").strip(),
            extra_entities=list(d.get("extra_entities") or []),
            reference_solution=d.get("reference_solution"),
            meta={k: v for k, v in d.items() if k not in known}, path=path,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Task":
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f), str(path))

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "name": self.name, "category": self.category, "description": self.description,
            "objectives": {"primary": self.primary_objective, "secondary": self.secondary_objectives},
            "max_rounds": self.max_rounds, "agents": self.agents, "extra_entities": self.extra_entities,
            "relevant_game_context": self.relevant_game_context, "verifier": self.verifier,
        }
        if self.reference_solution:
            d["reference_solution"] = self.reference_solution
        d.update(self.meta)
        return d

    @property
    def usernames(self) -> list[str]:
        return [a["username"] for a in self.agents]

    # ------------------------------------------------------------------ world
    def build_world(self, seed: int = 7, random_spawn: bool = False) -> World:
        world = World(seed=seed)
        for e in self.extra_entities:
            extra = {k: v for k, v in e.items() if k not in ("kind", "type", "pos", "id")}
            world.add_entity(e["kind"], e["type"], e["pos"], entity_id=e.get("id"), **extra)
        rng = None
        if random_spawn:
            import random
            rng = random.Random(seed)
        for spec in self.agents:
            spec = dict(spec)
            if rng is not None:
                spec["spawn"] = _random_walkable(world, rng)
            world.add_agent(spec)
        return world

    # ------------------------------------------------------------------ prompts
    def context_block(self, username: str, include_docs: bool = True) -> str:
        """User prompt template from the paper (Task / Description / Objectives / Context / Team)."""
        me = next(a for a in self.agents if a["username"] == username)
        others = [a["username"] for a in self.agents if a["username"] != username]
        sec = "\n".join(f"- {s}" for s in self.secondary_objectives) or "- (none)"
        parts = [
            f"**Task:** {self.name}",
            f"**Description:** {self.description}",
            f"**Primary Objective:** {self.primary_objective}",
            f"**Secondary Objectives:**\n{sec}",
        ]
        if include_docs:
            guide = f"\n\n{WORLD_GUIDE}" if self.meta.get("world_guide", True) else ""
            ctx = self.relevant_game_context or "(no shared documentation for this task)"
            parts.append(f"**Relevant Context:**\n{ctx}{guide}")
        parts.append(
            "**Team Information:**\n"
            f"- Total agents in party: {len(self.agents)}\n"
            f"- Your username: {username} (role: {me.get('role', '-')})\n"
            f"- Other team members: {', '.join(others)}"
        )
        if me.get("role_brief"):
            parts.append(f"**Your private role briefing:** {me['role_brief'].strip()}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ validation
    def validate(self) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if self.category not in CATEGORIES:
            errors.append(f"unknown category {self.category}")
        if not 5 <= self.max_rounds <= 200:
            errors.append(f"max_rounds {self.max_rounds} out of range")
        names = self.usernames
        if len(set(names)) != len(names):
            errors.append("duplicate usernames")
        world = World(seed=7, populate=False)
        for a in self.agents:
            x, y = a["spawn"]
            if not world.map.is_walkable(x, y):
                errors.append(f"{a['username']}: spawn {a['spawn']} not walkable")
            for s in a.get("skills") or {}:
                if s not in C.SKILLS:
                    errors.append(f"{a['username']}: unknown skill {s}")
            for i in list((a.get("inventory") or {}).keys()) + list(a.get("equipment") or []):
                if i not in C.ITEMS:
                    errors.append(f"{a['username']}: unknown item {i}")
            for t in a.get("restricted_tools") or []:
                if t not in TOOL_NAMES:
                    errors.append(f"{a['username']}: unknown restricted tool {t}")
        tables = {"mob": C.MOBS, "resource": C.RESOURCES, "landmark": dict.fromkeys(C.LANDMARKS),
                  "plate": {"plate": 1}, "npc": C.SHOPS, "station": {"anvil": 1, "stove": 1}, "structure": C.STRUCTURES}
        for e in self.extra_entities:
            if e["kind"] not in tables or e["type"] not in tables[e["kind"]]:
                errors.append(f"extra entity {e} has unknown kind/type")
            elif not world.map.is_walkable(*e["pos"]):
                errors.append(f"extra entity {e.get('id', e['type'])} at {e['pos']} not walkable")
        for cp in self.verifier.get("checkpoints", []):
            if cp.get("type") not in CHECKPOINT_TYPES:
                errors.append(f"unknown checkpoint type {cp.get('type')}")
                continue
            for key in ("agent",):
                if key in cp and cp[key] not in ("any", "all") and cp[key] not in names:
                    errors.append(f"checkpoint refers to unknown agent {cp[key]}")
            if "item" in cp and cp["item"] not in C.ITEMS:
                errors.append(f"checkpoint refers to unknown item {cp['item']}")
            if isinstance(cp.get("agents"), list):
                errors += [f"checkpoint refers to unknown agent {n}" for n in cp["agents"] if n not in names]
        if self.reference_solution:
            for n, steps in self.reference_solution.items():
                if n not in names:
                    errors.append(f"reference_solution for unknown agent {n}")
                for step in steps:
                    if step[0] not in TOOL_NAMES:
                        errors.append(f"reference_solution uses unknown tool {step[0]}")
        warnings += self.collaboration_warnings()
        return errors, warnings

    def collaboration_warnings(self) -> list[str]:
        """Static check that the objective needs more than one agent (paper: every task must
        require collaboration). Requirements = skill levels, gathering tools and tool permissions
        along the recipe tree of each item checkpoint; items sold by shops are skipped."""
        reqs: set[tuple] = set()
        start_inv: dict[str, int] = {}
        for a in self.agents:
            for k, v in (a.get("inventory") or {}).items():
                start_inv[k] = start_inv.get(k, 0) + int(v)
        for cp in self.verifier.get("checkpoints", []):
            if cp["type"] in ("has_item", "team_total", "equipped"):
                _requirements(cp["item"], int(cp.get("count", 1)), start_inv, reqs, depth=0)
        if not reqs:
            return []

        def meets(a: dict, req: tuple) -> bool:
            kind = req[0]
            if kind == "skill":
                return int((a.get("skills") or {}).get(req[1], C.DEFAULT_SKILL_LEVEL)) >= req[2]
            if kind == "tool":
                return req[1] in (a.get("inventory") or {}) or req[1] in (a.get("equipment") or [])
            return req[1] not in (a.get("restricted_tools") or [])

        warns = [f"no agent satisfies {req} (objective may be infeasible)"
                 for req in sorted(reqs) if not any(meets(a, req) for a in self.agents)]
        for a in self.agents:
            if all(meets(a, req) for req in reqs):
                warns.append(f"{a['username']} alone satisfies every requirement; collaboration may be optional")
        return warns


def _requirements(item: str, count: int, inv: dict, reqs: set, depth: int) -> None:
    if depth > 6:
        return
    have = inv.get(item, 0)
    if have >= count:
        inv[item] = have - count
        return
    missing = count - have
    inv[item] = 0
    prices = [shop["sells"][item] for shop in C.SHOPS.values() if item in shop["sells"]]
    if prices and inv.get("coins", 0) >= min(prices) * missing:
        inv["coins"] -= min(prices) * missing
        return  # the team can buy it; not a hard skill requirement
    if item in C.RECIPES:
        r = C.RECIPES[item]
        reqs.update({("skill", r["skill"], r["level"]), ("action", "craft_item")})
        crafts = -(-missing // r["output"])
        for k, n in r["inputs"].items():
            _requirements(k, n * crafts, inv, reqs, depth + 1)
        return
    for res in C.RESOURCES.values():
        if res["item"] == item:
            reqs.update({("skill", res["skill"], res["level"]), ("action", "harvest_resource")})
            if res["tool"]:
                reqs.add(("tool", res["tool"]))
            return


def _random_walkable(world: World, rng) -> list[int]:
    from .gamemap import HEIGHT, WIDTH
    while True:
        x, y = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        if world.map.is_walkable(x, y):
            return [x, y]


def load_tasks(paths: list[str] | str) -> list[Task]:
    if isinstance(paths, str):
        paths = [paths]
    out: list[Task] = []
    for p in paths:
        p = Path(p)
        files = sorted(p.glob("*.yaml")) if p.is_dir() else [p]
        out += [Task.load(f) for f in files]
    return out
