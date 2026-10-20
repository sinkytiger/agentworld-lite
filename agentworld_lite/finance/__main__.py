"""CLI for the finance domain:  python -m agentworld_lite.finance <command>

  make-tasks   write the matched shared/private task pairs in catalog.py to tasks/finance
  validate     replay reference solutions (must succeed) and idle teams (must fail)
  run          run episodes; trajectories work with `python -m agentworld_lite evaluate/report/pair-report`
  note         write an English Markdown sector note from a trajectory's submitted, cited facts (no LLM)
  fetch        build a company dataset from a filing source:
                 --source dart       DART OpenAPI (your own key in DART_API_KEY)
                 --source edgar      SEC EDGAR XBRL of 40-F / 6-K / 10-K / 10-Q (your contact in SEC_USER_AGENT)
                 --source local_pdf  figures you transcribed from documents you downloaded (e.g. from SEDAR+)
                 --source sedar_dds  placeholder for the licensed CSA Data Distribution Services feed
"""

from __future__ import annotations

import argparse
import sys

from ..llm import DEFAULT_MODEL
from ..task import load_tasks


def cmd_make_tasks(args) -> int:
    from .catalog import write_tasks
    for p in write_tasks(args.out, args.out_ca):
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


def cmd_note(args) -> int:
    import json
    from pathlib import Path
    from .note import NoteError, find_task, sector_note
    from .run import make_factory, run_episode
    try:
        if args.reference:
            task = find_task(args.reference, args.tasks)
            traj = run_episode(task, make_factory("scripted"), meta={"agent": "scripted", "model": "scripted reference"})
        else:
            traj = json.loads(Path(args.trajectory).read_text(encoding="utf-8"))
            task = find_task(traj["task_id"], args.tasks)
        note = sector_note(traj, task, require_success=not args.allow_failed)
    except NoteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(note)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(note)
    return 0


def cmd_fetch(args) -> int:
    from .sources import SourceError, get_source
    prices = {}
    for item in args.price or []:
        date, _, value = item.partition("=")
        prices[date] = float(value.replace(",", ""))
    price_sources = {d: args.price_source for d in prices} if args.price_source else {}
    defaults = {"dart": ("data/finance/dart", "data/finance/cache"), "edgar": ("data/finance/edgar", "data/finance/cache/edgar"),
                "local_pdf": ("data/finance/local", None), "sedar_dds": ("data/finance/sedar", None)}
    out_dir, cache = defaults[args.source]
    out_dir, cache = args.out or out_dir, args.cache or cache
    kw = {"dart": {"cache_dir": cache, "bases": args.basis}, "edgar": {"cache_dir": cache},
          "local_pdf": {"manifest": args.manifest}, "sedar_dds": {}}[args.source]
    try:
        source = get_source(args.source, **kw)
        companies = args.company or ([None] if args.source == "local_pdf" else [])
        if not companies:
            raise SourceError("--company is required for this source")
        for company in companies:
            extra = {"name": args.name, "exchange": args.exchange, "aliases": args.alias} if len(companies) == 1 else {}
            path = source.build_company(company, args.periods, prices=prices, price_sources=price_sources,
                                        out_dir=out_dir, **extra)
            print(f"wrote {path}")
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agentworld_lite.finance", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make-tasks", help="write the catalog's shared/private task pairs")
    m.add_argument("--out", default="tasks/finance")
    m.add_argument("--out-ca", default="tasks/finance_ca")

    v = sub.add_parser("validate", help="reference solution must succeed, idle team must fail")
    v.add_argument("--tasks", nargs="+", default=["tasks/finance", "tasks/finance_ca"])

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

    n = sub.add_parser("note", help="write a Markdown sector note from a trajectory's cited answers (no LLM)")
    src = n.add_mutually_exclusive_group(required=True)
    src.add_argument("--trajectory", help="trajectory JSON written by `run`")
    src.add_argument("--reference", metavar="TASK_ID", help="replay the task's scripted reference solution instead")
    n.add_argument("--tasks", nargs="+", default=["tasks/finance_ca", "tasks/finance"], help="where to find the task")
    n.add_argument("--out", help="Markdown file to write (default: print)")
    n.add_argument("--allow-failed", action="store_true", help="also write a note when some answers are wrong")

    f = sub.add_parser("fetch", help="build company datasets from DART, EDGAR or hand-checked local documents")
    f.add_argument("--source", choices=["dart", "edgar", "local_pdf", "sedar_dds"], default="dart")
    f.add_argument("--company", nargs="+", help="DART: stock code or exact name; EDGAR: ticker (RY), CIK or exact name")
    f.add_argument("--periods", nargs="+", help="YEAR:PERIOD (fiscal, year-to-date), e.g. 2025:FY 2026:H1 2026:Q3")
    f.add_argument("--basis", nargs="+", default=["CFS", "OFS"], choices=["CFS", "OFS"], help="DART only")
    f.add_argument("--manifest", help="local_pdf: YAML manifest of transcribed figures and their pages")
    f.add_argument("--price", nargs="*", help="closing prices DATE=VALUE (not in the filings' XBRL), e.g. 2026-07-31=293.41")
    f.add_argument("--price-source", help="where the --price values come from (stored with the dataset)")
    f.add_argument("--name", help="display name (one company only), e.g. 'Royal Bank of Canada'")
    f.add_argument("--exchange", help="listing for price labels (one company only), e.g. TSX")
    f.add_argument("--alias", nargs="*", help="other names agents may use (one company only), e.g. RBC")
    f.add_argument("--out", default=None, help="default data/finance/<source>")
    f.add_argument("--cache", default=None, help="default data/finance/cache[/edgar]; keep it out of synced folders")

    args = ap.parse_args(argv)
    if args.cmd == "fetch" and args.source != "local_pdf" and not args.periods:
        ap.error("--periods is required")
    if args.cmd == "run" and args.agent == "llm" and not args.model:
        if args.provider == "ollama":
            ap.error("--model is required with --provider ollama (e.g. llama3.2:3b or gemma4:cloud)")
        args.model = DEFAULT_MODEL
    return {"make-tasks": cmd_make_tasks, "validate": cmd_validate, "run": cmd_run, "note": cmd_note,
            "fetch": cmd_fetch}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
