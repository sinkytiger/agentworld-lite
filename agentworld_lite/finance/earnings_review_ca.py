"""Earnings review of a Big 5 Canadian bank (English): when a bank files a new interim or annual report, put the quarter
into a variance table, run model QC and write a draft note in which every figure cites its facts and filings.

    python -m agentworld_lite.finance earnings-review --market ca --group all                 # offline, latest reports
    python -m agentworld_lite.finance earnings-review --market ca --group RY --year 2026 --period H1

The Canadian counterpart of earnings_review.py (the DART reviewer), modelled on the same Earnings Reviewer agent of
anthropics/financial-services (Apache-2.0): pull the print, update the coverage model, run model QC, draft the note,
stage it for review, cite every number, never publish. The print is the desk's SEC EDGAR dataset (Form 6-K interim
and 40-F annual reports, XBRL as filed). Figures are fiscal year to date (fiscal years end October 31), so a quarter
alone is the difference of two reports. Filings only: no earnings-call transcript and no consensus, so the quarter is
compared with the previous quarter and, when the dataset holds both reports it needs, the same quarter a year earlier;
the year to date is compared with the same months a year earlier. Rules only, no model draft. Outputs go to
reports/earnings/ca (outside the repository's history), marked as a draft for review; nothing is posted or sent.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import BIG5
from .dataset import Company, Dataset, Fact, statement_facts
from .env import REPO_ROOT
from .note import ID_NAMES, SOURCE_NOTES, cited, filing_link, uncited_figures

DATA_DIR = REPO_ROOT / "data" / "finance" / "edgar"
OUT_DIR = REPO_ROOT / "reports" / "earnings" / "ca"
ORDER = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}
QUARTER = {"Q1": ("Q1", None), "H1": ("Q2", "Q1"), "Q3": ("Q3", "H1"), "FY": ("Q4", "Q3")}
PREV_Q = {"Q1": ("FY", -1), "H1": ("Q1", 0), "Q3": ("H1", 0), "FY": ("Q3", 0)}  # report of the previous quarter
YTD = {"Q1": "Q1 (3 months)", "H1": "H1 year to date (6 months)", "Q3": "Q3 year to date (9 months)",
       "FY": "full year (12 months)"}
MONTHS = {"Q1": 3, "H1": 6, "Q3": 9, "FY": 12}
FLOWS = ("revenue", "non_interest_expense", "net_income", "net_income_owners", "net_income_common")
LABEL = {"revenue": "Revenue", "non_interest_expense": "Non-interest expense",
         "net_income_common": "Net income to common shareholders",
         "net_income": "Net income (incl. non-controlling interests; net income to common not filed)"}
BANNER = "Draft for review · educational example, not investment advice · a person reviews before anything is shared"
SWING = 40.0  # percent: a larger year-to-date move in revenue is flagged for review
QOQ_SWING = 25.0  # percent: a larger quarter-on-quarter move in net income is flagged for review


class ReviewError(Exception):
    pass


def _qlabel(year: int, period: str) -> str:
    return f"fiscal {year} {QUARTER[period][0]}"


@dataclass
class Quarter:
    year: int
    period: str                                  # the report: Q1, H1, Q3 or FY (fiscal year to date)
    values: dict[str, tuple[float, list[Fact]]]  # the quarter alone per account, with the facts behind it
    cum: dict[str, Fact]                         # the report's own facts (year to date, balances at its end)

    @property
    def label(self) -> str:
        return _qlabel(self.year, self.period)


@dataclass
class Review:
    code: str
    company: Company
    year: int
    period: str
    now: Quarter
    prev: Quarter | None          # the previous quarter alone
    year_ago: Quarter | None      # the same quarter a year earlier, alone
    prev_report: dict[str, Fact]  # the previous quarter's report: balances and ratios at its end
    ago_report: dict[str, Fact]   # the same report a year earlier: year to date, balances at its end
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.company.name

    @property
    def label(self) -> str:
        return self.now.label


def latest_report(ds: Dataset, code: str) -> tuple[int, str]:
    keys = [k.split(":") for k in ds.companies[code].data["statements"] if k.endswith(":CFS")]
    year, period, _ = max(keys, key=lambda k: (int(k[0]), ORDER[k[1]]))
    return int(year), period


def _report(c: Company, year: int, period: str) -> dict[str, Fact]:
    return {f.meta["account"]: f for f in statement_facts(c, year, period, "CFS")}


def _missing(c: Company, year: int, period: str) -> list[str]:
    """Reports the quarter alone of (year, period) needs that the dataset does not have."""
    return [f"fiscal {year} {p}" for p in (period, QUARTER[period][1]) if p and c.statement(year, p, "CFS") is None]


def _end(c: Company, year: int, period: str) -> str:
    return (c.statement(year, period, "CFS") or {}).get("period_end") or f"the end of fiscal {year} {period}"


def quarter(ds: Dataset, code: str, year: int, period: str) -> Quarter | None:
    """The quarter a report covers, alone: Q2 = H1 - Q1, Q3 = 9M - H1, Q4 = FY - 9M (fiscal, year to date)."""
    c = ds.companies[code]
    cum = _report(c, year, period)
    if not cum:
        return None
    before = QUARTER[period][1]
    prior = _report(c, year, before) if before else {}
    if before and not prior:
        return None
    values = {}
    for a in FLOWS:
        if a in cum and (not before or a in prior):
            values[a] = ((cum[a].value - prior[a].value, [cum[a], prior[a]]) if before else (cum[a].value, [cum[a]]))
    return Quarter(year, period, values, cum)


def _ytd(report: dict[str, Fact]) -> dict[str, tuple[float, list[Fact]]]:
    return {a: (report[a].value, [report[a]]) for a in FLOWS if a in report}


def _efficiency(v: dict[str, tuple[float, list[Fact]]]) -> tuple[float, list[Fact]] | None:
    """Non-interest expense / revenue x 100, for a quarter alone or a year to date."""
    if "revenue" not in v or "non_interest_expense" not in v or v["revenue"][0] <= 0:
        return None
    return v["non_interest_expense"][0] / v["revenue"][0] * 100, [*v["non_interest_expense"][1], *v["revenue"][1]]


def _ni(v: dict) -> str:
    return "net_income_common" if "net_income_common" in v else "net_income"


def _pct(now: float, before: float) -> float:
    return (now / before - 1) * 100


def review(code: str, year: int | None = None, period: str | None = None, data_dir: str | Path = DATA_DIR,
           today: str | None = None) -> Review:
    ds = Dataset(Path(data_dir))
    c = ds.find(code)
    if c is None:
        raise ReviewError(f"{code} is not one of the banks in {data_dir}")
    code = c.stock_code
    if year is None or period is None:
        year, period = latest_report(ds, code)
    now = quarter(ds, code, year, period)
    if now is None:
        raise ReviewError(f"{code}: no fiscal {year} {period} report (or the report before it) in the dataset")
    pp, dy = PREV_Q[period]
    rv = Review(code, c, year, period, now, quarter(ds, code, year + dy, pp), quarter(ds, code, year - 1, period),
                _report(c, year + dy, pp), _report(c, year - 1, period))
    rv.checks = qc(rv, today or dt.date.today().isoformat())
    return rv


def qc(rv: Review, today: str) -> list[tuple[str, bool, str]]:
    """Model QC: the checks a reviewer would run before reading the numbers."""
    st, v, c, out = rv.now.cum, rv.now.values, rv.company, []
    pos = [a for a in ("revenue", "non_interest_expense") if a in v and v[a][0] > 0]
    out.append(("Quarter-alone values are positive", len(pos) == 2,
                "revenue and non-interest expense" + ("" if len(pos) == 2 else ": one is negative or missing")))
    eff = _efficiency(v)
    out.append(("Efficiency ratio within 30-80 (percent)", bool(eff) and 30 <= eff[0] <= 80,
                "if outside: check one-off items and the account mapping"))
    tot, own = st.get("equity_total"), st.get("equity_owners")
    if own:
        ok, note = bool(tot) and tot.value >= own.value > 0, "total equity and equity attributable to shareholders"
    else:
        ok, note = bool(tot) and tot.value > 0, ("equity attributable to shareholders is not in the filing's XBRL; "
                                                 "total equity checked alone")
    out.append(("Total equity >= equity attributable to shareholders > 0", ok, note))
    pp, dy = PREV_Q[rv.period]
    gaps = _missing(c, rv.year + dy, pp) + _missing(c, rv.year - 1, rv.period)
    out.append(("Comparison reports are in the dataset", not gaps,
                "previous quarter and the same quarter a year earlier"
                + (f"; missing: {', '.join(gaps)} (year-to-date comparison shown instead)" if gaps else "")))
    filed = (c.statement(rv.year, rv.period, "CFS") or {}).get("filed") or ""
    out.append(("Filing date is not after today", bool(filed) and filed <= today, f"filed {filed or 'n/a'}"))
    cet1 = st.get("cet1_ratio")
    out.append(("CET1 ratio within 8-20 (percent)", bool(cet1) and 8 <= cet1.value <= 20,
                "if outside: check the unit (percent, not a fraction) and the concept" if cet1
                else "not in the report"))
    r1, r0 = st.get("revenue"), rv.ago_report.get("revenue")
    if r1 and r0 and r0.value > 0:
        chg = _pct(r1.value, r0.value)
        ok = abs(chg) <= SWING
        k1, k0 = r1.meta.get("concept"), r0.meta.get("concept")
        why = (f"the XBRL concept is the same in both years (`{k1}`): check whether the move is real or a "
               "reclassification" if k1 == k0 else f"the XBRL concept changed (`{k0}` to `{k1}`): check the mapping")
        note = (f"{MONTHS[rv.period]} months of fiscal {rv.year} vs fiscal {rv.year - 1}" if ok
                else f"{_change(r1.value, r0.value, r1.unit, [r1, r0])}: {why}")
    else:
        ok, note = False, f"no fiscal {rv.year - 1} {rv.period} revenue to compare"
    out.append((f"Revenue year to date within {SWING:.0f} percent of a year earlier", ok, note))
    ni = next((a for a in ("net_income_common", "net_income") if a in v and rv.prev and a in rv.prev.values), None)
    if ni and rv.prev.values[ni][0] > 0:
        chg = _pct(v[ni][0], rv.prev.values[ni][0])
        ok = abs(chg) <= QOQ_SWING
        note = ("quarter alone vs the previous quarter" if ok else
                f"{_change(v[ni][0], rv.prev.values[ni][0], 'CAD', [*v[ni][1], *rv.prev.values[ni][1]])}: look for "
                "one-off items in the quarter (impairments, acquisition or divestiture charges, legal provisions)")
    else:
        ok, note = False, "no previous quarter to compare"
    out.append((f"Net income within {QOQ_SWING:.0f} percent of the previous quarter", ok, note))
    return out


# ---------------------------------------------------------------------- the note (English Markdown)
def objectives_section(rv: Review) -> list[str]:
    """The bank's objectives, quoted from its fiscal 2025 annual filing (objectives_ca), with the full check there."""
    from .objectives_ca import DOCS, objectives_lines
    if rv.code not in DOCS:
        return ["No quoted medium-term objectives for this bank."]
    return [*objectives_lines(rv.code), "",
            "Progress against these objectives, with each bank's own fiscal 2025 ROE: "
            "`python -m agentworld_lite.finance objectives-ca`."]


