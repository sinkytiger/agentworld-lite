"""Blueprint-first task generation, after AutoGym (arXiv:2609.22592), adapted to multi-agent collaboration.

AutoGym fixes a task's solution path before it builds the environment, so solvability is guaranteed
by construction instead of being checked after the fact. In this simulator the recipe graph and the
map are fully known, so the blueprint is built programmatically, with no LLM in the loop:

  GenParams -> Blueprint   blueprint initialization + construction: target, recipe tree, roles,
                           who produces what and who hands what to whom
  Blueprint -> Task        maze materialization: spawns, extra resource nodes, shared doc vs private
                           briefs (obfuscation), verifier, compiled reference solution
  (gym.py)                 ground truth run, iterative repair, round budget, yield statistics

Adaptations from single-agent AutoGym to multi-agent play (design choices of this project):
  - task topology tier  -> recipe depth of the target (1-3) and team structure (chain / hub)
  - obfuscation         -> how much of the plan sits in the shared document vs. one agent's private
                           brief vs. only discoverable in the world (distributed obfuscation)
  - answerability       -> every withheld fact must be held by some agent or be observable
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from . import content as C
from .engine import World
from .gamemap import biome_at, chebyshev

# ---------------------------------------------------------------------- generation parameters
OBFUSCATION_BUCKETS = {"low": (0.0, 0.3), "mid": (0.4, 0.6), "high": (0.7, 0.9), "max": (1.0, 1.0)}

DEFAULT_WEIGHTS: dict[str, dict] = {
    "depth": {1: 0.3, 2: 0.45, 3: 0.25},
    "topology": {"chain": 0.6, "hub": 0.4},
    "obfuscation": {"low": 0.3, "mid": 0.3, "high": 0.25, "max": 0.15},
    "quantity": {1: 0.6, 2: 0.3, 3: 0.1},
    "extra_agents": {0: 0.5, 1: 0.35, 2: 0.15},
}


@dataclass
class GenParams:
    depth: int          # recipe depth of the target (AutoGym topology tier / interaction depth)
    topology: str       # chain: one agent per craft skill; hub: one assembler holds every craft skill
    obfuscation: float  # 0..1, how much of the plan is withheld from the shared document
    quantity: int       # copies of the target (quantitative axis)
    extra_agents: int   # extra gatherers created by splitting quotas (team size axis)
    seed: int

    @property
    def obf_bucket(self) -> str:
        if self.obfuscation >= 0.95:
            return "max"
        if self.obfuscation >= 0.65:
            return "high"
        if self.obfuscation >= 0.35:
            return "mid"
        return "low"


def _wchoice(rng: random.Random, dist: dict):
    keys = list(dist)
    return rng.choices(keys, weights=[float(dist[k]) for k in keys])[0]


def sample_params(rng: random.Random, weights: dict | None = None) -> GenParams:
    w = weights or DEFAULT_WEIGHTS
    lo, hi = OBFUSCATION_BUCKETS[_wchoice(rng, w["obfuscation"])]
    return GenParams(
        depth=int(_wchoice(rng, w["depth"])),
        topology=str(_wchoice(rng, w["topology"])),
        obfuscation=round(rng.uniform(lo, hi), 2),
        quantity=int(_wchoice(rng, w["quantity"])),
        extra_agents=int(_wchoice(rng, w["extra_agents"])),
        seed=rng.randrange(1_000_000),
    )


# ---------------------------------------------------------------------- recipe graph
PROVIDED = {"bead": "an heirloom bead", "hide": "hides from an earlier hunt"}  # leaves not harvestable here
RAW_SOURCE = {r["item"]: t for t, r in C.RESOURCES.items()}
CRAFT_ROLE = {"fletching": "fletcher", "crafting": "crafter", "smithing": "smith", "cooking": "cook"}
GATHER_ROLE = {"lumberjacking": "lumberjack", "mining": "miner", "fishing": "fisher", "foraging": "forager"}
CLIENTS = {  # goal item -> (role, stats needed to use it)
    "staff": ("wizard", {"magic": 25}),
    "bronze_sword": ("knight", {"strength": 18}),
    "iron_sword": ("knight", {"strength": 20}),
    "bow": ("ranger", {"fletching": 12}),
    "leatherarmor": ("guard", {"strength": 15}),
}


def recipe_of(item: str) -> dict | None:
    if item in C.RECIPES:
        return C.RECIPES[item]
    if item in C.STRUCTURES:
        s = C.STRUCTURES[item]
        return {"skill": s["skill"], "level": s["level"], "inputs": s["inputs"], "output": 1, "station": None, "structure": True}
    return None


@lru_cache(maxsize=None)
def depth(item: str) -> int:
    r = recipe_of(item)
    return 0 if r is None else 1 + max(depth(i) for i in r["inputs"])


TARGETS = {d: sorted(x for x in list(C.RECIPES) + list(C.STRUCTURES) if depth(x) == d) for d in (1, 2, 3)}


def goal_kind(target: str) -> str:
    if target in C.STRUCTURES:
        return "structure"
    return "equip" if C.ITEMS[target].get("slot") else "deliver"


# ---------------------------------------------------------------------- blueprint
@dataclass
class Role:
    key: str
    kind: str                                    # gather | craft | client
    skills: dict = field(default_factory=dict)
    tools: list = field(default_factory=list)
    gathers: dict = field(default_factory=dict)  # raw item -> quota
    crafts: list = field(default_factory=list)   # [item, n_crafts] in production order
    holds: dict = field(default_factory=dict)    # provided starting items
    stations: list = field(default_factory=list)
    builds: bool = False
    restricted: list = field(default_factory=list)


@dataclass
class Blueprint:
    params: GenParams
    target: str
    goal: str
    goal_qty: int
    roles: dict                  # key -> Role
    deliveries: list             # [giver, receiver, item, qty]
    crafts: dict                 # item -> n_crafts
    raw_need: dict               # raw item -> total qty

    def summary(self) -> dict:
        return {
            "target": self.target, "goal": self.goal, "goal_qty": self.goal_qty,
            "roles": {k: {"kind": r.kind, "skills": r.skills, "gathers": r.gathers, "crafts": r.crafts}
                      for k, r in self.roles.items()},
            "deliveries": self.deliveries,
        }


class BlueprintError(ValueError):
    pass


def build_blueprint(p: GenParams) -> Blueprint:
    rng = random.Random(p.seed)
    target = rng.choice(TARGETS[p.depth])
    goal = goal_kind(target)
    r_t = recipe_of(target)
    if goal == "deliver":
        goal_qty = p.quantity * r_t["output"]
    else:
        goal_qty = 1

    # --- expand the recipe tree: total need, number of crafts, who consumes what
    def craft_key(skill: str, item: str = "") -> str:
        if item in C.STRUCTURES:
            return "builder" if p.topology == "hub" else CRAFT_ROLE[skill]
        return "artisan" if p.topology == "hub" else CRAFT_ROLE[skill]

    nodes: set[str] = set()

    def collect(item):
        nodes.add(item)
        r = recipe_of(item)
        if r:
            for i in r["inputs"]:
                collect(i)
    collect(target)

    need: Counter = Counter({target: goal_qty})
    crafts: dict[str, int] = {}
    uses: Counter = Counter()  # (consumer role key, item) -> qty
    for item in sorted(nodes, key=lambda x: (-depth(x), x)):
        r = recipe_of(item)
        if r is None:
            continue
        n = math.ceil(need[item] / r["output"])
        crafts[item] = n
        for inp, k in r["inputs"].items():
            need[inp] += k * n
            uses[(craft_key(r["skill"], item), inp)] += k * n
    has_client = goal in ("deliver", "equip")
    if has_client:
        uses[("client", target)] += goal_qty

    # --- roles
    roles: dict[str, Role] = {}
    for item in sorted(crafts, key=lambda x: (depth(x), x)):
        r = recipe_of(item)
        key = craft_key(r["skill"], item)
        role = roles.setdefault(key, Role(key=key, kind="craft"))
        role.skills[r["skill"]] = max(role.skills.get(r["skill"], 0), r["level"] + rng.randint(0, 6))
        role.crafts.append([item, crafts[item]])
        if r.get("station") and r["station"] not in role.stations:
            role.stations.append(r["station"])
        if r.get("structure"):
            role.builds = True
    for role in roles.values():
        if role.builds and role.stations:
            raise BlueprintError("hub assembler would need both a crafting station and the build site")
        role.restricted = ["harvest_resource"]

    raw_need = {i: need[i] for i in nodes if recipe_of(i) is None and i in RAW_SOURCE}
    for item, qty in sorted(raw_need.items()):
        res = C.RESOURCES[RAW_SOURCE[item]]
        key = GATHER_ROLE[res["skill"]]
        role = roles.setdefault(key, Role(key=key, kind="gather"))
        role.skills[res["skill"]] = max(role.skills.get(res["skill"], 0), res["level"] + rng.randint(0, 12))
        role.gathers[item] = qty
        if res["tool"] and res["tool"] not in role.tools:
            role.tools.append(res["tool"])

    if has_client:
        cname, stats = CLIENTS.get(target, ("quartermaster", {}))
        roles["client"] = Role(key="client", kind="client", skills=dict(stats),
                               restricted=["harvest_resource", "craft_item"])
    provided = {i: need[i] for i in nodes if recipe_of(i) is None and i in PROVIDED}
    if provided:
        holder = "client" if has_client else next(k for k, r in roles.items() if r.kind == "gather")
        roles[holder].holds.update(provided)

    # --- team-size axis: split gatherer quotas into extra agents
    supply: dict[str, list] = {}  # item -> [[producer key, qty]]
    for k, r in roles.items():
        for item, qty in r.gathers.items():
            supply[item] = [[k, qty]]
        for item, qty in r.holds.items():
            supply[item] = [[k, qty]]
    for item, n in crafts.items():
        if recipe_of(item).get("structure"):
            continue
        supply[item] = [[craft_key(recipe_of(item)["skill"]), n * recipe_of(item)["output"]]]

    i = 0
    while i < p.extra_agents or len(roles) < 3:
        i += 1
        if i > p.extra_agents + 3:
            break
        gathers = sorted((r for r in roles.values() if r.kind == "gather"), key=lambda r: -sum(r.gathers.values()))
        if not gathers:
            break
        src = gathers[0]
        new_key = f"{src.key}{i + 1}"
        if len(src.gathers) >= 2:
            moved = sorted(src.gathers)[len(src.gathers) // 2:]
            dst = Role(key=new_key, kind="gather", skills=dict(src.skills), tools=list(src.tools))
            for item in moved:
                dst.gathers[item] = src.gathers.pop(item)
                supply[item] = [[new_key, dst.gathers[item]]]
        else:
            (item, qty), = src.gathers.items()
            if qty < (2 if len(roles) < 3 else 4):
                break
            half = qty // 2
            dst = Role(key=new_key, kind="gather", skills=dict(src.skills), tools=list(src.tools))
            src.gathers[item], dst.gathers[item] = qty - half, half
            supply[item] = [[src.key, qty - half], [new_key, half]]
        roles[new_key] = dst

    # --- deliveries: assign supply chunks to consumers
    agg: Counter = Counter()
    for item, chunks in supply.items():
        consumers = sorted((c, q) for (c, it), q in uses.items() if it == item)
        chunks = [list(c) for c in chunks]
        for consumer, q in consumers:
            while q > 0 and chunks:
                giver, avail = chunks[0]
                take = min(avail, q)
                if giver != consumer:
                    agg[(giver, consumer, item)] += take
                q -= take
                chunks[0][1] -= take
                if chunks[0][1] <= 0:
                    chunks.pop(0)
    deliveries = [[g, r, i, q] for (g, r, i), q in sorted(agg.items())]

    # a giver hands over only after finishing its own production, so the role graph must be acyclic
    graph = defaultdict(set)
    for g, r, item, _ in deliveries:
        if item not in PROVIDED:
            graph[g].add(r)
    seen, stack = set(), set()

    def cyclic(n):
        if n in stack:
            return True
        if n in seen:
            return False
        seen.add(n)
        stack.add(n)
        hit = any(cyclic(m) for m in graph[n])
        stack.discard(n)
        return hit
    if any(cyclic(n) for n in list(graph)):
        raise BlueprintError("role dependency cycle")
    if len(roles) < 3:
        raise BlueprintError("fewer than 3 agents; not a team task")
    return Blueprint(p, target, goal, goal_qty, roles, deliveries, crafts, raw_need)


# ---------------------------------------------------------------------- materialization
STATION_SITES = {
    ("anvil",): [(33, 20), (32, 20), (34, 21)],
    ("fire",): [(36, 20), (37, 20), (35, 21)],
    ("anvil", "fire"): [(34, 20)],
}
VILLAGE_TILES = [(28, 23), (30, 23), (27, 26), (29, 27), (31, 27), (34, 26), (36, 25), (38, 23), (26, 22), (37, 28)]
BUILD_SITES = [(24, 13), (22, 22), (41, 25), (23, 31), (40, 11)]
NODE_REACH = 10   # nodes within this distance of a gatherer's spawn count as reachable supply
RETRIES = 80


def _default_world() -> World:
    return World(seed=7)


def _aggressive(world: World):
    return [(e.pos, e.meta.get("aggro_radius", 0)) for e in world.entities.values()
            if e.kind == "mob" and e.meta.get("aggro_radius", 0) > 0]


def _safe(world: World, pos, taken: set, margin: int = 3) -> bool:
    x, y = pos
    if not world.map.is_walkable(x, y) or pos in taken:
        return False
    if any(e.pos == pos for e in world.entities.values()):
        return False
    return all(chebyshev(m, pos) > r + margin for m, r in _aggressive(world))


def _free_near(world, center, taken, rng, max_r=4):
    cands = [(center[0] + dx, center[1] + dy) for dx in range(-max_r, max_r + 1) for dy in range(-max_r, max_r + 1)]
    rng.shuffle(cands)
    cands.sort(key=lambda p: chebyshev(p, center))
    for p in cands:
        if p != tuple(center) and _safe(world, p, taken, margin=2):
            return p
    return None


def place(bp: Blueprint, repairs: dict | None = None) -> dict:
    """Choose spawn positions, build site and extra resource nodes. `repairs` may pin spawns or add nodes."""
    repairs = repairs or {}
    rng = random.Random(bp.params.seed + 1)
    world = _default_world()
    taken: set = set()
    spawns: dict[str, tuple] = {}
    site = None
    if any(r.builds for r in bp.roles.values()):
        site = rng.choice([s for s in BUILD_SITES if _safe(world, s, taken)])

    for key, role in bp.roles.items():
        if key in repairs.get("spawn", {}):
            pos = tuple(repairs["spawn"][key])
        elif role.kind == "craft" and role.builds:
            pos = site
        elif role.kind == "craft" and role.stations:
            options = STATION_SITES[tuple(sorted(role.stations))]
            pos = next((p for p in options if p not in taken), None) or _free_near(world, options[0], taken, rng)
        elif role.kind in ("craft", "client"):
            pos = next(p for p in rng.sample(VILLAGE_TILES, len(VILLAGE_TILES)) if p not in taken)
        else:
            first = sorted(role.gathers)[0]
            rtype = RAW_SOURCE[first]
            nodes = [e for e in world.entities.values() if e.type == rtype]
            rng.shuffle(nodes)
            nodes.sort(key=lambda e: min((chebyshev(m, e.pos) - r for m, r in _aggressive(world)), default=99) < 4)
            pos = None
            for node in nodes:
                pos = _free_near(world, node.pos, taken, rng, max_r=2)
                if pos:
                    break
        if pos is None:
            raise BlueprintError(f"no safe spawn for {key}")
        taken.add(tuple(pos))
        spawns[key] = tuple(pos)

    # missing-resource prevention: guarantee enough reachable capacity for every quota
    extra_nodes: list[dict] = []
    for key, role in bp.roles.items():
        if role.kind != "gather":
            continue
        lvl_of = role.skills
        for item, quota in role.gathers.items():
            rtype = RAW_SOURCE[item]
            res = C.RESOURCES[rtype]
            per = 1 + (1 if lvl_of.get(res["skill"], 1) >= res["level"] + C.HARVEST_BONUS_MARGIN else 0)
            harvests = math.ceil(quota / per)
            cap = sum(res["capacity"] for e in world.entities.values()
                      if e.type == rtype and chebyshev(e.pos, spawns[key]) <= NODE_REACH)
            cap += res["capacity"] * sum(1 for n in extra_nodes if n["type"] == rtype and chebyshev(n["pos"], spawns[key]) <= NODE_REACH)
            add = math.ceil(max(0, harvests + 1 - cap) / res["capacity"]) + repairs.get("extra_nodes", {}).get(f"{key}:{item}", 0)
            for _ in range(add):
                pos = _free_near(world, spawns[key], taken | {tuple(n["pos"]) for n in extra_nodes}, rng, max_r=3)
                if pos:
                    extra_nodes.append({"kind": "resource", "type": rtype, "pos": list(pos)})
    return {"spawns": spawns, "site": site, "extra_nodes": extra_nodes}


def _station_pos(station: str) -> tuple:
    return (33, 19) if station == "anvil" else (36, 19)


def compile_solution(bp: Blueprint, names: dict, layout: dict) -> dict:
    """Reference solution per agent: gather -> craft/build -> deliver; clients equip or hold."""
    sol: dict[str, list] = {}
    for key, role in bp.roles.items():
        steps: list = []
        for item, quota in sorted(role.gathers.items()):
            steps.append(["harvest_resource", {"target": RAW_SOURCE[item]}, {"until_have": {item: quota}, "retries": RETRIES}])
        for item, n in role.crafts:
            r = recipe_of(item)
            if r.get("structure"):
                steps.append(["build_structure", {"structure": item}, {"retries": RETRIES}])
            else:
                steps.append(["craft_item", {"skill": r["skill"], "item_key": item, "count": n},
                              {"until_have": {item: n * r["output"]}, "retries": RETRIES}])
        outgoing = defaultdict(list)
        for g, rcv, item, q in bp.deliveries:
            if g == key and item not in PROVIDED:
                outgoing[rcv].append((item, q))
        for rcv, items in sorted(outgoing.items()):
            x, y = layout["spawns"][rcv]
            steps.append(["move_to", {"x": x, "y": y}, {"until_near": [x, y, 2], "retries": RETRIES}])
            for item, q in items:
                steps.append(["transfer_items", {"to_agent": names[rcv], "item": item, "count": q}, {"retries": RETRIES}])
        if role.kind == "client" and bp.goal == "equip":
            steps.append(["equip_item", {"item": bp.target}, {"retries": RETRIES}])
        first = []
        for g, rcv, item, q in bp.deliveries:  # starting items need no production: hand them over first
            if g == key and item in PROVIDED:
                x, y = layout["spawns"][rcv]
                first += [["move_to", {"x": x, "y": y}, {"until_near": [x, y, 2], "retries": RETRIES}],
                          ["transfer_items", {"to_agent": names[rcv], "item": item, "count": q}, {"retries": RETRIES}]]
        sol[names[key]] = first + steps
    return sol


def _role_line(name: str, role: Role) -> str:
    skills = ", ".join(f"{s} {l}" for s, l in sorted(role.skills.items())) or "no special skills"
    does = []
    if role.gathers:
        does.append("gathers " + ", ".join(sorted(role.gathers)))
    if role.crafts:
        does.append("crafts/builds " + ", ".join(i for i, _ in role.crafts))
    if role.kind == "client":
        does.append("is the person the result is for")
    return f"{name} ({role.key}; {skills}) {'; '.join(does)}"


def materialize(bp: Blueprint, layout: dict, task_id: str) -> dict:
    """Blueprint + layout -> task dict (the YAML schema used by tasks/main)."""
    p = bp.params
    names = {k: f"{task_id}_{k}" for k in bp.roles}
    client = names.get("client")
    builder = next((names[k] for k, r in bp.roles.items() if r.builds), None)
    site = layout["site"]
    bucket = p.obf_bucket

    # facts
    recipe_lines = [C.describe_structure(i) if recipe_of(i).get("structure") else C.describe_recipe(i)
                    for i, _ in sorted(bp.crafts.items(), key=lambda kv: depth(kv[0]))]
    loc_lines: dict[str, list[str]] = defaultdict(list)
    for key, role in bp.roles.items():
        for item in sorted(role.gathers):
            loc_lines[key].append(f"{RAW_SOURCE[item]} nodes ({item}) are close to {names[key]}'s start at {layout['spawns'][key]}.")
        for st in role.stations:
            loc_lines[key].append(f"{'the anvil' if st == 'anvil' else 'the stove'} is at {_station_pos(st)}; {names[key]} starts next to it.")
    roster = [_role_line(names[k], r) for k, r in bp.roles.items()]
    plan = [f"{names[g]} hands {q} {i} to {names[r]} (at {layout['spawns'][r]})." for g, r, i, q in bp.deliveries]

    # objective
    if bp.goal == "structure":
        precise = f"Build a {bp.target} within 4 tiles of {site}."
        vague = f"Fortify the site that {builder} is guarding."
        checkpoint = {"name": bp.target, "type": "structure", "structure": bp.target, "count": 1, "near": list(site), "radius": 4}
    elif bp.goal == "equip":
        precise = f"{client} ({CLIENTS.get(bp.target, ('client',))[0]}) must have a {bp.target} equipped."
        vague = f"Get {client} ready for the expedition."
        checkpoint = {"name": f"{bp.target} equipped", "type": "equipped", "agent": client, "item": bp.target}
    else:
        precise = f"{client} must hold at least {bp.goal_qty} {bp.target}."
        vague = f"Help {client} complete their order."
        checkpoint = {"name": bp.target, "type": "has_item", "agent": client, "item": bp.target, "count": bp.goal_qty}

    shared: list[str] = []
    briefs: dict[str, list[str]] = {k: [] for k in bp.roles}
    if bucket == "low":
        shared = ["Recipes:"] + [f"  {l}" for l in recipe_lines] + ["Team:"] + [f"  {l}" for l in roster] + \
                 ["Plan:"] + [f"  {l}" for l in plan] + ["Locations:"] + [f"  {l}" for k in bp.roles for l in loc_lines[k]]
    elif bucket == "mid":
        shared = ["Recipes:"] + [f"  {l}" for l in recipe_lines] + ["Team:"] + [f"  {l}" for l in roster]
        for k in bp.roles:
            briefs[k] += loc_lines[k]
    else:
        for k, role in bp.roles.items():
            briefs[k].append(_role_line(names[k], role))
            if bucket == "high":
                briefs[k] += [C.describe_structure(i) if recipe_of(i).get("structure") else C.describe_recipe(i)
                              for i, _ in role.crafts]
                briefs[k] += loc_lines[k]
    objective = vague if bucket == "max" else precise
    if bucket == "max":
        holder = "client" if "client" in bp.roles else next(k for k, r in bp.roles.items() if r.builds)
        briefs[holder].append(f"What success means: {precise}")
    for k, role in bp.roles.items():
        for item, n in role.holds.items():
            briefs[k].append(f"You carry {PROVIDED[item]} ({n} {item}); someone on the team will need it.")
        if role.restricted:
            briefs[k].append(f"Your role cannot use: {', '.join(role.restricted)}.")

    agents = []
    for key, role in bp.roles.items():
        inv = {"apple": 3}
        inv.update(role.holds)
        spec = {
            "username": names[key], "role": role.key, "spawn": list(layout["spawns"][key]),
            "skills": {**role.skills, "health": 15}, "inventory": inv, "equipment": list(role.tools),
        }
        if role.restricted:
            spec["restricted_tools"] = list(role.restricted)
        if briefs[key]:
            spec["role_brief"] = " ".join(briefs[key])
        agents.append(spec)

    category = "construction" if bp.goal == "structure" else ("coordination" if len(agents) >= 5 else "crafting")
    return {
        "id": task_id,
        "name": f"Generated: {bp.target.replace('_', ' ')} ({p.topology}, depth {p.depth})",
        "category": category,
        "description": (f"A generated {p.topology} supply chain of depth {p.depth}. Raw materials must be gathered, "
                        f"processed by the right specialists and handed along until the goal is met."),
        "objectives": {"primary": objective, "secondary": ["Avoid wasted materials and redundant messages."]},
        "max_rounds": 55,
        "agents": agents,
        "extra_entities": layout["extra_nodes"],
        "relevant_game_context": "\n".join(shared),
        "verifier": {"success": "all", "checkpoints": [checkpoint]},
        "reference_solution": compile_solution(bp, names, layout),
        "world_guide": bucket != "max",
        "generation": {"params": asdict(p), "obfuscation_bucket": bucket, "blueprint": bp.summary()},
    }
