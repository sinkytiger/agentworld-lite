"""Episode runner: turn-based round-robin protocol + trajectory logging.

Protocol (paper §3): each round, every agent in a fixed order receives a fresh observation,
selects ONE tool call, and the action resolves before the next agent acts. Episodes end on
success or when the round budget is exhausted. Agents are black boxes to each other; chat
messages routed through the environment are the only shared channel.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .agents import BaseAgent, LLMAgent, RandomAgent, ScriptedAgent, TurnInput
from .engine import TOOL_NAMES, ActionResult
from .llm import DEFAULT_MODEL, ClaudeClient
from .observation import render_observation, visible_state
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
    "relay": {"chat": False, "docs": True, "whitebox": False, "random_spawn": False},
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
    if setting == "relay":
        for item in ((task.meta.get("generation") or {}).get("matched") or {}).get("relay_messages", []):
            if item["from"] not in names or item["to"] not in names:
                raise ValueError(f"relay message refers to an unknown agent: {item}")
            msg = {"round": 0, "from": item["from"], "to": item["to"], "text": item["text"]}
            chats[item["from"]].append(msg)
            if item["to"] != item["from"]:
                chats[item["to"]].append(msg)
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
            decision = agents[name].act(TurnInput(world, name, r, obs, contexts[name], allowed,
                                                 visible_state=visible_state(world, name)))
            for k, v in decision.usage.items():
                if k != "calls":
                    usage[k] = usage.get(k, 0) + v
            if decision.usage:
                usage["calls"] += decision.usage.get("calls", 1)
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
                "usage": decision.usage, "decision_trace": decision.decision_trace,
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
        "matched": ((task.meta.get("generation") or {}).get("matched")),
        "checkpoint_names": [c["name"] for c in final["checkpoints"]],
        "checkpoint_types": [cp["type"] for cp in task.verifier["checkpoints"]],
        "actions": actions, "env_events": world.env_events, "snapshots": snapshots,
        "result": {
            "success": final["success"], "psr": final["psr"], "message": final["message"],
            "checkpoints": final["checkpoints"], "rounds_used": world.round,
            "n_actions": len(actions), "n_chats": sum(1 for a in actions if a["tool"] == "send_chat" and a["ok"]),
            "n_failed_actions": sum(1 for a in actions if not a["ok"]),
            "n_wait_actions": sum(1 for a in actions if a["tool"] == "wait"),
            "controller_replans": sum(a["decision_trace"].get("extra_decisions", 0) for a in actions),
            "controller_suppressed": sum(bool(a["decision_trace"].get("suppressed")) for a in actions),
            "deaths": sum(a.deaths for a in world.agents.values()),
            "wall_seconds": round(time.time() - t0, 1),
        },
        "usage": usage,
        "meta": meta or {},
    }


# ---------------------------------------------------------------------- factories
def make_factory(kind: str, model: str | None = None, effort: str | None = None, seed: int = 7,
                 strict_tools: bool = True, use_fallbacks: bool = True, provider: str = "anthropic",
                 host: str | None = None, num_ctx: int | None = None, think: bool | None = None,
                 coordination: bool = False, coordination_config: dict | None = None) -> AgentFactory:
    if coordination and kind != "llm":
        raise ValueError("coordination is only available for LLM agents")
    if coordination_config and not coordination:
        raise ValueError("coordination_config requires coordination=True")
    if coordination:
        from .coordination import CoordinationAgent, CoordinationConfig

        controller_options = CoordinationConfig(**(coordination_config or {})).to_dict()

        def wrap(agent: BaseAgent) -> BaseAgent:
            return CoordinationAgent(agent, **controller_options)
    else:
        def wrap(agent: BaseAgent) -> BaseAgent:
            return agent
    if kind == "llm" and provider == "ollama":
        from .local_llm import DEFAULT_HOST, DEFAULT_NUM_CTX, OllamaAgent, OllamaClient
        if not model:
            raise ValueError("Ollama requires an explicit model name")
        local = OllamaClient(model, host=host or DEFAULT_HOST, num_ctx=num_ctx or DEFAULT_NUM_CTX, think=think, seed=seed)
        local.check()
        return lambda task, name: wrap(OllamaAgent(name, local))
    if kind == "llm":
        client = ClaudeClient(model=model or DEFAULT_MODEL, effort=effort, use_fallbacks=use_fallbacks)
        return lambda task, name: wrap(LLMAgent(name, client, strict_tools=strict_tools))
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


def _task_hash(task: Task) -> str:
    payload = json.dumps(task.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    """Never leave a partial final JSON file that a later run could mistake for complete."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _execution_meta(agent, model, effort, provider, host, num_ctx, think,
                    strict_tools, use_fallbacks, coordination, coordination_config) -> dict:
    from .local_llm import DEFAULT_HOST, DEFAULT_NUM_CTX

    if provider not in ("anthropic", "ollama"):
        raise ValueError(f"unknown provider {provider}")
    is_llm = agent == "llm"
    is_ollama = is_llm and provider == "ollama"
    if is_ollama and not model:
        raise ValueError("Ollama requires an explicit model name")
    if coordination_config and not coordination:
        raise ValueError("coordination_config requires coordination=True")
    controller, version = None, None
    if coordination:
        from .coordination import CoordinationConfig, POLICY_VERSION
        controller = CoordinationConfig(**(coordination_config or {})).to_dict()
        version = POLICY_VERSION
    return {
        "agent": agent,
        "model": (f"ollama:{model}" if is_ollama else model or DEFAULT_MODEL) if is_llm else agent,
        "provider": provider if is_llm else None,
        "effort": effort if is_llm and not is_ollama else None,
        "think": think if is_ollama else None,
        "host": (host or DEFAULT_HOST).rstrip("/") if is_ollama else None,
        "num_ctx": num_ctx or DEFAULT_NUM_CTX if is_ollama else None,
        "strict_tools": bool(strict_tools) if is_llm and not is_ollama else None,
        "use_fallbacks": bool(use_fallbacks) if is_llm and not is_ollama else None,
        "coordination": bool(coordination),
        "coordination_version": version,
        "coordination_config": controller,
    }


