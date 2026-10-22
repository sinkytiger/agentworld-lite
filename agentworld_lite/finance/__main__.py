"""CLI for the finance domain:  python -m agentworld_lite.finance <command>

  make-tasks   write the matched shared/private task pairs in catalog.py to tasks/finance
  validate     replay reference solutions (must succeed) and idle teams (must fail)
  run          run episodes; trajectories work with `python -m agentworld_lite evaluate/report/pair-report`
  note         write a Markdown sector note from a trajectory's submitted, cited facts (no LLM); English for the
               Canadian desk, Korean for the financial holding companies (tasks/finance_kr)
  news         write a Big 5 sector risk digest from news feeds and the banks' filed figures (no LLM)
  valuation    write a cited valuation snapshot of one bank with its peers as comps (no LLM): --market ca for the
               Big 5 Canadian banks (English), --market kr for the four Korean financial holding companies (Korean)
  valueup      Korean value-up scorecard of the four financial holding companies: targets quoted from their
               기업가치 제고 계획 filings, progress measured from filings (--build fetches and checks the sources)
  ai-report    ask a model the same Korean question twice (no data / a DART data pack), store prompts and answers,
               and fact-check every figure against the filings (--prompts and --check need no model; --run is paid)
  fetch        build a company dataset from a filing source:
                 --source dart       DART OpenAPI (your own key in DART_API_KEY)
                 --source edgar      SEC EDGAR XBRL of 40-F / 6-K / 10-K / 10-Q (your contact in SEC_USER_AGENT)
                 --source local_pdf  figures you transcribed from documents you downloaded (e.g. from SEDAR+)
                 --source sedar_dds  placeholder for the licensed CSA Data Distribution Services feed
"""

from __future__ import annotations

import argparse
import json
import sys

from ..llm import DEFAULT_MODEL
from ..task import load_tasks


def cmd_make_tasks(args) -> int:
    from .catalog import write_tasks
    for p in write_tasks(args.out, args.out_ca, args.out_kr):
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
    from .env import build_env
    from .note import NoteError, find_task, sector_note
    from .run import make_factory, run_episode
    try:
        if args.reference:
            task = find_task(args.reference, args.tasks)
            traj = run_episode(task, make_factory("scripted"), meta={"agent": "scripted", "model": "scripted reference"})
        else:
            traj = json.loads(Path(args.trajectory).read_text(encoding="utf-8"))
            task = find_task(traj["task_id"], args.tasks)
        if build_env(task).market == "kr_fhc":
            from .note_ko import sector_note_ko as sector_note  # the Korean desk writes its note in Korean
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


def cmd_news(args) -> int:
    from pathlib import Path
    from .news import FEEDS, NewsError, build_digest
    feeds = FEEDS
    if args.feed:
        feeds = dict(item.split("=", 1) for item in args.feed)
    try:
        md, items = build_digest(feeds, cache_dir=args.cache, as_of=args.as_of, days=args.days,
                                 macro=not args.no_macro, offline=args.offline)
    except NewsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        print(f"wrote {args.out} ({len(items)} items in the window)")
    else:
        sys.stdout.write(md)
    return 0


def cmd_valuation(args) -> int:
    from pathlib import Path
    from .valuation import ValuationError, snapshot
    try:
        if args.market == "kr":
            from .valuation_ko import snapshot_ko
            md, _ = snapshot_ko(args.bank or "105560", as_of=args.as_of or "2026-10-15", year=args.year)
        else:
            md, _ = snapshot(args.bank or "RY", as_of=args.as_of or "2026-09-30", year=args.year)
    except ValuationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(md)
    return 0


def cmd_valueup(args) -> int:
    from pathlib import Path
    from .valueup import ValueupError, build, render_scorecard
    try:
        if args.build:
            from .dart import DartClient, DartError
            try:
                path = build(DartClient(cache_dir=args.cache))
            except DartError as exc:
                raise ValueupError(str(exc)) from None
            print(f"wrote {path}")
            if not args.out:
                return 0
        md = render_scorecard()
    except ValueupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(md)
    return 0


