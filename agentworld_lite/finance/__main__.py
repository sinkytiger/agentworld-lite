"""CLI for the finance domain:  python -m agentworld_lite.finance <command>

  make-tasks   write the matched shared/private task pairs in catalog.py to tasks/finance
  validate     replay reference solutions (must succeed) and idle teams (must fail)
  run          run episodes; trajectories work with `python -m agentworld_lite evaluate/report/pair-report`
  fetch        pull company data from DART OpenAPI (needs your own key in DART_API_KEY)
"""

from __future__ import annotations

import argparse
import sys

from ..llm import DEFAULT_MODEL
from ..task import load_tasks


def cmd_make_tasks(args) -> int:
    from .catalog import write_tasks
    for p in write_tasks(args.out):
        print(p)
    return 0


def cmd_validate(args) -> int:
    from .run import validate_task
    bad = 0
    for t in load_tasks(args.tasks):
        errors = validate_task(t)
        bad += bool(errors)
        print(f"[{'OK ' if not errors else 'ERR'}] {t.id}" + "".join(f"\n      {e}" for e in errors))
    return 1 if bad else 0


def cmd_run(args) -> int:
    from .run import run_suite
    think = None if args.think is None else args.think == "on"
    tasks = args.tasks
    if args.only:
        tasks = [str(t.path) for t in load_tasks(args.tasks) if t.id in args.only]
    run_suite(tasks, args.out, agent=args.agent, settings=args.settings, seeds=args.seeds, verbose=args.verbose,
              model=args.model, provider=args.provider, host=args.ollama_host, num_ctx=args.num_ctx, think=think,
              effort=args.effort)
    return 0


def cmd_fetch(args) -> int:
    from .dart import DartClient, DartError, fetch_company
    prices = {}
    for item in args.price or []:
        date, _, value = item.partition("=")
        prices[date] = float(value.replace(",", ""))
    try:
        client = DartClient(cache_dir=args.cache)
        for company in args.company:
            path = fetch_company(client, company, args.periods, bases=args.basis, prices=prices, out_dir=args.out)
            print(f"wrote {path}")
    except DartError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agentworld_lite.finance", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make-tasks", help="write the catalog's shared/private task pairs")
    m.add_argument("--out", default="tasks/finance")

    v = sub.add_parser("validate", help="reference solution must succeed, idle team must fail")
    v.add_argument("--tasks", nargs="+", default=["tasks/finance"])

    r = sub.add_parser("run", help="run finance episodes and save trajectories")
    r.add_argument("--tasks", nargs="+", default=["tasks/finance"])
    r.add_argument("--only", nargs="+", help="task ids to run")
    r.add_argument("--agent", choices=["llm", "scripted", "random"], default="llm")
    r.add_argument("--provider", choices=["anthropic", "ollama"], default="anthropic")
    r.add_argument("--model", default=None, help=f"default {DEFAULT_MODEL} (anthropic); required for ollama")
    r.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    r.add_argument("--ollama-host", default=None)
    r.add_argument("--num-ctx", type=int, default=None)
    r.add_argument("--think", choices=["on", "off"], default=None)
    r.add_argument("--settings", nargs="+", default=["full"], choices=["full", "no_comm", "no_docs", "whitebox"])
    r.add_argument("--seeds", nargs="+", type=int, default=[7])
    r.add_argument("--out", required=True)
    r.add_argument("--verbose", action="store_true")

    f = sub.add_parser("fetch", help="pull statements and share counts from DART OpenAPI")
    f.add_argument("--company", nargs="+", required=True, help="stock code (e.g. 005930) or exact company name")
    f.add_argument("--periods", nargs="+", required=True, help="YEAR:PERIOD, e.g. 2024:FY 2024:H1 2025:Q1 2025:H1")
    f.add_argument("--basis", nargs="+", default=["CFS", "OFS"], choices=["CFS", "OFS"])
    f.add_argument("--price", nargs="*", help="closing prices DATE=VALUE (not in DART), e.g. 2025-09-30=60000")
    f.add_argument("--out", default="data/finance/dart")
    f.add_argument("--cache", default="data/finance/cache")

    args = ap.parse_args(argv)
    if args.cmd == "run" and args.agent == "llm" and not args.model:
        if args.provider == "ollama":
            ap.error("--model is required with --provider ollama (e.g. llama3.2:3b or gemma4:cloud)")
        args.model = DEFAULT_MODEL
    return {"make-tasks": cmd_make_tasks, "validate": cmd_validate, "run": cmd_run, "fetch": cmd_fetch}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
