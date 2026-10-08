"""Game engine: world state + the 13 high-level API tools.

Each tool call encapsulates a multi-step mechanic, as in the paper (e.g. attack_entity
paths to the target, fights, and collects loot in one call). Every call returns an
ActionResult whose `effects` (inventory / position deltas, chat, kills, builds) are
logged in the trajectory and later used for causal analysis.
"""

from __future__ import annotations

import inspect
from collections import Counter
from dataclasses import dataclass, field

from . import content as C
from .gamemap import VILLAGE_SPAWN, GameMap, biome_at, chebyshev, default_population

MOVE_RANGE = 12       # max path length per move_to, and auto-approach budget of interaction tools
TRANSFER_RANGE = 2    # giver must end within this many tiles of the receiver
STATION_RANGE = 2     # crafting stations must be within this many tiles
KNOCKOUT_ROUNDS = 3   # a defeated agent sits out this many rounds, then respawns in the village
STATION_PROVIDERS = {"anvil": {"anvil"}, "fire": {"stove", "campfire"}}

TOOL_NAMES = (
    "move_to", "attack_entity", "harvest_resource", "craft_item", "transfer_items",
    "send_chat", "eat_food", "equip_item", "buy_item", "sell_item",
    "build_structure", "scan_area", "wait",
)


@dataclass
class AgentState:
    name: str
    role: str
    x: int
    y: int
    skills: dict
    inventory: Counter
    equipment: dict
    max_hp: int
    hp: int
    view_radius: int = 8
    restricted_tools: frozenset = frozenset()
    spawn: tuple = VILLAGE_SPAWN
    knocked_out_until: int = 0
    deaths: int = 0
    kills: Counter = field(default_factory=Counter)

    @property
    def pos(self) -> tuple[int, int]:
        return (self.x, self.y)

    @property
    def knocked_out(self) -> bool:
        return self.hp <= 0

    def level(self, skill: str) -> int:
        return int(self.skills.get(skill, C.DEFAULT_SKILL_LEVEL))

    def has_tool(self, tool: str) -> bool:
        return self.inventory[tool] > 0 or tool in self.equipment.values()

    def attack_power(self) -> int:
        power = 1 + self.level("strength") // 3
        weapon = self.equipment.get("weapon")
        if weapon:
            spec = C.ITEMS[weapon]
            power += spec.get("attack", 0)
            if spec.get("magic_scaling"):
                power += self.level("magic") // 3
        return power

    def defense(self) -> int:
        armor = sum(C.ITEMS[i].get("defense", 0) for s, i in self.equipment.items() if s != "weapon")
        return armor + self.level("health") // 10


@dataclass
class Entity:
    id: str
    kind: str  # resource | mob | npc | station | structure | landmark | plate
    type: str
    x: int
    y: int
    hp: int = 0
    max_hp: int = 0
    remaining: int = 0
    active: bool = True
    respawn_at: int | None = None
    origin: tuple = (0, 0)
    meta: dict = field(default_factory=dict)

    @property
    def pos(self) -> tuple[int, int]:
        return (self.x, self.y)

    def label(self) -> str:
        if self.kind == "mob":
            return f"{self.id} (lvl {C.MOBS[self.type]['level']}, hp {self.hp}/{self.max_hp}{', aggressive' if self.meta.get('aggro_radius', 0) else ''}) at ({self.x},{self.y})"
        if self.kind == "resource":
            r = C.RESOURCES[self.type]
            return f"{self.id} [{r['item']}, needs {r['skill']} {r['level']}{', ' + r['tool'] if r['tool'] else ''}, {self.remaining} left] at ({self.x},{self.y})"
        if self.kind == "plate":
            return f"{self.id} ({self.meta['color']} pressure plate) at ({self.x},{self.y})"
        if self.kind == "structure":
            return f"{self.id} (built by {self.meta.get('builder')}) at ({self.x},{self.y})"
        return f"{self.id} at ({self.x},{self.y})"


@dataclass
class ActionResult:
    ok: bool
    message: str
    effects: dict = field(default_factory=dict)


def max_hp_for(health: int) -> int:
    return 30 + 3 * health


