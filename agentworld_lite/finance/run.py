"""Episode runner for finance tasks: the same round-robin protocol and trajectory schema as runner.py,
so `evaluate` (CCE/PAC, failure taxonomy) and `report` work on finance run directories unchanged."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Callable

from ..agents import BaseAgent, Decision, TurnInput
from ..engine import ActionResult
from ..runner import AgentFactory, describe_action, episode_path, make_factory as _base_factory
from ..task import Task, load_tasks
from ..tools import format_call
from .dataset import BASES, PERIODS
from .env import TOOL_NAMES, FinanceEnv, build_env, evaluate, render_observation
from .tools import SYSTEM_PROMPT, tool_specs

SETTINGS: dict[str, dict] = {
    "full": {"chat": True, "docs": True, "whitebox": False},
    "no_comm": {"chat": False, "docs": True, "whitebox": False},
    "no_docs": {"chat": True, "docs": False, "whitebox": False},
    "whitebox": {"chat": True, "docs": True, "whitebox": True},
}


def run_episode(task: Task, factory: AgentFactory, setting: str = "full", seed: int = 7,
                log: Callable[[str], None] | None = None, meta: dict | None = None) -> dict:
    cfg = SETTINGS[setting]
    env = build_env(task)
    names = task.usernames
    agents = {n: factory(task, n) for n in names}
    allowed = [t for t in TOOL_NAMES if cfg["chat"] or t != "send_chat"]
    contexts = {n: task.context_block(n, include_docs=cfg["docs"]) for n in names}

    actions: list[dict] = []
    history: dict[str, list[dict]] = {n: [] for n in names}
    chats: dict[str, list[dict]] = {n: [] for n in names}
    seen: dict[str, int] = {n: 0 for n in names}
    last_call: dict[str, str] = {}
    snapshots = [env.snapshot() | {"checkpoints": evaluate(env, task.verifier)["checkpoints"]}]
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "cost_usd": 0.0}
    success = False
    t0 = time.time()

    for r in range(1, task.max_rounds + 1):
        env.round = r
        for turn, name in enumerate(names, start=1):
            obs = render_observation(env, name, max_rounds=task.max_rounds, history=history[name],
                                     chat_log=chats[name], seen_chat=seen[name], whitebox=cfg["whitebox"],
                                     last_actions=last_call)
            seen[name] = len(chats[name])
            decision = agents[name].act(TurnInput(env, name, r, obs, contexts[name], allowed,
                                                  system_prompt=SYSTEM_PROMPT, tool_specs=tool_specs))
            for k, v in decision.usage.items():
                usage[k] = usage.get(k, 0) + v
            if decision.usage:
                usage["calls"] += 1
            if decision.tool not in allowed:
                result = ActionResult(False, f"Tool '{decision.tool}' is not available in this setting.")
            else:
                result = env.execute(name, decision.tool, decision.args)
            agents[name].observe_result(result)
            ev = evaluate(env, task.verifier)
            rec = {
                "id": f"r{r}.{turn}", "round": r, "turn": turn, "agent": name,
                "tool": decision.tool, "args": decision.args, "ok": result.ok, "message": result.message,
                "effects": result.effects, "cp_done": [c["done"] for c in ev["checkpoints"]],
                "cp_have": [c["have"] for c in ev["checkpoints"]],
                "reasoning": decision.reasoning, "note": decision.note,
            }
            rec["description"] = describe_action(rec)
            actions.append(rec)
            call = format_call(decision.tool, decision.args)
            history[name].append({"round": r, "call": call, "ok": result.ok, "message": result.message})
            last_call[name] = f"{call} -> {'OK' if result.ok else 'FAILED'}"
            chat = result.effects.get("chat")
            if chat:
                msg = {"round": r, "from": name, "to": chat["to"], "text": chat["text"]}
                for n in names:
                    if n == name or chat["to"] in ("all", n):
                        chats[n].append(msg)
            if log:
                log(f"  [{task.id} R{r}] {rec['description'][:160]}")
            if ev["success"]:
                success = True
                break
        env.end_round()
        snapshots.append(env.snapshot() | {"checkpoints": evaluate(env, task.verifier)["checkpoints"]})
        if success:
            break

    final = evaluate(env, task.verifier)
    return {
        "task_id": task.id, "task_name": task.name, "category": task.category, "domain": "finance",
        "setting": setting, "seed": seed, "max_rounds": task.max_rounds, "objective": task.primary_objective,
        "agents": [{"username": a["username"], "role": a.get("role", "")} for a in task.agents],
        "matched": (task.meta.get("matched")),
        "checkpoint_names": [c["name"] for c in final["checkpoints"]],
        "checkpoint_types": [cp["type"] for cp in task.verifier["checkpoints"]],
        "actions": actions, "env_events": env.env_events, "snapshots": snapshots,
        "submission": {k: {"fact_id": v, "value": env.facts[v].value} for k, v in env.submission.items()},
        "result": {
            "success": final["success"], "psr": final["psr"], "message": final["message"],
            "checkpoints": final["checkpoints"], "rounds_used": env.round,
            "n_actions": len(actions), "n_chats": sum(1 for a in actions if a["tool"] == "send_chat" and a["ok"]),
            "n_failed_actions": sum(1 for a in actions if not a["ok"]),
            "deaths": 0, "wall_seconds": round(time.time() - t0, 1),
        },
        "usage": usage,
        "meta": meta or {},
    }


class RandomAnalyst(BaseAgent):
    """Uniform random tool with plausible arguments (random-action baseline for the finance domain)."""

    kind = "random"

    def __init__(self, name: str, seed: int = 0) -> None:
        super().__init__(name)
        self.rng = random.Random(f"{seed}-{name}")

    def act(self, turn: TurnInput) -> Decision:
        env: FinanceEnv = turn.world
        rng, me = self.rng, env.agents[self.name]
        tool = rng.choice(turn.allowed_tools)
        held = [k for k, v in me.inventory.items() if v] or ["none"]
        mates = [n for n in env.agents if n != self.name]
        company = rng.choice([c.stock_code for c in env.dataset.companies.values()] or ["000000"])
        year = int(env.as_of[:4]) - rng.randint(0, 1)
        if tool == "fetch_financials":
            args = {"company": company, "year": year, "period": rng.choice(PERIODS), "basis": rng.choice(BASES)}
        elif tool == "fetch_shares":
            args = {"company": company, "year": year, "period": rng.choice(PERIODS)}
        elif tool == "calculate":
            a, b = rng.choice(held), rng.choice(held)
            args = {"name": f"r{rng.randint(1, 99)}", "formula": rng.choice(["a + b", "a - b", "a / b", "a * b"]),
                    "inputs": [{"var": "a", "fact_id": a}, {"var": "b", "fact_id": b}], "unit": "x"}
        elif tool == "share_facts":
            args = {"to_agent": rng.choice(mates), "fact_ids": [rng.choice(held)]}
        elif tool == "submit_answer":
            args = {"answers": [{"field": rng.choice(["per", "pbr", "ttm_net_income", "q2_revenue", "q2_operating_margin"]),
                                 "fact_id": rng.choice(held)}]}
        elif tool == "send_chat":
            args = {"to": "all", "message": rng.choice(["hello", "what do you need?", "fetching now", "ready"])}
        else:
            args = {"reason": "random"}
        return Decision(tool, args)


def make_factory(kind: str, seed: int = 7, **kw) -> AgentFactory:
    if kind == "random":
        return lambda task, name: RandomAnalyst(name, seed=seed)
    return _base_factory(kind, seed=seed, **kw)


def validate_task(task: Task) -> list[str]:
    """Static checks plus a scripted replay of the reference solution (must succeed) and an idle run (must fail)."""
    errors = []
    names = task.usernames
    for a in task.agents:
        for t in a.get("restricted_tools") or []:
            if t not in TOOL_NAMES:
                errors.append(f"{a['username']}: unknown restricted tool {t}")
    for cp in task.verifier.get("checkpoints", []):
        if cp.get("type") != "answer" or "field" not in cp or "expected" not in cp:
            errors.append(f"bad checkpoint {cp}")
    for n, steps in (task.reference_solution or {}).items():
        if n not in names:
            errors.append(f"reference_solution for unknown agent {n}")
        errors += [f"reference_solution uses unknown tool {s[0]}" for s in steps if s[0] not in TOOL_NAMES]
    if errors:
        return errors
    try:
        build_env(task)
    except (ValueError, KeyError) as exc:
        return [f"cannot build environment: {exc}"]
    if not task.reference_solution:
        return ["no reference_solution"]
    ref = run_episode(task, make_factory("scripted"))
    if not ref["result"]["success"]:
        errors.append(f"reference solution fails: {ref['result']['message']}")
    idle = run_episode(task, lambda t, n: _Idle(n))
    if idle["result"]["success"]:
        errors.append("an idle team already succeeds")
    return errors


class _Idle(BaseAgent):
    def act(self, turn: TurnInput) -> Decision:
        return Decision("wait", {"reason": "idle"})


def run_suite(task_paths, out_dir: str, agent: str = "llm", settings=("full",), seeds=(7,),
              verbose: bool = False, **factory_kw) -> list[dict]:
    """Sequential (local models are the common case); skips episodes that already exist."""
    tasks = load_tasks(task_paths)
    out = Path(out_dir)
    (out / "trajectories").mkdir(parents=True, exist_ok=True)
    model, provider = factory_kw.get("model"), factory_kw.get("provider", "anthropic")
    label = (f"ollama:{model}" if provider == "ollama" else model) if agent == "llm" else agent
    run_meta = {"agent": agent, "model": label, "provider": provider if agent == "llm" else None, "domain": "finance"}
    (out / "run_config.json").write_text(json.dumps(run_meta | {
        "settings": list(settings), "seeds": list(seeds), "tasks": [t.id for t in tasks]}, indent=2), encoding="utf-8")
    results = []
    for seed in seeds:
        factory = None
        for t in tasks:
            for s in settings:
                p = episode_path(out, t.id, s, seed)
                if p.exists():
                    print(f"skip (exists): {p.name}")
                    continue
                factory = factory or make_factory(agent, seed=seed, **factory_kw)
                traj = run_episode(t, factory, setting=s, seed=seed, log=print if verbose else None, meta=run_meta)
                p.write_text(json.dumps(traj, ensure_ascii=False, indent=1), encoding="utf-8")
                r = traj["result"]
                print(f"[{'SUCCESS' if r['success'] else 'fail   '}] {t.id:<24} {s:<10} seed={seed} "
                      f"rounds={r['rounds_used']}/{t.max_rounds} psr={r['psr']:.2f} chats={r['n_chats']} ({r['message']})")
                results.append(traj)
    return results
