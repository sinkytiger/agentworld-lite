from agentworld_lite.engine import MOVE_RANGE, World


def make_world(**agent):
    w = World()
    spec = {"username": "a", "spawn": [30, 24], "skills": {}, "inventory": {}}
    spec.update(agent)
    w.add_agent(spec)
    w.add_agent({"username": "b", "spawn": [31, 24]})
    w.round = 1
    return w


def test_move_is_capped_and_avoids_water():
    w = make_world(spawn=[30, 30])
    r = w.execute("a", "move_to", {"x": 30, "y": 47})  # straight south crosses the lake
    assert r.ok
    a = w.agents["a"]
    assert not (22 <= a.x <= 44 and 35 <= a.y <= 46)
    assert "moves" in r.effects and len(r.effects["moves"]) == 1
    assert max(abs(a.x - 30), abs(a.y - 30)) <= MOVE_RANGE


def test_harvest_needs_tool_and_skill():
    w = make_world(spawn=[12, 22], skills={"lumberjacking": 25})
    r = w.execute("a", "harvest_resource", {"target": "tree"})
    assert not r.ok and "axe" in r.message
    w.agents["a"].inventory["axe"] = 1
    r = w.execute("a", "harvest_resource", {"target": "tree"})
    assert r.ok and r.effects["inv"]["a"]["logs"] == 2  # +1 bonus at level >= 21


def test_smithing_requires_anvil():
    w = make_world(spawn=[12, 22], skills={"smithing": 10}, inventory={"copper_ore": 1, "tin_ore": 1})
    r = w.execute("a", "craft_item", {"skill": "smithing", "item_key": "bronze_bar", "count": 1})
    assert not r.ok and "anvil" in r.message
    w.agents["a"].x, w.agents["a"].y = 33, 20
    r = w.execute("a", "craft_item", {"skill": "smithing", "item_key": "bronze_bar", "count": 1})
    assert r.ok and w.agents["a"].inventory["bronze_bar"] == 1


def test_transfer_auto_approach_and_range():
    w = make_world(inventory={"logs": 3})
    w.agents["b"].x, w.agents["b"].y = 36, 24
    r = w.execute("a", "transfer_items", {"to_agent": "b", "item": "logs", "count": 2})
    assert r.ok and w.agents["b"].inventory["logs"] == 2
    w.agents["b"].x, w.agents["b"].y = 5, 5
    r = w.execute("a", "transfer_items", {"to_agent": "b", "item": "logs", "count": 1})
    assert not r.ok and "too far" in r.message


def test_combat_kill_loot_and_knockout():
    w = make_world(spawn=[34, 9], skills={"strength": 30}, equipment=["iron_sword"])
    r = w.execute("a", "attack_entity", {"target": "goblin"})
    assert r.ok and "defeated" in r.message and w.agents["a"].inventory["bead"] == 1
    weak = make_world(spawn=[34, 9], hp=3)
    r = weak.execute("a", "attack_entity", {"target": "goblin"})
    assert weak.agents["a"].knocked_out and weak.agents["a"].deaths == 1
    assert not weak.execute("a", "wait", {"reason": "x"}).ok


def test_restricted_tool_and_hidden_entity():
    w = make_world(restricted_tools=["buy_item"], inventory={"coins": 50})
    assert not w.execute("a", "buy_item", {"npc": "general_store", "item": "apple", "count": 1}).ok
    shrine = w.add_entity("landmark", "ancient_shrine", (30, 26), entity_id="s", reveal={"foraging": 20})
    a = w.agents["a"]
    assert shrine not in w.entities_near(a.pos, 5, viewer=a)
    a.skills["foraging"] = 25
    assert shrine in w.entities_near(a.pos, 5, viewer=a)


def test_aggro_damage_at_round_end():
    w = make_world(spawn=[34, 6])  # within 2 tiles of goblin#2 (33,5)
    hp = w.agents["a"].hp
    w.end_round()
    assert w.agents["a"].hp < hp
    assert any(e["type"] == "aggro" for e in w.env_events)