def _change(v: float, v0: float, unit: str, facts: list[Fact]) -> str:
    """Signed change: percentage points for a ratio, percent for an amount."""
    if unit != "%" and v0 <= 0:
        return "n/a"
    x = v - v0 if unit == "%" else _pct(v, v0)
    return ("+" if x > 0 else "") + cited(x, "pp" if unit == "%" else "%", facts)


def _cells(now: tuple[float, list[Fact]], other: tuple[float, list[Fact]] | None, unit: str) -> list[str]:
    if other is None:
        return ["n/a", "n/a"]
    (v, f), (v0, f0) = now, other
    return [cited(v0, unit, f0), _change(v, v0, unit, [*f, *f0])]


def _table(rv: Review, now: dict, others: list[dict | None]) -> list[str]:
    cur, rows = rv.company.currency, []
    lines = [(LABEL[a], lambda v, a=a: v.get(a), cur) for a in ("revenue", "non_interest_expense", _ni(now))]
    for name, get, unit in [*lines, ("Efficiency ratio", _efficiency, "%")]:
        x = get(now)
        if x is None:
            continue
        cells = [name, cited(x[0], unit, x[1])]
        for o in others:
            cells += _cells(x, get(o) if o is not None else None, unit)
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def render(rv: Review) -> str:
    c, cur = rv.company, rv.company.currency
    st = c.statement(rv.year, rv.period, "CFS")
    before = QUARTER[rv.period][1]
    pp, dy = PREV_Q[rv.period]
    m, d = (int(x) for x in (c.data.get("fiscal_year_end") or "10-31").split("-"))
    out = [f"# {rv.name} ({rv.code}): {rv.label} earnings review", "",
           f"> **{BANNER}.** Written by fixed rules from the desk's SEC EDGAR dataset; no language model wrote it. "
           "Every figure is followed by its fact ids and the filings behind it (form and SEC EDGAR accession number). "
           "Filings only: there is no earnings-call transcript and no consensus estimate, so the quarter is compared "
           "with the previous quarter and the same quarter a year earlier, and the year to date with the same months a "
           f"year earlier. Money is in {'Canadian dollars (C$)' if cur == 'CAD' else cur} as filed under IFRS, not "
           "adjusted.", "",
           f"- **Report:** Form {st.get('form') or 'filing'} {filing_link(c, st.get('doc_id') or st.get('rcept_no'))}, "
           f"filed {st.get('filed')}: fiscal {rv.year} {YTD[rv.period]} to {st.get('period_end')}; the fiscal year "
           f"ends {dt.date(2000, m, d):%B} {d}.",
           f"- **Quarter alone:** {rv.label} = " + (f"{YTD[rv.period]} minus {YTD[before]}." if before
                                                   else "the Q1 report as filed.")]
    if rv.year_ago is None:
        out.append(f"- **Same quarter a year earlier:** {_qlabel(rv.year - 1, rv.period)} alone cannot be computed: "
                   f"the dataset has no {' or '.join(_missing(c, rv.year - 1, rv.period))} report. The year-to-date "
                   "comparison below gives the year-on-year view instead.")
    cols = [(_qlabel(rv.year + dy, pp), "previous quarter", rv.prev)]
    cols += [(rv.year_ago.label, "a year earlier", rv.year_ago)] if rv.year_ago else []
    out += ["", "## Variance table", "",
            f"| Line | {rv.label} | " + " | ".join(f"{lab} | vs {what}" for lab, what, _ in cols) + " |",
            "|---|---|" + "---|---|" * len(cols)]
    out += _table(rv, rv.now.values, [q.values if q else None for _, _, q in cols])
    out += ["", "Changes in money lines are percent changes; the efficiency ratio (non-interest expense / revenue) "
            "changes in percentage points (pp), and lower is leaner."]
    ytd_now, ytd_ago = (f"{MONTHS[rv.period]} months, fiscal {y}" for y in (rv.year, rv.year - 1))
    out += ["", "## Year to date", "", f"| Line | {ytd_now} | {ytd_ago} | vs a year earlier |", "|---|---|---|---|"]
    out += _table(rv, _ytd(rv.now.cum), [_ytd(rv.ago_report) if rv.ago_report else None])
    out += ["", "## Capital and per share", ""]
    for a, name, unit in (("cet1_ratio", "CET1 ratio", "%"), ("rwa", "Risk-weighted assets (RWA)", cur)):
        f = rv.now.cum.get(a)
        if not f:
            out.append(f"- {name}: not in the report's XBRL.")
            continue
        parts = []
        for rep, y, p in ((rv.prev_report, rv.year + dy, pp), (rv.ago_report, rv.year - 1, rv.period)):
            f0, when = rep.get(a), _end(c, y, p)
            parts.append(f"since {when} ({cited(f0.value, unit, [f0])}): {_change(f.value, f0.value, unit, [f, f0])}"
                         if f0 else f"since {when}: no figure in the dataset")
        out.append(f"- {name} at {st.get('period_end')}: {cited(f.value, unit, [f])}; " + "; ".join(parts) + ".")
    e1, e0 = rv.now.cum.get("eps_diluted"), rv.ago_report.get("eps_diluted")
    if e1:
        line = f"- Diluted EPS, {ytd_now}: {cited(e1.value, f'{cur}/share', [e1])}"
        if e0:
            line += (f"; {ytd_ago}: {cited(e0.value, f'{cur}/share', [e0])}, change "
                     f"{_change(e1.value, e0.value, f'{cur}/share', [e1, e0])}")
        out.append(line + ". EPS is not additive across quarters (share counts and preferred dividends change), so no "
                   "quarter-alone EPS is derived.")
    out += ["", "## Medium-term objectives", "", *objectives_section(rv)]
    out += ["", "## Model QC", "", "| Check | Result | Note |", "|---|---|---|"]
    out += [f"| {name} | {'pass' if ok else 'check'} | {note} |" for name, ok, note in rv.checks]
    out += ["", "## Before sharing", "",
            "- Reported results as filed: one-off items (acquisition and integration costs, divestiture charges, "
            "impairments) are not adjusted. The banks also publish adjusted results, which differ.",
            "- Provisions for credit losses are not in the dataset, so credit quality and pre-provision earnings are "
            "not covered.",
            "- Check against the bank's Report to Shareholders before sharing; its own definitions of the efficiency "
            "ratio and net income to common may differ.",
            "- No rating, no target price and no share price.", ""]
    keys = {(rv.year, rv.period), (rv.year + dy, pp), (rv.year - 1, rv.period)}
    keys |= {(q.year, QUARTER[q.period][1]) for q in (rv.now, rv.prev, rv.year_ago) if q and QUARTER[q.period][1]}
    out += ["## Sources", "", "| Period | Form | Filing | Filed |", "|---|---|---|---|"]
    for y, p in sorted(keys, key=lambda k: (k[0], ORDER[k[1]])):
        s = c.statement(y, p, "CFS")
        if s:
            out.append(f"| fiscal {y} {YTD[p]} to {s.get('period_end')} | {s.get('form') or 'filing'} | "
                       f"{filing_link(c, s.get('doc_id') or s.get('rcept_no'))} | {s.get('filed') or 'n/a'} |")
    out += ["", f"Identifiers: {ID_NAMES['EDGAR']} for every filing. {SOURCE_NOTES['EDGAR']}", ""]
    md = "\n".join(out).rstrip() + "\n"
    from .objectives_ca import QUOTE
    bad = uncited_figures(md, extra=(QUOTE,))  # quoted objectives carry their 40-F source
    if bad:
        raise ReviewError(f"uncited figures in the review: {', '.join(bad[:5])}")
    return md


