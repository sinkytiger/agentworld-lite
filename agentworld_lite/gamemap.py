"""Tile map, biomes, BFS pathfinding and the default (seeded) world population."""

from __future__ import annotations

import random
from collections import deque

WIDTH, HEIGHT = 64, 48

# (name, x0, y0, x1, y1) inclusive rectangles. Later entries win on overlap.
BIOMES: list[tuple[str, int, int, int, int]] = [
    ("plains", 0, 0, WIDTH - 1, HEIGHT - 1),
    ("forest", 0, 2, 20, 46),
    ("goblin_camp", 31, 2, 40, 8),
    ("mines", 42, 1, 62, 17),
    ("dungeon", 46, 21, 62, 45),
    ("village", 25, 17, 39, 30),
    ("lake", 22, 35, 44, 46),
]
WATER_RECT = (22, 35, 44, 46)
# Dungeon has walls with a single entrance on its west side (x=46, y=27..29).
DUNGEON_WALL = (45, 20, 63, 46)
DUNGEON_GATE = [(45, 27), (45, 28), (45, 29)]

VILLAGE_SPAWN = (32, 25)


def in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


def biome_at(x: int, y: int) -> str:
    name = "plains"
    for b, x0, y0, x1, y1 in BIOMES:
        if x0 <= x <= x1 and y0 <= y <= y1:
            name = b
    return name


def chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class GameMap:
    def __init__(self) -> None:
        self.walkable = [[True] * WIDTH for _ in range(HEIGHT)]
        for y in range(HEIGHT):
            for x in range(WIDTH):
                if in_rect(x, y, WATER_RECT):
                    self.walkable[y][x] = False
        # dungeon outer wall (west + north edges) with a gate
        x0, y0, x1, y1 = DUNGEON_WALL
        for y in range(y0, min(y1, HEIGHT - 1) + 1):
            self.walkable[y][x0] = (x0, y) in DUNGEON_GATE
        for x in range(x0, min(x1, WIDTH - 1) + 1):
            self.walkable[y0][x] = False
            self.walkable[HEIGHT - 1][x] = False

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < WIDTH and 0 <= y < HEIGHT

    def is_walkable(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and self.walkable[y][x]

    def neighbors(self, x: int, y: int):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = x + dx, y + dy
            if not self.is_walkable(nx, ny):
                continue
            # no corner cutting through walls/water
            if dx and dy and not (self.is_walkable(x + dx, y) and self.is_walkable(x, y + dy)):
                continue
            yield nx, ny

    def path(self, start: tuple[int, int], goal_test, max_nodes: int = 6000) -> list[tuple[int, int]] | None:
        """BFS (8-connected). goal_test(x, y) -> bool. Returns tiles after start, [] if start is a goal."""
        if goal_test(*start):
            return []
        prev = {start: None}
        q = deque([start])
        while q and len(prev) < max_nodes:
            cur = q.popleft()
            for nb in self.neighbors(*cur):
                if nb in prev:
                    continue
                prev[nb] = cur
                if goal_test(*nb):
                    out = [nb]
                    while prev[out[-1]] != start:
                        out.append(prev[out[-1]])
                    return out[::-1]
                q.append(nb)
        return None

    def path_to(self, start, goal):
        gx, gy = goal
        return self.path(tuple(start), lambda x, y: x == gx and y == gy)

    def path_adjacent(self, start, target, reach: int = 1):
        tx, ty = target
        return self.path(tuple(start), lambda x, y: max(abs(x - tx), abs(y - ty)) <= reach)


# --- default world population ---------------------------------------------------------
# (kind, type, count, rect) — positions sampled with a fixed seed so every run is identical.
POPULATION: list[tuple[str, str, int, tuple[int, int, int, int]]] = [
    ("resource", "tree", 30, (2, 4, 20, 44)),
    ("resource", "berry_bush", 8, (4, 6, 20, 30)),
    ("resource", "herb_patch", 6, (2, 32, 14, 44)),
    ("mob", "wolf", 4, (13, 10, 20, 28)),
    ("resource", "flax_patch", 8, (22, 2, 29, 14)),
    ("resource", "berry_bush", 3, (22, 10, 29, 15)),
    ("mob", "rat", 4, (30, 10, 40, 15)),
    ("mob", "goblin", 4, (32, 3, 40, 7)),
    ("resource", "copper_rock", 8, (42, 2, 52, 9)),
    ("resource", "tin_rock", 8, (42, 10, 52, 17)),
    ("resource", "coal_rock", 5, (53, 2, 62, 8)),
    ("resource", "iron_rock", 5, (53, 9, 62, 14)),
    ("mob", "skeleton", 2, (56, 15, 62, 17)),
    ("mob", "skeleton", 5, (50, 30, 62, 44)),
]

FIXED_ENTITIES: list[tuple[str, str, tuple[int, int]]] = [
    ("npc", "general_store", (27, 19)),
    ("npc", "blacksmith", (31, 19)),
    ("station", "anvil", (33, 19)),
    ("station", "stove", (36, 19)),
    ("npc", "trader", (24, 6)),
    # fishing spots on the north shore of the lake, trout on the west shore
    *[("resource", "fishing_spot", (x, 34)) for x in (24, 28, 32, 36, 40, 43)],
    *[("resource", "trout_spot", (21, y)) for y in (37, 41, 45)],
]


def default_population(gmap: GameMap, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    taken: set[tuple[int, int]] = set()
    out: list[dict] = []
    for kind, etype, pos in FIXED_ENTITIES:
        taken.add(pos)
        out.append({"kind": kind, "type": etype, "pos": pos})
    reserved = {VILLAGE_SPAWN}
    for kind, etype, count, (x0, y0, x1, y1) in POPULATION:
        cands = [
            (x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
            if gmap.is_walkable(x, y) and (x, y) not in taken and (x, y) not in reserved
            and biome_at(x, y) != "village"
        ]
        for pos in rng.sample(cands, min(count, len(cands))):
            taken.add(pos)
            out.append({"kind": kind, "type": etype, "pos": pos})
    return out


WORLD_GUIDE = """World map (64x48 tiles, x grows east, y grows south):
- village (x25-39, y17-30): safe zone. general_store (27,19), blacksmith (31,19), anvil (33,19), stove (36,19), spawn (32,25).
- forest (x0-20, y2-46): trees, berry bushes; herb patches in the south-west; wolves (aggressive) in the east part (x13-20, y10-28).
- plains (north, x22-30, y2-15): flax patches, berry bushes; trader NPC at (24,6). Rats around (x30-40, y10-15).
- goblin_camp (x31-40, y2-8): aggressive goblins (drop beads).
- mines (x42-62, y1-17): copper rocks (north-west), tin rocks (south-west), coal (north-east), iron (east); skeletons at the far south-east.
- lake (x22-44, y35-46): water. Fishing spots along the north shore (y=34), trout spots on the west shore (x=21).
- dungeon (x46-62, y21-45): walled, single gate on the west wall at (45,27)-(45,29). Aggressive skeletons inside.
"""
