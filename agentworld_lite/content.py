"""Static game content for the AgentWorld-Lite sandbox.

The AgentWorld paper runs on Kaetram (380+ items, 144 mob types, 8 gathering /
production skills). This module is a deliberately small subset that keeps the
same *kinds* of mechanics: skill-gated harvesting, multi-stage crafting with
stations, combat with loot, NPC shops, and buildable structures.
"""

from __future__ import annotations

# Eight production skills (same set as the paper) + three combat/role stats.
PRODUCTION_SKILLS = (
    "lumberjacking", "mining", "fishing", "foraging",
    "crafting", "smithing", "fletching", "cooking",
)
COMBAT_STATS = ("strength", "health", "magic")
SKILLS = PRODUCTION_SKILLS + COMBAT_STATS
DEFAULT_SKILL_LEVEL = 1

# kind: material | tool | weapon | armor | food | currency | quest
ITEMS: dict[str, dict] = {
    # raw materials
    "logs": {"kind": "material"},
    "copper_ore": {"kind": "material"},
    "tin_ore": {"kind": "material"},
    "iron_ore": {"kind": "material"},
    "coal": {"kind": "material"},
    "shrimp": {"kind": "material"},
    "trout": {"kind": "material"},
    "flax": {"kind": "material"},
    "herb": {"kind": "material"},
    "hide": {"kind": "material"},
    "bone": {"kind": "material"},
    "bead": {"kind": "material"},
    "ogre_tooth": {"kind": "quest"},
    # intermediates
    "stick": {"kind": "material"},
    "plank": {"kind": "material"},
    "string": {"kind": "material"},
    "leather": {"kind": "material"},
    "bronze_bar": {"kind": "material"},
    "iron_bar": {"kind": "material"},
    "nails": {"kind": "material"},
    # gathering tools (needed in inventory or equipped)
    "axe": {"kind": "tool"},
    "pickaxe": {"kind": "tool"},
    "fishing_rod": {"kind": "tool"},
    # weapons / armor
    "bronze_sword": {"kind": "weapon", "slot": "weapon", "attack": 6},
    "iron_sword": {"kind": "weapon", "slot": "weapon", "attack": 12, "requires": {"strength": 15}},
    "staff": {"kind": "weapon", "slot": "weapon", "attack": 4, "requires": {"magic": 20}, "magic_scaling": True},
    "bow": {"kind": "weapon", "slot": "weapon", "attack": 8, "requires": {"fletching": 10}},
    "leatherarmor": {"kind": "armor", "slot": "armor", "defense": 3},
    "leatherboots": {"kind": "armor", "slot": "boots", "defense": 1},
    # food / potions
    "apple": {"kind": "food", "heal": 10},
    "berries": {"kind": "food", "heal": 5},
    "cooked_shrimp": {"kind": "food", "heal": 20},
    "cooked_trout": {"kind": "food", "heal": 35},
    "fish_stew": {"kind": "food", "heal": 60},
    "herbal_tonic": {"kind": "food", "heal": 45},
    "flask": {"kind": "food", "heal": 30},
    # misc
    "coins": {"kind": "currency"},
    "red_crystal": {"kind": "quest"},
    "blue_crystal": {"kind": "quest"},
    "green_crystal": {"kind": "quest"},
    "yellow_crystal": {"kind": "quest"},
}

# output item -> recipe. station: None | "anvil" | "fire" (agent must be within 2 tiles).
RECIPES: dict[str, dict] = {
    "stick": {"skill": "fletching", "level": 5, "inputs": {"logs": 1}, "output": 4, "station": None},
    "bow": {"skill": "fletching", "level": 10, "inputs": {"stick": 3, "string": 1}, "output": 1, "station": None},
    "staff": {"skill": "crafting", "level": 10, "inputs": {"stick": 5, "bead": 1}, "output": 1, "station": None},
    "string": {"skill": "crafting", "level": 1, "inputs": {"flax": 2}, "output": 1, "station": None},
    "plank": {"skill": "crafting", "level": 5, "inputs": {"logs": 2}, "output": 1, "station": None},
    "leather": {"skill": "crafting", "level": 5, "inputs": {"hide": 1}, "output": 1, "station": None},
    "leatherarmor": {"skill": "crafting", "level": 15, "inputs": {"leather": 3, "string": 1}, "output": 1, "station": None},
    "bronze_bar": {"skill": "smithing", "level": 1, "inputs": {"copper_ore": 1, "tin_ore": 1}, "output": 1, "station": "anvil"},
    "iron_bar": {"skill": "smithing", "level": 15, "inputs": {"iron_ore": 1, "coal": 1}, "output": 1, "station": "anvil"},
    "nails": {"skill": "smithing", "level": 5, "inputs": {"bronze_bar": 1}, "output": 10, "station": "anvil"},
    "bronze_sword": {"skill": "smithing", "level": 5, "inputs": {"bronze_bar": 2, "stick": 1}, "output": 1, "station": "anvil"},
    "iron_sword": {"skill": "smithing", "level": 20, "inputs": {"iron_bar": 2, "leather": 1}, "output": 1, "station": "anvil"},
    "cooked_shrimp": {"skill": "cooking", "level": 1, "inputs": {"shrimp": 1}, "output": 1, "station": "fire"},
    "cooked_trout": {"skill": "cooking", "level": 15, "inputs": {"trout": 1}, "output": 1, "station": "fire"},
    "fish_stew": {"skill": "cooking", "level": 10, "inputs": {"cooked_shrimp": 2, "berries": 1}, "output": 1, "station": "fire"},
    "herbal_tonic": {"skill": "cooking", "level": 5, "inputs": {"herb": 2}, "output": 1, "station": "fire"},
}

