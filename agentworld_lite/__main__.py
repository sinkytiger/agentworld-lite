"""CLI: python -m agentworld_lite {validate,run,evaluate,report,augment,show} ..."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .llm import DEFAULT_MODEL


def make_client(provider: str, model: str, effort: str | None = None, no_fallbacks: bool = False,
                host: str | None = None, num_ctx: int | None = None, think: bool | None = None):
    """Claude (Anthropic API) or a local Ollama model behind the same json_call interface."""
    if provider == "ollama":
        from .local_llm import DEFAULT_HOST, DEFAULT_NUM_CTX, OllamaClient, OllamaError
        if model == DEFAULT_MODEL:
            raise SystemExit("With --provider ollama, pass a local model name, e.g. --model qwen3:8b")
        client = OllamaClient(model, host=host or DEFAULT_HOST, num_ctx=num_ctx or DEFAULT_NUM_CTX, think=think)
        try:
            print(f"Ollama model {model} ({client.check()}) is ready")
        except OllamaError as exc:
            raise SystemExit(str(exc)) from None
        return client
    from .llm import ClaudeClient
    return ClaudeClient(model=model, effort=effort, use_fallbacks=not no_fallbacks)


def add_provider_args(p, model_flag: str = "--model") -> None:
    p.add_argument("--provider" if model_flag == "--model" else "--judge-provider", dest="provider",
                   choices=["anthropic", "ollama"], default="anthropic",
                   help="anthropic = Claude API (paid); ollama = local open model (free)")
    p.add_argument("--ollama-host", default=None, help="default http://127.0.0.1:11434")
    p.add_argument("--num-ctx", type=int, default=None, help="Ollama context window (default 8192)")
    p.add_argument("--think", choices=["on", "off"], default=None,
                   help="Ollama thinking mode for models that support it (default: model default)")


def _think(args) -> bool | None:
    return None if args.think is None else args.think == "on"


def cmd_validate(args) -> int:
    from .runner import make_factory, run_episode
    from .task import load_tasks
    from .verifier import evaluate

    bad = 0
    for t in load_tasks(args.tasks):
        errors, warnings = t.validate()
        pre = evaluate(t.build_world(), t.verifier)["success"]
        status, detail = "OK", ""
        if errors:
            status, detail = "INVALID", "; ".join(errors)
        elif pre:
            status, detail = "TRIVIAL", "objective already satisfied at round 0"
        elif t.reference_solution:
            r = run_episode(t, make_factory("scripted"))["result"]
            detail = f"reference solution: {'solved' if r['success'] else 'FAILED'} in {r['rounds_used']}/{t.max_rounds} rounds"
            if not r["success"]:
                status = "UNSOLVED"
        else:
            detail = "no reference solution (feasibility unchecked)"
        bad += status not in ("OK",)
        print(f"[{status:<8}] {t.id:<26} {t.category:<12} agents={len(t.agents):<2} {detail}")
        for w in warnings:
            print(f"           warning: {w}")
    return 1 if bad else 0


def cmd_run(args) -> int:
    from .runner import run_suite

    workers = args.workers if args.workers is not None else (1 if args.provider == "ollama" else 4)
    if args.agent == "llm" and args.provider == "ollama":
        make_client("ollama", args.model, host=args.ollama_host, num_ctx=args.num_ctx)  # fail fast if not ready
        print(f"workers: {workers}")
    elif args.agent == "llm":
        print(f"Model: {args.model}  effort: {args.effort or 'model default'}  "
              f"fallbacks: {'on' if not args.no_fallbacks else 'off'}")
    run_suite(args.tasks, args.out, agent=args.agent, model=args.model, effort=args.effort,
              settings=args.settings, seeds=args.seeds, workers=workers, verbose=args.verbose,
              strict_tools=not args.no_strict, use_fallbacks=not args.no_fallbacks, task_filter=args.only,
              provider=args.provider, host=args.ollama_host, num_ctx=args.num_ctx, think=_think(args))
    return 0


def cmd_evaluate(args) -> int:
    from .cce import LLMJudge, RuleJudge, agreement
    from .failures import classify_failures
    run = Path(args.run)
    (run / "evals").mkdir(exist_ok=True)
    client = None
    if args.judge in ("llm", "both") or args.failures:
        client = make_client(args.provider, args.judge_model, args.judge_effort, args.no_fallbacks, args.ollama_host,
                             args.num_ctx, _think(args))

    def one(p: Path) -> str:
        out = run / "evals" / p.name
        ev = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        traj = json.loads(p.read_text(encoding="utf-8"))
        ev.setdefault("cce", {})
        if args.judge in ("rule", "both") and ("rule" not in ev["cce"] or args.force):
            ev["cce"]["rule"] = RuleJudge().compute(traj)
        if args.judge in ("llm", "both") and ("llm" not in ev["cce"] or args.force):
            ev["cce"]["llm"] = LLMJudge(client).compute(traj)
        if "rule" in ev["cce"] and "llm" in ev["cce"] and traj["result"]["success"]:
            ev["agreement"] = agreement(traj, ev["cce"]["rule"], ev["cce"]["llm"])
        if args.failures and ("failures" not in ev or args.force):
            ev["failures"] = classify_failures(traj, client)
        out.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
        parts = [f"{j}={c['cce']:.3f}" for j, c in ev["cce"].items()]
        return f"{p.stem:<40} CCE " + " ".join(parts)

    paths = sorted((run / "trajectories").glob("*.json"))
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for line in ex.map(one, paths):
            print(line)
    if client:
        print(f"Judge usage: {client.usage.as_dict()}")
    return 0


def cmd_report(args) -> int:
    from .report import load_run, render_markdown

    trajs = [t for r in args.runs for t in load_run(r)]
    if not trajs:
        print("no trajectories found", file=sys.stderr)
        return 1
    md = render_markdown(trajs, judge=args.judge, graphs=args.graphs)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def cmd_augment(args) -> int:
    from .augment import augment_task, save_task
    from .task import load_tasks

    client = make_client(args.provider, args.model, args.effort, args.no_fallbacks, args.ollama_host, args.num_ctx,
                         _think(args))
    for src in load_tasks(args.tasks):
        if args.only and src.id not in args.only:
            continue
        previous: list[str] = []
        for k in range(1, args.n + 1):
            task, log = augment_task(src, client, k, previous)
            if task is None:
                print(f"[rejected] {src.id} v{k}: {log['attempts'][-1]['problem'][:300]}")
                continue
            p = save_task(task, args.out)
            previous.append("; ".join(task.meta.get("changes", [])))
            print(f"[kept]     {task.id} -> {p}  ({len(log['attempts'])} attempt(s))")
    print(f"Usage: {client.usage.as_dict()}")
    return 0


def cmd_gen(args) -> int:
    from .gym import generate_gym, summarize

    weights = None
    if args.weights:
        weights = json.loads(Path(args.weights).read_text(encoding="utf-8"))
    kept, records = generate_gym(args.n, seed=args.seed, weights=weights, out_dir=args.out, prefix=args.prefix)
    print(summarize(records))
    print(f"wrote {len(kept)} task(s) to {args.out}")
    return 0


def cmd_show(args) -> int:
    traj = json.loads(Path(args.traj).read_text(encoding="utf-8"))
    r = traj["result"]
    print(f"{traj['task_id']} [{traj['setting']}] success={r['success']} rounds={r['rounds_used']}/{traj['max_rounds']} | {r['message']}")
    for a in traj["actions"]:
        print(f"{a['id']:>7} {a['description'][:args.width]}")
        if args.reasoning and a.get("reasoning"):
            print(f"{'':>8}  (thought) {a['reasoning'][:args.width]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agentworld_lite", description="AgentWorld-Lite multi-agent collaboration benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="static checks + replay reference solutions")
    v.add_argument("--tasks", nargs="+", default=["tasks/main"])
    v.set_defaults(fn=cmd_validate)

    r = sub.add_parser("run", help="run episodes and save trajectories")
    r.add_argument("--tasks", nargs="+", default=["tasks/main"])
    r.add_argument("--agent", choices=["llm", "scripted", "random"], default="llm")
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    r.add_argument("--settings", nargs="+", default=["full"],
                   choices=["full", "no_comm", "no_docs", "whitebox", "random_spawn"])
    r.add_argument("--seeds", nargs="+", type=int, default=[7])
    r.add_argument("--workers", type=int, default=None, help="episodes run in parallel (default 4; 1 with ollama)")
    r.add_argument("--only", nargs="+", help="task ids to run")
    r.add_argument("--out", required=True)
    r.add_argument("--verbose", action="store_true")
    r.add_argument("--no-strict", action="store_true", help="disable strict tool schemas")
    r.add_argument("--no-fallbacks", action="store_true", help="disable server-side refusal fallbacks")
    add_provider_args(r)
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("evaluate", help="CCE / PAC and failure taxonomy for a run directory")
    e.add_argument("--run", required=True)
    e.add_argument("--judge", choices=["rule", "llm", "both"], default="rule")
    e.add_argument("--judge-model", default=DEFAULT_MODEL)
    e.add_argument("--judge-effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    e.add_argument("--failures", action="store_true", help="classify chat messages into the paper's failure modes")
    e.add_argument("--workers", type=int, default=4)
    e.add_argument("--force", action="store_true")
    e.add_argument("--no-fallbacks", action="store_true")
    add_provider_args(e, model_flag="--judge-model")
    e.set_defaults(fn=cmd_evaluate)

    rp = sub.add_parser("report", help="aggregate runs into a Markdown report")
    rp.add_argument("--runs", nargs="+", required=True)
    rp.add_argument("--out", default="reports/report.md")
    rp.add_argument("--judge", choices=["rule", "llm"], default=None, help="which CCE to report (default: llm if present)")
    rp.add_argument("--graphs", type=int, default=0, help="include N causal graphs (Mermaid)")
    rp.set_defaults(fn=cmd_report)

    a = sub.add_parser("augment", help="generate validated task variants with Claude")
    a.add_argument("--tasks", nargs="+", default=["tasks/main"])
    a.add_argument("--out", default="tasks/augmented")
    a.add_argument("--n", type=int, default=1, help="variants per task")
    a.add_argument("--only", nargs="+")
    a.add_argument("--model", default=DEFAULT_MODEL)
    a.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="high")
    a.add_argument("--no-fallbacks", action="store_true")
    add_provider_args(a)
    a.set_defaults(fn=cmd_augment)

    g = sub.add_parser("gen", help="blueprint-first task generation (AutoGym-style), certified by ground-truth runs")
    g.add_argument("--n", type=int, default=20, help="generation attempts")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--out", default="tasks/gym")
    g.add_argument("--prefix", default="g")
    g.add_argument("--weights", help="JSON file with sampling weights (see blueprint.DEFAULT_WEIGHTS)")
    g.set_defaults(fn=cmd_gen)

    s = sub.add_parser("show", help="print a trajectory")
    s.add_argument("traj")
    s.add_argument("--reasoning", action="store_true")
    s.add_argument("--width", type=int, default=200)
    s.set_defaults(fn=cmd_show)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
