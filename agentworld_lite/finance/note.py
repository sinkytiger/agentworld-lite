"""Sector note: an English Markdown write-up of a finished trajectory's submitted, cited facts. No model writes it.

The trajectory's actions are replayed on a fresh desk built from the task (the environment is deterministic), which
recovers every fact the team created together with its provenance: a statement fact carries the filing form and its
accession number, a calculated fact its formula and inputs. The replay must reproduce the recorded submission, or the
note is refused. Every figure in the note is followed by a citation, and a derived figure cites every filing behind it:

    C$5,879 million [fact `calc.ry_q3_net_income_common` from 6-K 0001193125-26-369461; 6-K 0001193125-26-242928]

`uncited_figures` finds figures without one; `sector_note` runs it on its own output before returning.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..task import Task, load_tasks
from .dataset import CURRENCY_SIGN, PERIODS, Fact, fmt_value
from .env import REPO_ROOT, FinanceEnv, build_env
from .run import SETTINGS
from .tools import tool_names_for


class NoteError(Exception):
    pass


# ---------------------------------------------------------------------- replay
def find_task(task_id: str, paths: list[str]) -> Task:
    dirs = [p if Path(p).exists() else str(REPO_ROOT / p) for p in paths]
    for t in load_tasks([d for d in dirs if Path(d).exists()]):
        if t.id == task_id:
            return t
    raise NoteError(f"task {task_id} not found in {', '.join(paths)}")


def replay(traj: dict, task: Task) -> FinanceEnv:
    """Re-execute the recorded actions on a fresh desk; the result must match the trajectory step by step."""
    if traj.get("task_id") != task.id:
        raise NoteError(f"trajectory is for {traj.get('task_id')}, not {task.id}")
    env = build_env(task)
    chat = SETTINGS.get(traj.get("setting", "full"), SETTINGS["full"])["chat"]
    allowed = {t for t in tool_names_for(env.market) if chat or t != "send_chat"}
    for rec in traj.get("actions", []):
        env.round = rec["round"]
        ok = rec["tool"] in allowed and env.execute(rec["agent"], rec["tool"], rec.get("args")).ok
        if ok != rec["ok"]:
            raise NoteError(f"action {rec['id']} ({rec['tool']}) does not replay (recorded ok={rec['ok']}); "
                            "the task or the dataset changed after the run")
    recorded = {k: v["fact_id"] for k, v in (traj.get("submission") or {}).items()}
    if env.submission != recorded:
        raise NoteError("the replayed submission differs from the recorded one")
    for k, v in (traj.get("submission") or {}).items():
        if abs(env.facts[v["fact_id"]].value - v["value"]) > 1e-9 * max(1.0, abs(v["value"])):
            raise NoteError(f"{k}: replayed value {env.facts[v['fact_id']].value} != recorded {v['value']}")
    return env


# ---------------------------------------------------------------------- figures and citations
def fmt_figure(value: float, unit: str) -> str:
    """Bank-report style: money in millions, ratios with two decimals."""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if unit in CURRENCY_SIGN:
        cur = CURRENCY_SIGN[unit]
        if v >= 1e6:
            m = v / 1e6
            return f"{sign}{cur}{m:,.0f} million" if abs(m - round(m)) < 1e-6 else f"{sign}{cur}{m:,.1f} million"
        return f"{sign}{cur}{v:,.2f}"
    if unit.endswith("/share") and unit.split("/")[0] in CURRENCY_SIGN:
        return f"{sign}{CURRENCY_SIGN[unit.split('/')[0]]}{v:,.2f} per share"
    if unit == "%":
        return f"{sign}{v:,.2f}%"
    if unit == "pp":
        return f"{sign}{v:,.2f} pp"
    if unit == "x":
        return f"{sign}{v:,.2f}x"
    if unit == "KRW":
        return fmt_value(value, unit)
    return f"{sign}{v:,.2f} {unit}".strip()


def _leaves(env: FinanceEnv, fid: str, out: list[str] | None = None) -> list[str]:
    """Facts that are not calculations (filings, prices, task inputs) behind `fid`, in order of first use."""
    out = [] if out is None else out
    fact = env.facts[fid]
    if fact.meta.get("source") == "calculation":
        for sub in fact.meta.get("inputs", {}).values():
            _leaves(env, sub, out)
    elif fid not in out:
        out.append(fid)
    return out


def _where(fact: Fact) -> str:
    m = fact.meta
    if m.get("form") and m.get("rcept_no"):
        return f"{m['form']} {m['rcept_no']}"
    if m.get("rcept_no"):
        page = f" p. {m['page']}" if m.get("page") else ""
        return f"{m.get('source', 'filing')} {m['rcept_no']}{page}"
    return str(m.get("source") or "task input")


def cite(env: FinanceEnv, fid: str) -> str:
    wheres = list(dict.fromkeys(_where(env.facts[x]) for x in _leaves(env, fid)))
    return f"[fact `{fid}` from {'; '.join(wheres)}]"


def figure(env: FinanceEnv, fid: str) -> str:
    f = env.facts[fid]
    return f"{fmt_figure(f.value, f.unit)} {cite(env, fid)}"


def cite_facts(facts: list[Fact]) -> str:
    """Citation for a figure computed outside a desk run from dataset facts (statement or price facts)."""
    facts = list({f.id: f for f in facts}.values())
    wheres = list(dict.fromkeys(_where(f) for f in facts))
    ids = ", ".join(f"`{f.id}`" for f in facts)
    return f"[{'fact' if len(facts) == 1 else 'facts'} {ids} from {'; '.join(wheres)}]"


def cited(value: float, unit: str, facts: list[Fact]) -> str:
    return f"{fmt_figure(value, unit)} {cite_facts(facts)}"


_CITATION = re.compile(r"\[facts? `[^`]+`(?:, `[^`]+`)* from [^\]]*\]")
_CODE = re.compile(r"`[^`]*`")
_FIGURE = re.compile(r"(?<![\w.,$-])(-?)((?:US\$|[A-Z]{1,2}\$|\$)?)((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
                     r"(%|x\b| pp\b| million\b| billion\b| per share\b| shares\b)?")


def uncited_figures(markdown: str, extra: tuple[str, ...] = ()) -> list[str]:
    """Figures (numbers with a currency sign, a decimal point, a thousands separator or a unit) that are not
    immediately followed by a citation. Headings, code spans and the citations themselves are skipped, so years,
    dates, period names and fact ids do not count. `extra` adds citation patterns (regexes) for other sources."""
    citations = [_CITATION, *(re.compile(p) for p in extra)]
    bad = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for c in citations:
            line = c.sub("\x00", line)
        text = _CODE.sub("\x01", line)
        for m in _FIGURE.finditer(text):
            _, currency, number, unit = m.groups()
            if not (currency or unit or "," in number or "." in number):
                continue
            if not text[m.end():].lstrip("*").startswith(" \x00"):
                bad.append(m.group(0))
    return bad


# ---------------------------------------------------------------------- the note
SOURCE_NOTES = {
    "EDGAR": ("Figures are as originally filed in each issuer's XBRL: Form 40-F annual and Form 6-K interim reports on "
              "SEC EDGAR, the same reports the issuers file on SEDAR+. They are not adjusted for later restatements or "
              "for items an issuer excludes from its adjusted results."),
    "DART": "Figures are as originally filed with DART and are not adjusted for later corrections.",
    "local documents": "Figures were transcribed from the cited pages and checked against the page text.",
}
ID_NAMES = {"EDGAR": "SEC EDGAR accession number", "DART": "DART receipt number", "local documents": "document id"}


def _company_of(env: FinanceEnv, fid: str):
    code = env.facts[fid].meta.get("company")
    return env.dataset.companies.get(code) if code else None


def _period(env: FinanceEnv, fact: Fact) -> str:
    m = fact.meta
    company = env.dataset.companies[m["company"]]
    st = company.statement(m["year"], m["period"], m["basis"]) or {}
    end = f" (to {st['period_end']})" if st.get("period_end") else ""
    return f"fiscal {m['year']} {m['period']}{end}" if company.lang == "en" else f"{m['year']} {m['period']}"


def filing_link(company, rcept_no: str) -> str:
    if company.source == "EDGAR" and company.data.get("cik"):
        return f"[{rcept_no}](https://www.sec.gov/Archives/edgar/data/{int(company.data['cik'])}/{rcept_no.replace('-', '')}/)"
    if company.source == "DART":
        return f"[{rcept_no}](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no})"
    return rcept_no


def _derivation(env: FinanceEnv, fid: str, depth: int = 0) -> list[str]:
    fact = env.facts[fid]
    if fact.meta.get("source") != "calculation":
        return []
    lines = []
    for var, sub in fact.meta["inputs"].items():
        s = env.facts[sub]
        what = f" ({s.label})" if s.meta.get("source") != "calculation" else f" = `{s.meta['formula']}`, where:"
        lines.append(f"{'  ' * depth}- `{var}` = {figure(env, sub)}{what}")
        lines += _derivation(env, sub, depth + 1)
    return lines


def _comparison(env: FinanceEnv, fields: dict[str, str]) -> tuple[list[tuple[str, dict]], list[str]]:
    """Columns of one fact per company: per-company answers (ry_x, td_x, ...) and, for derived answers, inputs that
    take the same account from the same period of several companies (e.g. each bank's CET1 ratio)."""
    codes = {c.lower(): c for c in env.dataset.companies}
    groups: dict[str, dict] = {}
    for fld, fid in fields.items():
        prefix, _, metric = fld.partition("_")
        if prefix in codes and metric:
            groups.setdefault(metric, {})[codes[prefix]] = fid
    grouped = {f"{c.lower()}_{metric}" for metric, g in groups.items() if len(g) > 1 for c in g}
    columns = [(f"`{metric}`", g) for metric, g in groups.items() if len(g) > 1]
    others = [f for f in fields if f not in grouped]
    inputs: dict[tuple, dict] = {}
    for f in others:
        for sub in env.facts[fields[f]].meta.get("inputs", {}).values():
            m = env.facts[sub].meta
            if m.get("account") and m.get("company"):
                inputs.setdefault((m["account"], m["year"], m["period"]), {})[m["company"]] = sub
    for (account, year, period), g in inputs.items():
        if len(g) > 1:
            columns.append((f"`{account}` (fiscal {year} {period} filing)", g))
    return columns, others


def sector_note(traj: dict, task: Task, *, require_success: bool = True) -> str:
    env = replay(traj, task)
    order = [cp["field"] for cp in task.verifier["checkpoints"]]
    fields = {f: env.submission[f] for f in order if f in env.submission}
    fields.update({f: fid for f, fid in env.submission.items() if f not in fields})
    if not fields:
        raise NoteError("the team submitted no answers")
    done = {cp["name"]: cp["done"] for cp in traj["result"]["checkpoints"]}
    by_field = {cp["field"]: done.get(cp.get("name") or cp["field"], False) for cp in task.verifier["checkpoints"]}
    n_ok = sum(by_field.values())
    if require_success and not traj["result"]["success"]:
        raise NoteError(f"only {n_ok} of {len(by_field)} answers are within tolerance (pass --allow-failed to write anyway)")

    leaves = list(dict.fromkeys(x for fid in fields.values() for x in _leaves(env, fid)))
    sources = sorted({c.source for c in (_company_of(env, x) for x in leaves) if c})
    agent = (traj.get("meta") or {}).get("model") or (traj.get("meta") or {}).get("agent") or "unknown"
    verdict = ("every answer is within the task's tolerance" if n_ok == len(by_field)
               else f"{n_ok} of {len(by_field)} answers are within the task's tolerance")
    ids = " / ".join(ID_NAMES.get(s, "document id") for s in sources) or "source"
    out = [
        f"# {task.name}", "",
        "*Sector note built from the desk's submitted, cited facts.*", "",
        f"> {task.description}", "",
        f"- **Run:** `{task.id}`, setting `{traj.get('setting', 'full')}`, seed `{traj.get('seed', '')}`, agent `{agent}`, "
        f"`{traj['result']['rounds_used']}` of `{traj.get('max_rounds', task.max_rounds)}` rounds.",
        f"- **As of:** {env.as_of}. Filings made after that date were not available to the team.",
        f"- **Verifier:** {verdict}.", "",
        f"Every figure is followed by its fact id and the filing it comes from: the form and its {ids}. A derived "
        "figure lists every filing behind it. The text is assembled from the team's submitted facts by a fixed "
        "template; no language model wrote it.", "",
    ]

    columns, others = _comparison(env, fields)
    if columns:
        companies = list(dict.fromkeys(c for _, g in columns for c in g))
        first = columns[0][1]
        companies.sort(key=lambda c: (c not in first, -(env.facts[first[c]].value if c in first else 0)))
        out += ["## Comparison", "", f"Sorted by {columns[0][0]}, highest first.", "",
                "| Company | " + " | ".join(h for h, _ in columns) + " |",
                "|---|" + "---|" * len(columns)]
        for c in companies:
            cells = [figure(env, g[c]) if c in g else "n/a" for _, g in columns]
            out.append(f"| {env.dataset.companies[c].name} ({c}) | " + " | ".join(cells) + " |")
        out.append("")
    if others:
        out += ["## Sector-wide answers" if columns else "## Answers", ""]
        out += [f"- `{f}`: {figure(env, fields[f])}" for f in others]
        out.append("")

    out += ["## How each answer was derived", ""]
    for f, fid in fields.items():
        fact = env.facts[fid]
        check = "within tolerance" if by_field.get(f) else "outside tolerance"
        out.append(f"### `{f}` ({check})")
        out.append("")
        if fact.meta.get("source") == "calculation":
            out.append(f"{figure(env, fid)} = `{fact.meta['formula']}`, where:")
            out.append("")
            out += _derivation(env, fid)
        else:
            out.append(f"{figure(env, fid)}: {fact.label}.")
        out.append("")

    filings = {}
    for x in leaves:
        fact = env.facts[x]
        if fact.meta.get("rcept_no") and fact.meta.get("year"):
            filings.setdefault((fact.meta["company"], fact.meta["rcept_no"]), fact)
    if filings:
        out += ["## Sources", "", "| Company | Period | Form | Filing | Filed |", "|---|---|---|---|---|"]
        rank = {c: i for i, c in enumerate(dict.fromkeys(code for code, _ in filings))}
        rows = sorted(filings.items(), key=lambda kv: (rank[kv[0][0]], kv[1].meta["year"],
                                                       PERIODS.index(kv[1].meta["period"])))
        for (code, rcept_no), fact in rows:
            company = env.dataset.companies[code]
            out.append(f"| {company.name} | {_period(env, fact)} | {fact.meta.get('form') or company.source} | "
                       f"{filing_link(company, rcept_no)} | {fact.meta.get('filed') or 'n/a'} |")
        out.append("")
    out += [SOURCE_NOTES[s] for s in sources if s in SOURCE_NOTES]
    note = "\n".join(out).rstrip() + "\n"
    bad = uncited_figures(note)
    if bad:
        raise NoteError(f"uncited figures in the note: {', '.join(bad)}")
    return note