# Buildable structures (construction category). Built on the builder's tile.
STRUCTURES: dict[str, dict] = {
    "campfire": {"skill": "crafting", "level": 1, "inputs": {"logs": 3}, "provides_station": "fire"},
    "palisade_wall": {"skill": "crafting", "level": 10, "inputs": {"plank": 3, "nails": 10}, "provides_station": None},
    "watchtower": {"skill": "crafting", "level": 20, "inputs": {"plank": 6, "nails": 20, "iron_bar": 2}, "provides_station": None},
}

# Harvestable resource nodes. capacity = harvests before depletion; respawn in rounds.
RESOURCES: dict[str, dict] = {
    "tree": {"skill": "lumberjacking", "level": 1, "tool": "axe", "item": "logs", "capacity": 4, "respawn": 6},
    "copper_rock": {"skill": "mining", "level": 1, "tool": "pickaxe", "item": "copper_ore", "capacity": 3, "respawn": 8},
    "tin_rock": {"skill": "mining", "level": 1, "tool": "pickaxe", "item": "tin_ore", "capacity": 3, "respawn": 8},
    "coal_rock": {"skill": "mining", "level": 10, "tool": "pickaxe", "item": "coal", "capacity": 3, "respawn": 10},
    "iron_rock": {"skill": "mining", "level": 15, "tool": "pickaxe", "item": "iron_ore", "capacity": 3, "respawn": 10},
    "fishing_spot": {"skill": "fishing", "level": 1, "tool": "fishing_rod", "item": "shrimp", "capacity": 5, "respawn": 5},
    "trout_spot": {"skill": "fishing", "level": 15, "tool": "fishing_rod", "item": "trout", "capacity": 4, "respawn": 6},
    "berry_bush": {"skill": "foraging", "level": 1, "tool": None, "item": "berries", "capacity": 3, "respawn": 6},
    "flax_patch": {"skill": "foraging", "level": 5, "tool": None, "item": "flax", "capacity": 3, "respawn": 6},
    "herb_patch": {"skill": "foraging", "level": 10, "tool": None, "item": "herb", "capacity": 3, "respawn": 8},
}
HARVEST_BONUS_MARGIN = 20  # +1 yield when skill >= required level + margin

# Mobs. aggro_radius > 0 means the mob attacks nearby agents at the end of every round.
MOBS: dict[str, dict] = {
    "rat": {"level": 1, "hp": 12, "attack": 2, "defense": 0, "drops": {"coins": 2}, "aggro_radius": 0, "regen": 0, "respawn": 6},
    "goblin": {"level": 6, "hp": 35, "attack": 5, "defense": 2, "drops": {"coins": 8, "bead": 1}, "aggro_radius": 2, "regen": 0, "respawn": 10},
    "wolf": {"level": 10, "hp": 50, "attack": 7, "defense": 3, "drops": {"hide": 1, "bone": 1}, "aggro_radius": 2, "regen": 0, "respawn": 10},
    "skeleton": {"level": 18, "hp": 90, "attack": 11, "defense": 6, "drops": {"bone": 2, "coins": 15}, "aggro_radius": 3, "regen": 0, "respawn": 12},
    "ogre": {"level": 35, "hp": 320, "attack": 16, "defense": 8, "drops": {"ogre_tooth": 1, "coins": 100}, "aggro_radius": 0, "regen": 30, "respawn": None},
}
MAX_EXCHANGES_PER_ATTACK = 4  # one attack_entity call resolves at most this many hit exchanges

# NPC shops: sells = price the agent pays, buys = price the agent receives.
SHOPS: dict[str, dict] = {
    "general_store": {
        "sells": {"apple": 2, "flask": 10, "axe": 15, "pickaxe": 20, "fishing_rod": 15, "leatherboots": 12},
        "buys": {"logs": 1, "berries": 1, "shrimp": 2, "cooked_shrimp": 4, "hide": 5, "bone": 3, "leather": 8, "flax": 1},
    },
    "blacksmith": {
        "sells": {"bronze_bar": 15, "bronze_sword": 40, "iron_sword": 90},
        "buys": {"copper_ore": 3, "tin_ore": 3, "iron_ore": 8, "coal": 5, "bronze_bar": 10, "iron_bar": 25, "bronze_sword": 30},
    },
    "trader": {
        "sells": {"bead": 40, "leatherarmor": 60, "string": 6},
        "buys": {"ogre_tooth": 200, "bead": 20, "herbal_tonic": 12, "fish_stew": 15},
    },
}

LANDMARKS = ("ancient_shrine",)
PLATE_COLORS = ("red", "blue", "green", "yellow")


def heal_value(item: str) -> int:
    return ITEMS.get(item, {}).get("heal", 0)


def describe_recipe(item: str) -> str:
    r = RECIPES[item]
    mats = " + ".join(f"{n}x {k}" for k, n in r["inputs"].items())
    station = f", near {r['station']}" if r["station"] else ""
    return f"{item}: craft_item skill={r['skill']} (lvl {r['level']}{station}) | {mats} -> {r['output']}x {item}"


def describe_structure(name: str) -> str:
    s = STRUCTURES[name]
    mats = " + ".join(f"{n}x {k}" for k, n in s["inputs"].items())
    return f"{name}: build_structure (crafting lvl {s['level']}) | {mats}"