def _saved_signature(meta: dict) -> dict:
    """Normalize known historical baseline defaults, without inferring task equality."""
    agent = meta.get("agent")
    model = meta.get("model")
    if not agent or not model:
        raise ValueError("saved run is missing agent/model metadata; use a new --out directory")
    provider = meta.get("provider") or ("ollama" if str(model).startswith("ollama:") else "anthropic")
    raw_model = str(model).removeprefix("ollama:") if provider == "ollama" else model
    signature = _execution_meta(
        agent, raw_model, meta.get("effort"), provider, meta.get("host"), meta.get("num_ctx"),
        meta.get("think"), meta.get("strict_tools", True), meta.get("use_fallbacks", True),
        meta.get("coordination", False), meta.get("coordination_config"),
    )
    # A previous controller implementation is never equivalent to today's defaults.
    if meta.get("coordination"):
        signature["coordination_version"] = meta.get("coordination_version", "legacy")
    return signature


def _check_signature(saved: dict, expected: dict, source: str) -> None:
    signature = _saved_signature(saved)
    different = [key for key in expected if signature.get(key) != expected[key]]
    if different:
        raise ValueError(f"{source} has a different run configuration ({', '.join(different)}); "
                         "use a new --out directory")


def _validate_saved_episode(path: Path, expected: dict, tasks: dict[str, Task],
                            hashes: dict[str, str], allow_legacy_resume: bool) -> bool:
    try:
        traj = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(traj, dict) or not isinstance(traj.get("actions"), list)
                or not isinstance(traj.get("result"), dict)
                or "success" not in traj["result"] or "rounds_used" not in traj["result"]):
            raise ValueError("missing completed episode data")
        identity = episode_path(path.parent.parent, traj["task_id"], traj["setting"], traj["seed"])
        if identity.name != path.name:
            raise ValueError("filename does not match task/setting/seed")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Cannot resume: {path.name} is invalid ({exc}); move or repair the file first") from exc
    meta = traj.get("meta") or {}
    _check_signature(meta, expected, path.name)
    task_id = traj["task_id"]
    saved_hash = meta.get("task_hash")
    if saved_hash:
        if task_id in hashes and saved_hash != hashes[task_id]:
            raise ValueError(f"task {task_id} changed since {path.name}; use a new --out directory")
        return False
    if meta.get("coordination"):
        raise ValueError("legacy coordination episodes require a new --out directory")
    if not allow_legacy_resume:
        raise ValueError(f"{path.name} has no task hash. Full task equality cannot be verified. "
                         "Use a new --out directory, or --allow-legacy-resume to accept this limitation.")
    if task_id in tasks:
        task = tasks[task_id]
        observed_identity = {
            "task_name": task.name, "category": task.category,
            "objective": task.primary_objective, "max_rounds": task.max_rounds,
            "agents": [{"username": a["username"], "role": a.get("role", "")} for a in task.agents],
            "matched": ((task.meta.get("generation") or {}).get("matched")),
        }
        if any(traj.get(key) != value for key, value in observed_identity.items()):
            raise ValueError(f"task {task_id} differs from its saved legacy episode; use a new --out directory")
    return True