def coverage_model(rv: Review, ds: Dataset | None = None) -> dict:
    """The 'model' a reviewer updates: every quarter alone the dataset supports for the bank, with sources."""
    ds = ds or Dataset(DATA_DIR)
    c = ds.companies[rv.code]
    out = {"bank": rv.code, "name": rv.name, "as_of_report": f"{rv.year}:{rv.period}", "currency": c.currency,
           "fiscal_year_end": c.data.get("fiscal_year_end"), "period_ends": {}, "quarters": {}}
    for key in sorted(c.data["statements"], key=lambda k: (int(k.split(":")[0]), ORDER[k.split(":")[1]])):
        y, p, basis = key.split(":")
        q = quarter(ds, rv.code, int(y), p) if basis == "CFS" else None
        if q:
            lines = {**q.values, **({"efficiency_ratio": _efficiency(q.values)} if _efficiency(q.values) else {})}
            out["period_ends"][q.label] = _end(c, q.year, q.period)
            out["quarters"][q.label] = {
                a: {"value": v, "facts": [f.id for f in fs], "accession": sorted({f.meta["rcept_no"] for f in fs})}
                for a, (v, fs) in lines.items()}
    return out


def write(rv: Review, out_dir: str | Path = OUT_DIR, data_dir: str | Path = DATA_DIR) -> Path:
    d = Path(out_dir) / f"{rv.code}_{rv.year}{rv.period}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "review.md").write_text(render(rv), encoding="utf-8", newline="\n")
    (d / "model.json").write_text(json.dumps(coverage_model(rv, Dataset(Path(data_dir))), ensure_ascii=False, indent=1)
                                  + "\n", encoding="utf-8", newline="\n")
    return d


def groups(arg: str, data_dir: str | Path = DATA_DIR) -> list[str]:
    if arg.lower() == "all":
        return list(BIG5)
    c = Dataset(Path(data_dir)).find(arg)
    if c is None or c.stock_code not in BIG5:
        raise ReviewError(f"unknown bank {arg!r}; use all, a ticker ({', '.join(BIG5)}) or a full name such as "
                          "Royal Bank of Canada")
    return [c.stock_code]