def cmd_ai_report(args) -> int:
    from .ai_report import AIReportError, check, run, write_prompts
    try:
        if args.prompts:
            print(f"wrote prompts to {write_prompts(args.out)}")
        if args.run:
            from ..llm import ClaudeClient
            record = run(ClaudeClient(model=args.model), args.out, max_cost=args.max_cost, force=args.force,
                         variants=args.variants, max_tokens=args.max_tokens)
            print(f"total ${record['total_cost_usd']:.4f}")
        if args.check or args.run:
            for variant, res in check(args.out).items():
                print(variant, json.dumps(res["summary"], ensure_ascii=False))
    except AIReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_fetch(args) -> int:
    from .sources import SourceError, get_source
    prices, company_prices = {}, {}  # DATE=VALUE applies to every --company; CODE@DATE=VALUE to one of them
    for item in args.price or []:
        date, _, value = item.partition("=")
        code, at, date = date.rpartition("@") if "@" in date else ("", "", date)
        (company_prices.setdefault(code, {}) if at else prices)[date] = float(value.replace(",", ""))
    defaults = {"dart": ("data/finance/dart_fhc" if args.profile == "fhc" else "data/finance/dart", "data/finance/cache"),
                "edgar": ("data/finance/edgar", "data/finance/cache/edgar"),
                "local_pdf": ("data/finance/local", None), "sedar_dds": ("data/finance/sedar", None)}
    out_dir, cache = defaults[args.source]
    out_dir, cache = args.out or out_dir, args.cache or cache
    kw = {"dart": {"cache_dir": cache, "bases": args.basis, "profile": args.profile}, "edgar": {"cache_dir": cache},
          "local_pdf": {"manifest": args.manifest}, "sedar_dds": {}}[args.source]
    try:
        source = get_source(args.source, **kw)
        companies = args.company or ([None] if args.source == "local_pdf" else [])
        if not companies:
            raise SourceError("--company is required for this source")
        unknown = sorted(set(company_prices) - set(companies))
        if unknown:
            raise SourceError(f"--price names {', '.join(unknown)}, which is not in --company")
        for company in companies:
            extra = {"name": args.name, "exchange": args.exchange, "aliases": args.alias} if len(companies) == 1 else {}
            mine = {**prices, **company_prices.get(company, {})}
            price_sources = {d: args.price_source for d in mine} if args.price_source else {}
            path = source.build_company(company, args.periods or [], prices=mine, price_sources=price_sources,
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
    m.add_argument("--out-kr", default="tasks/finance_kr")

    v = sub.add_parser("validate", help="reference solution must succeed, idle team must fail")
    v.add_argument("--tasks", nargs="+", default=["tasks/finance", "tasks/finance_ca", "tasks/finance_kr"])

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
    n.add_argument("--tasks", nargs="+", default=["tasks/finance_ca", "tasks/finance_kr", "tasks/finance"],
                   help="where to find the task")
    n.add_argument("--out", help="Markdown file to write (default: print)")
    n.add_argument("--allow-failed", action="store_true", help="also write a note when some answers are wrong")

    w = sub.add_parser("news", help="write a Big 5 sector risk digest from news feeds and filed figures (no LLM)")
    w.add_argument("--out", help="Markdown file to write (default: print)")
    w.add_argument("--cache", default="data/finance/cache/news",
                   help="parsed feed items and Valet data (never raw feeds); keep it out of synced folders")
    w.add_argument("--as-of", default=None, help="YYYY-MM-DD, default today")
    w.add_argument("--days", type=int, default=14, help="news window ending on the as-of date")
    w.add_argument("--offline", action="store_true", help="use the cache only; fetch nothing")
    w.add_argument("--no-macro", action="store_true", help="skip the Bank of Canada Valet series")
    w.add_argument("--feed", nargs="+", metavar="NAME=URL", help="replace the default feeds")

    val = sub.add_parser("valuation", help="write a cited valuation snapshot of one Big 5 bank or Korean group (no LLM)")
    val.add_argument("--market", choices=["ca", "kr"], default="ca",
                     help="ca: Big 5 Canadian banks (English); kr: four Korean financial holding companies (Korean)")
    val.add_argument("--bank", default=None, help="subject: ca ticker or name (default RY), kr stock code or name "
                                                 "(default 105560, KB금융)")
    val.add_argument("--as-of", default=None, help="use only filings made by this date (default 2026-09-30 ca, "
                                                  "2026-10-15 kr)")
    val.add_argument("--year", type=int, default=2026, help="fiscal year of the Q3 (ca) or H1 (kr) report")
    val.add_argument("--out", help="Markdown file to write (default: print)")

    vu = sub.add_parser("valueup", help="Korean value-up scorecard: quoted targets vs progress measured from filings")
    vu.add_argument("--build", action="store_true",
                    help="fetch the sources from DART (needs DART_API_KEY), check every quote, write valueup.json")
    vu.add_argument("--cache", default="data/finance/cache", help="DART company-code cache (with --build)")
    vu.add_argument("--out", help="Markdown file to write (default: print)")

    ai = sub.add_parser("ai-report", help="Korean AI report with and without a DART data pack, fact-checked")
    ai.add_argument("--prompts", action="store_true", help="write the system prompt and both prompts (no model call)")
    ai.add_argument("--run", action="store_true", help="paid: ask the model once per prompt not yet answered")
    ai.add_argument("--check", action="store_true", help="fact-check the saved answers (no model call)")
    ai.add_argument("--model", default="claude-opus-5-5")
    ai.add_argument("--max-cost", type=float, default=1.0, help="refuse to call when the worst case exceeds this (USD)")
    ai.add_argument("--force", action="store_true", help="ask again even when an answer is saved")
    ai.add_argument("--variants", nargs="+", choices=["nodata", "data"], default=["nodata", "data"])
    ai.add_argument("--max-tokens", type=int, default=8000, help="output cap per call (thinking included)")
    ai.add_argument("--out", default="data/finance/ai_report")

    f = sub.add_parser("fetch", help="build company datasets from DART, EDGAR or hand-checked local documents")
    f.add_argument("--source", choices=["dart", "edgar", "local_pdf", "sedar_dds"], default="dart")
    f.add_argument("--company", nargs="+", help="DART: stock code or exact name; EDGAR: ticker (RY), CIK or exact name")
    f.add_argument("--periods", nargs="+", help="YEAR:PERIOD (fiscal, year-to-date), e.g. 2025:FY 2026:H1 2026:Q3")
    f.add_argument("--basis", nargs="+", default=["CFS", "OFS"], choices=["CFS", "OFS"], help="DART only")
    f.add_argument("--profile", choices=["default", "fhc"], default="default",
                   help="DART account set: default (operating companies) or fhc (financial holding companies, 금융지주); "
                        "fhc writes to data/finance/dart_fhc")
    f.add_argument("--manifest", help="local_pdf: YAML manifest of transcribed figures and their pages")
    f.add_argument("--price", nargs="*", help="closing prices DATE=VALUE for every --company, or CODE@DATE=VALUE for one "
                                              "(not in the filings' XBRL), e.g. 2026-07-31=293.41 or 105560@2026-06-30=123400")
    f.add_argument("--price-source", help="where the --price values come from (stored with the dataset)")
    f.add_argument("--name", help="display name (one company only), e.g. 'Royal Bank of Canada'")
    f.add_argument("--exchange", help="listing for price labels (one company only), e.g. TSX")
    f.add_argument("--alias", nargs="*", help="other names agents may use (one company only), e.g. RBC")
    f.add_argument("--out", default=None, help="default data/finance/<source>")
    f.add_argument("--cache", default=None, help="default data/finance/cache[/edgar]; keep it out of synced folders")

    args = ap.parse_args(argv)
    if args.cmd == "fetch" and args.source != "local_pdf" and not args.periods and not (args.source == "dart" and args.price):
        ap.error("--periods is required (DART: or --price alone, to add prices to existing datasets)")
    if args.cmd == "run" and args.agent == "llm" and not args.model:
        if args.provider == "ollama":
            ap.error("--model is required with --provider ollama (e.g. llama3.2:3b or gemma4:cloud)")
        args.model = DEFAULT_MODEL
    return {"make-tasks": cmd_make_tasks, "validate": cmd_validate, "run": cmd_run, "note": cmd_note,
            "news": cmd_news, "valuation": cmd_valuation, "valueup": cmd_valueup, "ai-report": cmd_ai_report,
            "fetch": cmd_fetch}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