def run_suite(task_paths, out_dir: str, agent: str = "llm", model: str | None = None, effort: str | None = None,
              settings=("full",), seeds=(7,), workers: int = 4, verbose: bool = False,
              strict_tools: bool = True, use_fallbacks: bool = True, task_filter: list[str] | None = None,
              provider: str = "anthropic", host: str | None = None, num_ctx: int | None = None,
              think: bool | None = None, shuffle_jobs: bool = False, job_seed: int = 0,
              coordination: bool = False, coordination_config: dict | None = None,
              allow_legacy_resume: bool = False) -> list[dict]:
    if coordination and agent != "llm":
        raise ValueError("coordination is only available for LLM agents")
    tasks = load_tasks(task_paths)
    if task_filter:
        unknown = set(task_filter) - {t.id for t in tasks}
        if unknown:
            raise ValueError(f"unknown task id(s): {', '.join(sorted(unknown))}")
        tasks = [t for t in tasks if t.id in task_filter]
    if not tasks:
        raise ValueError("no tasks found; check --tasks and --only")
    if len({t.id for t in tasks}) != len(tasks):
        raise ValueError("duplicate task IDs would overwrite trajectories")
    settings, seeds = tuple(dict.fromkeys(settings)), tuple(dict.fromkeys(seeds))
    if not settings or set(settings) - SETTINGS.keys():
        raise ValueError("settings must contain valid experiment settings")
    if not seeds or workers < 1:
        raise ValueError("at least one seed and workers >= 1 are required")
    signature = _execution_meta(agent, model, effort, provider, host, num_ctx, think,
                                strict_tools, use_fallbacks, coordination, coordination_config)
    hashes = {t.id: _task_hash(t) for t in tasks}
    out = Path(out_dir)
    (out / "trajectories").mkdir(parents=True, exist_ok=True)
    run_meta = signature | {"run_schema_version": 2, "shuffle_jobs": shuffle_jobs,
                            "job_seed": job_seed if shuffle_jobs else None}
    config_path = out / "run_config.json"
    existing = sorted((out / "trajectories").glob("*.json"))
    previous = {}
    if config_path.exists():
        previous = json.loads(config_path.read_text(encoding="utf-8"))
        _check_signature(previous, signature, "run_config.json")
        for task_id, task_hash in hashes.items():
            if task_id in previous.get("task_hashes", {}) and previous["task_hashes"][task_id] != task_hash:
                raise ValueError(f"task {task_id} changed since this run; use a new --out directory")
    legacy_files = []
    task_map = {t.id: t for t in tasks}
    for path in existing:
        if _validate_saved_episode(path, signature, task_map, hashes, allow_legacy_resume):
            legacy_files.append(path.name)
    if legacy_files:
        print(f"WARNING: resuming {len(legacy_files)} legacy episode(s) without verified task hashes.")
    manifest = run_meta | {
        "settings": list(dict.fromkeys([*previous.get("settings", []), *settings])),
        "seeds": list(dict.fromkeys([*previous.get("seeds", []), *seeds])),
        "tasks": list(dict.fromkeys([*previous.get("tasks", []), *task_map])),
        "task_hashes": previous.get("task_hashes", {}) | hashes,
        "legacy_resume_unverified": bool(legacy_files), "legacy_episode_files": legacy_files,
    }
    _atomic_json(config_path, manifest)

    jobs = []
    for t in tasks:
        for s in settings:
            if s == "relay" and ((t.meta.get("generation") or {}).get("matched") or {}).get("placement") != "private":
                continue
            for seed in seeds:
                p = episode_path(out, t.id, s, seed)
                if p.exists():
                    print(f"skip (exists): {p.name}")
                    continue
                jobs.append((t, s, seed, p))
    if shuffle_jobs:
        random.Random(job_seed).shuffle(jobs)

    factories: dict[int, AgentFactory] = {}
    factory_lock = threading.Lock()

    def factory_for(seed):
        with factory_lock:
            if seed not in factories:
                factories[seed] = make_factory(agent, model, effort, seed, strict_tools, use_fallbacks,
                                               provider=provider, host=host, num_ctx=num_ctx, think=think,
                                               coordination=coordination, coordination_config=coordination_config)
        return factories[seed]

    def job(t, s, seed, p):
        traj = run_episode(t, factory_for(seed), setting=s, seed=seed,
                           log=print if verbose else None, meta=run_meta | {"task_hash": hashes[t.id]})
        _atomic_json(p, traj)
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
            try:
                for f in as_completed(futs):
                    results.append(f.result())
            except BaseException:
                for future in futs:
                    future.cancel()
                raise
    return results
