"""Episode runner: turn-based round-robin protocol + trajectory logging.

Protocol (paper §3): each round, every agent in a fixed order receives a fresh observation,
selects ONE tool call, and the action resolves before the next agent acts. Episodes end on
success or when the round budget is exhausted. Agents are black boxes to each other; chat
messages routed through the environment are the only shared channel.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .agents import BaseAgent, LLMAgent, RandomAgent, ScriptedAgent, TurnInput
from .engine import TOOL_NAMES, ActionResult
from .llm import ClaudeClient
from .observation import render_observation
from .task import Task, load_tasks
from .tools import format_call
from .verifier import evaluate

# Ablation settings (paper Table 4 / baselines). "whitebox" stands in for the paper's
# oracle-communication upper bound: every agent sees all teammates' full state.
SETTINGS: dict[str, dict] = {
    "full": {"chat": True, "docs": True, "whitebox": False, "random_spawn": False},
    "no_comm": {"chat": False, "docs": True, "whitebox": False, "random_spawn": False},
    "no_docs": {"chat": True, "docs": False, "whitebox": False, "random_spawn": False},
    "whitebox": {"chat": True, "docs": True, "whitebox": True, "random_spawn": False},
    "random_spawn": {"chat": True, "docs": True, "whitebox": False, "random_spawn": True},
}

AgentFactory = Callable[[Task, str], BaseAgent]


def describe_action(rec: dict) -> str:
    status = "OK" if rec["ok"] else "FAILED"
    return f"{rec['agent']}: {format_call(rec['tool'], rec['args'])} -> {status}: {rec['message']}"


def run_episode(task: Task, factory: AgentFactory, setting: str = "full", seed: int = 7,
                log: Callable[[str], None] | None = None, meta: dict | None = None) -> dict:
    cfg = SETTINGS[setting]
    world = task.build_world(seed=seed, random_spawn=cfg["random_spawn"])
    names = task.usernames
    agents = {n: factory(task, n) for n in names}
    allowed = [t for t in TOOL_NAMES if cfg["chat"] or t != "send_chat"]
    contexts = {n: task.context_block(n, include_docs=cfg["docs"]) for n in names}

    actions: list[dict] = []
    history: dict[str, list[dict]] = {n: [] for n in names}
    chats: dict[str, list[dict]] = {n: [] for n in names}
    seen: dict[str, int] = {n: 0 for n in names}
    last_call: dict[str, str] = {}
    snapshots = [world.snapshot() | {"checkpoints": evaluate(world, task.verifier)["checkpoints"]}]
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "cost_usd": 0.0}
    success = False
    t0 = time.time()

    for r in range(1, task.max_rounds + 1):
        world.round = r
        for turn, name in enumerate(names, start=1):
            me = world.agents[name]
            if me.knocked_out:
                continue
            obs = render_observation(
                world, name, max_rounds=task.max_rounds, history=history[name], chat_log=chats[name],
                seen_chat=seen[name], whitebox=cfg["whitebox"], last_actions=last_call,
            )
            seen[name] = len(chats[name])
            decision = agents[name].act(TurnInput(world, name, r, obs, contexts[name], allowed))
            for k, v in decision.usage.items():
                usage[k] = usage.get(k, 0) + v
            if decision.usage:
                usage["calls"] += 1
            if decision.tool not in allowed:
                result = ActionResult(False, f"Tool '{decision.tool}' is not available in this setting.")
            else:
                result = world.execute(name, decision.tool, decision.args)
            agents[name].observe_result(result)
            ev = evaluate(world, task.verifier)
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
        if not success:
            world.end_round()
        ev = evaluate(world, task.verifier)  # tick-based checkpoints (e.g. rounds_survived) flip here
        snapshots.append(world.snapshot() | {"checkpoints": ev["checkpoints"]})
        if success or ev["success"]:
            success = True
            break

    final = evaluate(world, task.verifier)
    return {
        "task_id": task.id, "task_name": task.name, "category": task.category, "setting": setting, "seed": seed,
        "max_rounds": task.max_rounds, "objective": task.primary_objective,
        "agents": [{"username": a["username"], "role": a.get("role", "")} for a in task.agents],
        "checkpoint_names": [c["name"] for c in final["checkpoints"]],
        "checkpoint_types": [cp["type"] for cp in task.verifier["checkpoints"]],
        "actions": actions, "env_events": world.env_events, "snapshots": snapshots,
        "result": {
            "success": final["success"], "psr": final["psr"], "message": final["message"],
            "checkpoints": final["checkpoints"], "rounds_used": world.round,
            "n_actions": len(actions), "n_chats": sum(1 for a in actions if a["tool"] == "send_chat" and a["ok"]),
            "n_failed_actions": sum(1 for a in actions if not a["ok"]),
            "deaths": sum(a.deaths for a in world.agents.values()),
            "wall_seconds": round(time.time() - t0, 1),
        },
        "usage": usage,
        "meta": meta or {},
    }


# ---------------------------------------------------------------------- factories
def make_factory(kind: str, model: str | None = None, effort: str | None = None, seed: int = 7,
                 strict_tools: bool = True, use_fallbacks: bool = True, provider: str = "anthropic",
                 host: str | None = None, num_ctx: int | None = None, think: bool | None = None) -> AgentFactory:
    if kind == "llm" and provider == "ollama":
        from .local_llm import DEFAULT_HOST, DEFAULT_NUM_CTX, OllamaAgent, OllamaClient
        local = OllamaClient(model, host=host or DEFAULT_HOST, num_ctx=num_ctx or DEFAULT_NUM_CTX, think=think, seed=seed)
        local.check()
        return lambda task, name: OllamaAgent(name, local)
    if kind == "llm":
        client = ClaudeClient(model=model, effort=effort, use_fallbacks=use_fallbacks)
        return lambda task, name: LLMAgent(name, client, strict_tools=strict_tools)
    if kind == "scripted":
        def f(task, name):
            if not task.reference_solution:
                raise ValueError(f"{task.id} has no reference_solution")
            return ScriptedAgent(name, task.reference_solution.get(name, []))
        return f
    if kind == "random":
        return lambda task, name: RandomAgent(name, seed=seed)
    raise ValueError(f"unknown agent kind {kind}")


def episode_path(out_dir: Path, task_id: str, setting: str, seed: int) -> Path:
    return out_dir / "trajectories" / f"{task_id}__{setting}__s{seed}.json"


def run_suite(task_paths, out_dir: str, agent: str = "llm", model: str | None = None, effort: str | None = None,
              settings=("full",), seeds=(7,), workers: int = 4, verbose: bool = False,
              strict_tools: bool = True, use_fallbacks: bool = True, task_filter: list[str] | None = None,
              provider: str = "anthropic", host: str | None = None, num_ctx: int | None = None,
              think: bool | None = None) -> list[dict]:
    tasks = load_tasks(task_paths)
    if task_filter:
        tasks = [t for t in tasks if t.id in task_filter]
    out = Path(out_dir)
    (out / "trajectories").mkdir(parents=True, exist_ok=True)
    label = (f"ollama:{model}" if provider == "ollama" else model) if agent == "llm" else agent
    run_meta = {"agent": agent, "model": label, "effort": effort, "provider": provider if agent == "llm" else None,
                "think": think}
    (out / "run_config.json").write_text(json.dumps(run_meta | {
        "settings": list(settings), "seeds": list(seeds), "tasks": [t.id for t in tasks]}, indent=2), encoding="utf-8")

    jobs = []
    for t in tasks:
        for s in settings:
            for seed in seeds:
                p = episode_path(out, t.id, s, seed)
                if p.exists():
                    print(f"skip (exists): {p.name}")
                    continue
                jobs.append((t, s, seed, p))

    factories: dict[int, AgentFactory] = {}

    def factory_for(seed):
        if seed not in factories:
            factories[seed] = make_factory(agent, model, effort, seed, strict_tools, use_fallbacks,
                                           provider=provider, host=host, num_ctx=num_ctx, think=think)
        return factories[seed]

    def job(t, s, seed, p):
        traj = run_episode(t, factory_for(seed), setting=s, seed=seed,
                           log=print if verbose else None, meta=run_meta)
        p.write_text(json.dumps(traj, ensure_ascii=False, indent=1), encoding="utf-8")
        r = traj["result"]
        print(f"[{'SUCCESS' if r['success'] else 'fail   '}] {t.id:<24} {s:<12} seed={seed} "
              f"rounds={r['rounds_used']}/{t.max_rounds} psr={r['psr']:.2f} chats={r['n_chats']} "
              f"cost=${traj['usage'].get('cost_usd', 0):.3f}  ({r['message']})")
        return traj

    results = []
    if workers <= 1 or len(jobs) <= 1:
        results = [job(*j) for j in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(job, *j) for j in jobs]
            for f in as_completed(futs):
                results.append(f.result())
    return results