class World:
    def __init__(self, seed: int = 7, populate: bool = True) -> None:
        self.map = GameMap()
        self.round = 0
        self.completed_rounds = 0
        self.entities: dict[str, Entity] = {}
        self.agents: dict[str, AgentState] = {}
        self.inbox: dict[str, list[dict]] = {}
        self.env_events: list[dict] = []
        self._ids: Counter = Counter()
        if populate:
            for spec in default_population(self.map, seed):
                self.add_entity(spec["kind"], spec["type"], spec["pos"])

    # ------------------------------------------------------------------ setup
    def add_entity(self, kind: str, etype: str, pos, entity_id: str | None = None, **meta) -> Entity:
        x, y = int(pos[0]), int(pos[1])
        if entity_id is None:
            self._ids[etype] += 1
            entity_id = f"{etype}#{self._ids[etype]}"
        e = Entity(id=entity_id, kind=kind, type=etype, x=x, y=y, origin=(x, y))
        if kind == "mob":
            spec = C.MOBS[etype]
            e.max_hp = e.hp = int(meta.pop("hp", spec["hp"]))
            e.meta["aggro_radius"] = int(meta.pop("aggro_radius", spec["aggro_radius"]))
        elif kind == "resource":
            e.remaining = C.RESOURCES[etype]["capacity"]
        e.meta.update(meta)
        self.entities[entity_id] = e
        return e

    def add_agent(self, spec: dict) -> AgentState:
        skills = {k: int(v) for k, v in (spec.get("skills") or {}).items()}
        skills.setdefault("health", 10)
        inv = Counter({k: int(v) for k, v in (spec.get("inventory") or {}).items()})
        equipment: dict[str, str] = {}
        for item in spec.get("equipment") or []:
            slot = C.ITEMS[item].get("slot")
            if slot:
                equipment[slot] = item
            else:  # tools listed as equipment simply live in the inventory
                inv[item] += 1
        hp_max = max_hp_for(skills["health"])
        spawn = tuple(spec["spawn"])
        a = AgentState(
            name=spec["username"], role=spec.get("role", ""), x=spawn[0], y=spawn[1],
            skills=skills, inventory=inv, equipment=equipment, max_hp=hp_max,
            hp=int(spec.get("hp", hp_max)), view_radius=int(spec.get("view_radius", 8)),
            restricted_tools=frozenset(spec.get("restricted_tools") or []),
            spawn=tuple(spec.get("respawn_point", VILLAGE_SPAWN)),
        )
        self.agents[a.name] = a
        self.inbox[a.name] = []
        return a

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def can_see(agent: AgentState | None, e: Entity) -> bool:
        """Entities with meta.reveal = {skill: level} are only visible to agents meeting it."""
        reveal = e.meta.get("reveal")
        if not reveal or agent is None:
            return True
        return all(agent.level(s) >= int(l) for s, l in reveal.items())

    def entities_near(self, pos, radius: int, kinds=None, viewer: AgentState | None = None):
        out = [
            e for e in self.entities.values()
            if e.active and chebyshev(pos, e.pos) <= radius and (kinds is None or e.kind in kinds)
            and self.can_see(viewer, e)
        ]
        out.sort(key=lambda e: (chebyshev(pos, e.pos), e.id))
        return out

    def resolve(self, agent: AgentState, target: str, kinds: set[str]):
        """Target may be an entity id ('tree#3') or a type name ('tree' -> nearest active one)."""
        target = (target or "").strip()
        e = self.entities.get(target)
        if e is not None:
            if e.kind not in kinds:
                return None, f"{target} is a {e.kind}, not a valid target for this action."
            if not e.active:
                return None, f"{target} is not available right now (depleted/defeated)."
            return e, ""
        cands = [e for e in self.entities.values()
                 if e.active and e.kind in kinds and e.type == target and self.can_see(agent, e)]
        if not cands:
            return None, f"No active '{target}' found. Use an entity id from your observation (e.g. 'tree#3') or a valid type."
        cands.sort(key=lambda e: (chebyshev(agent.pos, e.pos), e.id))
        return cands[0], ""

    def approach(self, agent: AgentState, target_pos, reach: int):
        if chebyshev(agent.pos, target_pos) <= reach:
            return True, ""
        path = self.map.path_adjacent(agent.pos, target_pos, reach)
        if path is None:
            return False, f"No walkable path to {tuple(target_pos)}."
        if len(path) > MOVE_RANGE:
            return False, (f"Target at {tuple(target_pos)} is {len(path)} steps away (max {MOVE_RANGE} per action). "
                           f"Use move_to to get closer first.")
        agent.x, agent.y = path[-1]
        return True, ""

    def station_near(self, agent: AgentState, station: str) -> bool:
        providers = STATION_PROVIDERS[station]
        return any(e.type in providers for e in self.entities_near(agent.pos, STATION_RANGE, {"station", "structure"}))

    def knock_out(self, agent: AgentState, cause: str) -> None:
        agent.hp = 0
        agent.deaths += 1
        agent.knocked_out_until = self.round + KNOCKOUT_ROUNDS
        agent.x, agent.y = agent.spawn
        self.env_events.append({"round": self.round, "type": "knocked_out", "agent": agent.name, "cause": cause})

    # ------------------------------------------------------------------ dispatch
    def execute(self, agent_name: str, tool: str, args: dict | None) -> ActionResult:
        agent = self.agents[agent_name]
        args = dict(args or {})
        if tool not in TOOL_NAMES:
            return ActionResult(False, f"Unknown tool '{tool}'.")
        if tool in agent.restricted_tools:
            return ActionResult(False, f"Your role cannot use {tool}. Ask a teammate who can.")
        if agent.knocked_out:
            return ActionResult(False, "You are knocked out and cannot act.")
        handler = getattr(self, f"_t_{tool}")
        params = inspect.signature(handler).parameters
        call_args = {k: v for k, v in args.items() if k in params}
        missing = [p for p, v in params.items() if p != "agent" and v.default is inspect._empty and p not in call_args]
        if missing:
            return ActionResult(False, f"Missing argument(s) for {tool}: {', '.join(missing)}.")
        before = self._state_for_diff()
        try:
            ok, msg, extra = handler(agent, **call_args)
        except (TypeError, ValueError) as exc:
            return ActionResult(False, f"Invalid arguments for {tool}: {exc}")
        effects = self._diff(before, self._state_for_diff())
        effects.update(extra)
        return ActionResult(ok, msg, effects)

    def _state_for_diff(self):
        return {
            n: (a.pos, Counter(a.inventory), dict(a.equipment), a.hp)
            for n, a in self.agents.items()
        }

    @staticmethod
    def _diff(before, after) -> dict:
        inv, moves, equip, hp = {}, {}, {}, {}
        for n in after:
            p0, i0, e0, h0 = before[n]
            p1, i1, e1, h1 = after[n]
            delta = {k: i1[k] - i0[k] for k in set(i0) | set(i1) if i1[k] != i0[k]}
            if delta:
                inv[n] = delta
            if p0 != p1:
                moves[n] = [list(p0), list(p1)]
            if e0 != e1:
                equip[n] = e1
            if h0 != h1:
                hp[n] = h1 - h0
        out = {}
        if inv:
            out["inv"] = inv
        if moves:
            out["moves"] = moves
        if equip:
            out["equip"] = equip
        if hp:
            out["hp"] = hp
        return out

    # ------------------------------------------------------------------ tools
    def _t_move_to(self, agent, x, y):
        x, y = int(x), int(y)
        if not self.map.in_bounds(x, y):
            return False, f"({x},{y}) is outside the map (0-63, 0-47).", {}
        if (x, y) == agent.pos:
            return True, f"Already at ({x},{y}).", {}
        if self.map.is_walkable(x, y):
            path = self.map.path_to(agent.pos, (x, y))
        else:
            path = self.map.path_adjacent(agent.pos, (x, y), 1)
        if path is None:
            return False, f"No walkable path to ({x},{y}).", {}
        step = path[:MOVE_RANGE]
        agent.x, agent.y = step[-1]
        if len(path) <= MOVE_RANGE:
            note = "" if self.map.is_walkable(x, y) else " (target tile is not walkable; stopped next to it)"
            return True, f"Moved to ({agent.x},{agent.y}){note}.", {}
        return True, (f"Moved {MOVE_RANGE} tiles toward ({x},{y}); now at ({agent.x},{agent.y}), "
                      f"{len(path) - MOVE_RANGE} steps remaining."), {}

    def _t_attack_entity(self, agent, target):
        mob, err = self.resolve(agent, target, {"mob"})
        if mob is None:
            return False, err, {}
        ok, err = self.approach(agent, mob.pos, 1)
        if not ok:
            return False, err, {}
        spec = C.MOBS[mob.type]
        dealt = taken = 0
        exchanges = 0
        killed = False
        hit = max(1, agent.attack_power() - spec["defense"])
        incoming = max(1, int(mob.meta.get("attack", spec["attack"])) - agent.defense())
        while exchanges < C.MAX_EXCHANGES_PER_ATTACK:
            exchanges += 1
            dmg = min(hit, mob.hp)
            mob.hp -= dmg
            dealt += dmg
            if mob.hp <= 0:
                killed = True
                break
            agent.hp -= incoming
            taken += incoming
            if agent.hp <= 0:
                break
            if agent.hp <= incoming:  # auto-disengage before a lethal hit
                break
        extra = {"damage_dealt": {mob.id: dealt}}
        msg = f"Fought {mob.id}: dealt {dealt} dmg over {exchanges} exchange(s), took {taken} dmg."
        if killed:
            mob.active = False
            mob.meta["killed_by"] = agent.name
            mob.meta["killed_round"] = self.round
            respawn = spec["respawn"]
            mob.respawn_at = self.round + respawn if respawn else None
            agent.kills[mob.type] += 1
            for item, n in spec["drops"].items():
                agent.inventory[item] += n
            loot = ", ".join(f"{n} {i}" for i, n in spec["drops"].items())
            msg += f" {mob.id} defeated! Loot: {loot}."
            extra["killed"] = mob.id
            extra["killed_aggressive"] = mob.meta.get("aggro_radius", 0) > 0
        else:
            msg += f" {mob.id} has {mob.hp}/{mob.max_hp} HP left."
        if agent.hp <= 0:
            self.knock_out(agent, f"killed by {mob.id}")
            msg += " You were knocked out and will respawn in the village."
            extra["knocked_out"] = agent.name
        elif not killed and agent.hp <= incoming:
            msg += f" You disengaged at low health ({agent.hp}/{agent.max_hp} HP)."
        return True, msg, extra

    def _t_harvest_resource(self, agent, target):
        node, err = self.resolve(agent, target, {"resource"})
        if node is None:
            return False, err, {}
        spec = C.RESOURCES[node.type]
        if agent.level(spec["skill"]) < spec["level"]:
            return False, f"{node.type} requires {spec['skill']} level {spec['level']} (you have {agent.level(spec['skill'])}).", {}
        if spec["tool"] and not agent.has_tool(spec["tool"]):
            return False, f"You need a {spec['tool']} to harvest {node.type}.", {}
        ok, err = self.approach(agent, node.pos, 1)
        if not ok:
            return False, err, {}
        qty = 1 + (1 if agent.level(spec["skill"]) >= spec["level"] + C.HARVEST_BONUS_MARGIN else 0)
        agent.inventory[spec["item"]] += qty
        node.remaining -= 1
        msg = f"Harvested {qty} {spec['item']} from {node.id}."
        if node.remaining <= 0:
            node.active = False
            node.respawn_at = self.round + spec["respawn"]
            msg += f" {node.id} is depleted (respawns in {spec['respawn']} rounds)."
        return True, msg, {"harvested_from": node.id}

    def _t_craft_item(self, agent, skill, item_key, count=1):
        item_key = str(item_key).strip().lower()
        skill = str(skill).strip().lower()
        recipe = C.RECIPES.get(item_key)
        if recipe is None:
            return False, f"No recipe for '{item_key}'. Craftable: {', '.join(sorted(C.RECIPES))}.", {}
        if recipe["skill"] != skill:
            return False, f"{item_key} is crafted with skill={recipe['skill']}, not {skill}.", {}
        if agent.level(skill) < recipe["level"]:
            return False, f"{item_key} requires {skill} level {recipe['level']} (you have {agent.level(skill)}).", {}
        if recipe["station"] and not self.station_near(agent, recipe["station"]):
            where = "the anvil in the village (33,19)" if recipe["station"] == "anvil" else "a stove (36,19) or a campfire"
            return False, f"{item_key} must be crafted within {STATION_RANGE} tiles of {where}.", {}
        count = max(1, min(int(count), 20))
        possible = min(agent.inventory[k] // n for k, n in recipe["inputs"].items())
        if possible <= 0:
            need = ", ".join(f"{n} {k} (have {agent.inventory[k]})" for k, n in recipe["inputs"].items())
            return False, f"Missing materials for {item_key}: need {need}.", {}
        done = min(count, possible)
        for k, n in recipe["inputs"].items():
            agent.inventory[k] -= n * done
        agent.inventory[item_key] += recipe["output"] * done
        msg = f"Crafted {recipe['output'] * done} {item_key}."
        if done < count:
            msg += f" (Only had materials for {done} of {count} crafts.)"
        return True, msg, {}

    def _t_transfer_items(self, agent, to_agent, item, count=1):
        to_agent = str(to_agent).strip()
        other = self.agents.get(to_agent)
        if other is None or other is agent:
            return False, f"'{to_agent}' is not a teammate. Teammates: {', '.join(n for n in self.agents if n != agent.name)}.", {}
        if other.knocked_out:
            return False, f"{to_agent} is knocked out.", {}
        count = int(count)
        if count <= 0:
            return False, "Count must be positive.", {}
        if agent.inventory[item] < count:
            return False, f"You only have {agent.inventory[item]} {item}.", {}
        if chebyshev(agent.pos, other.pos) > TRANSFER_RANGE:
            ok, err = self.approach(agent, other.pos, TRANSFER_RANGE)
            if not ok:
                return False, f"{to_agent} is at {other.pos}, too far to hand items over. {err}", {}
        agent.inventory[item] -= count
        other.inventory[item] += count
        return True, f"Gave {count} {item} to {to_agent}.", {}

    def _t_send_chat(self, agent, to, message):
        to = str(to).strip() or "all"
        message = str(message).strip()[:600]
        if not message:
            return False, "Empty message.", {}
        if to != "all" and to not in self.agents:
            return False, f"Unknown recipient '{to}'. Use 'all' or a teammate username.", {}
        recipients = [n for n in self.agents if n != agent.name] if to == "all" else [to]
        for r in recipients:
            self.inbox[r].append({"round": self.round, "from": agent.name, "to": to, "text": message})
        return True, f"Message sent to {to}.", {"chat": {"to": to, "text": message}}

    def _t_eat_food(self, agent, item):
        heal = C.heal_value(item)
        if heal <= 0:
            return False, f"{item} is not edible.", {}
        if agent.inventory[item] <= 0:
            return False, f"You have no {item}.", {}
        agent.inventory[item] -= 1
        before = agent.hp
        agent.hp = min(agent.max_hp, agent.hp + heal)
        return True, f"Ate {item}: HP {before} -> {agent.hp}/{agent.max_hp}.", {}

    def _t_equip_item(self, agent, item):
        spec = C.ITEMS.get(item)
        if spec is None or "slot" not in spec:
            return False, f"{item} cannot be equipped.", {}
        if agent.inventory[item] <= 0:
            return False, f"You have no {item} in your inventory.", {}
        for skill, lvl in spec.get("requires", {}).items():
            if agent.level(skill) < lvl:
                return False, f"{item} requires {skill} level {lvl}.", {}
        slot = spec["slot"]
        prev = agent.equipment.get(slot)
        if prev:
            agent.inventory[prev] += 1
        agent.inventory[item] -= 1
        agent.equipment[slot] = item
        return True, f"Equipped {item} ({slot})" + (f", unequipped {prev}." if prev else "."), {}

    def _shop(self, agent, npc):
        e, err = self.resolve(agent, npc, {"npc"})
        if e is None:
            return None, err
        ok, err = self.approach(agent, e.pos, 1)
        if not ok:
            return None, err
        return e, ""

    def _t_buy_item(self, agent, npc, item, count=1):
        e, err = self._shop(agent, npc)
        if e is None:
            return False, err, {}
        shop = C.SHOPS[e.type]
        if item not in shop["sells"]:
            return False, f"{e.id} does not sell {item}. Sells: {shop['sells']}.", {}
        count = max(1, int(count))
        cost = shop["sells"][item] * count
        if agent.inventory["coins"] < cost:
            return False, f"{count} {item} costs {cost} coins; you have {agent.inventory['coins']}.", {}
        agent.inventory["coins"] -= cost
        agent.inventory[item] += count
        return True, f"Bought {count} {item} from {e.id} for {cost} coins.", {}

    def _t_sell_item(self, agent, npc, item, count=1):
        e, err = self._shop(agent, npc)
        if e is None:
            return False, err, {}
        shop = C.SHOPS[e.type]
        if item not in shop["buys"]:
            return False, f"{e.id} does not buy {item}. Buys: {shop['buys']}.", {}
        count = max(1, int(count))
        if agent.inventory[item] < count:
            return False, f"You only have {agent.inventory[item]} {item}.", {}
        gain = shop["buys"][item] * count
        agent.inventory[item] -= count
        agent.inventory["coins"] += gain
        return True, f"Sold {count} {item} to {e.id} for {gain} coins.", {}

    def _t_build_structure(self, agent, structure):
        spec = C.STRUCTURES.get(structure)
        if spec is None:
            return False, f"Unknown structure '{structure}'. Buildable: {', '.join(C.STRUCTURES)}.", {}
        if agent.level(spec["skill"]) < spec["level"]:
            return False, f"{structure} requires {spec['skill']} level {spec['level']}.", {}
        if biome_at(*agent.pos) == "village":
            return False, "Building is not allowed inside the village.", {}
        if any(e.pos == agent.pos for e in self.entities_near(agent.pos, 0, {"structure", "station", "npc", "resource"})):
            return False, "This tile is occupied; move to an empty tile.", {}
        missing = {k: n for k, n in spec["inputs"].items() if agent.inventory[k] < n}
        if missing:
            need = ", ".join(f"{n} {k} (have {agent.inventory[k]})" for k, n in missing.items())
            return False, f"Missing materials for {structure}: {need}.", {}
        for k, n in spec["inputs"].items():
            agent.inventory[k] -= n
        e = self.add_entity("structure", structure, agent.pos, builder=agent.name, built_round=self.round)
        return True, f"Built {e.id} at {agent.pos}.", {"built": e.id}

    def _t_scan_area(self, agent):
        radius = agent.view_radius * 2
        groups: dict[str, list[Entity]] = {}
        for e in self.entities_near(agent.pos, radius, viewer=agent):
            groups.setdefault(e.type, []).append(e)
        lines = [f"Scan (radius {radius}) around ({agent.x},{agent.y}), biome={biome_at(*agent.pos)}:"]
        for etype, es in sorted(groups.items(), key=lambda kv: chebyshev(agent.pos, kv[1][0].pos)):
            near = ", ".join(f"{e.id}@({e.x},{e.y})" for e in es[:3])
            lines.append(f"- {etype} x{len(es)}: {near}")
        mates = [f"{a.name}@({a.x},{a.y})" for a in self.agents.values()
                 if a is not agent and chebyshev(agent.pos, a.pos) <= radius]
        if mates:
            lines.append("- teammates: " + ", ".join(mates))
        return True, "\n".join(lines), {}

    def _t_wait(self, agent, reason=""):
        return True, "Waited." + (f" ({reason})" if reason else ""), {}

    # ------------------------------------------------------------------ round tick
    def end_round(self) -> None:
        r = self.round
        # hostile mobs strike the nearest agent in their aggro radius
        for mob in self.entities.values():
            radius = mob.meta.get("aggro_radius", 0)
            if mob.kind != "mob" or not mob.active or radius <= 0:
                continue
            targets = [a for a in self.agents.values() if not a.knocked_out and chebyshev(a.pos, mob.pos) <= radius]
            if not targets:
                continue
            victim = min(targets, key=lambda a: (chebyshev(a.pos, mob.pos), a.name))
            dmg = max(1, int(mob.meta.get("attack", C.MOBS[mob.type]["attack"])) - victim.defense())
            victim.hp -= dmg
            self.env_events.append({"round": r, "type": "aggro", "mob": mob.id, "agent": victim.name, "damage": dmg, "hp_after": max(victim.hp, 0)})
            if victim.hp <= 0:
                self.knock_out(victim, f"killed by {mob.id}")
        for e in self.entities.values():
            if e.kind == "mob" and e.active and e.hp < e.max_hp:
                e.hp = min(e.max_hp, e.hp + int(e.meta.get("regen", C.MOBS[e.type]["regen"])))
            if not e.active and e.respawn_at is not None and e.respawn_at <= r:
                e.active = True
                e.respawn_at = None
                e.x, e.y = e.origin
                if e.kind == "mob":
                    e.hp = e.max_hp
                elif e.kind == "resource":
                    e.remaining = C.RESOURCES[e.type]["capacity"]
        for a in self.agents.values():
            if a.knocked_out and a.knocked_out_until <= r:
                a.hp = a.max_hp // 2
                self.env_events.append({"round": r, "type": "respawn", "agent": a.name})
        self.completed_rounds = r

    # ------------------------------------------------------------------ snapshots
    def snapshot(self) -> dict:
        return {
            "round": self.round,
            "agents": {
                n: {
                    "pos": list(a.pos), "hp": a.hp, "max_hp": a.max_hp,
                    "inventory": {k: v for k, v in sorted(a.inventory.items()) if v},
                    "equipment": dict(a.equipment), "knocked_out": a.knocked_out, "deaths": a.deaths,
                }
                for n, a in self.agents.items()
            },
            "structures": [{"id": e.id, "type": e.type, "pos": list(e.pos)} for e in self.entities.values() if e.kind == "structure"],
        }
