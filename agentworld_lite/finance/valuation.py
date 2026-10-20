"""Valuation snapshot of one Big 5 bank against the other four, from filed figures only. No model writes it.

    python -m agentworld_lite.finance valuation --bank RY --out ry_valuation.md

For each bank: trailing twelve-month (TTM) diluted EPS = fiscal-year EPS - prior-year Q3 year-to-date EPS + Q3
year-to-date EPS, the P/E at the closing share price on the fiscal Q3 period end (as printed in the bank's Q3
Report to Shareholders), the efficiency ratio of fiscal Q3 alone and
the CET1 ratio. The subject's TTM EPS times the peers' lowest, median and highest P/E gives the range those
multiples imply. Every number cites its fact ids and the filings behind them (form and accession; prices cite the
exhibit they were read from). An educational sample: no rating, no target price, not investment advice.
"""

from __future__ import annotations

import statistics
from pathlib import Path

from .catalog import BIG5
from .dataset import Dataset, Fact, price_fact, statement_facts
from .env import REPO_ROOT
from .note import ID_NAMES, cited, filing_link, uncited_figures

RATING_WORDS = ("buy", "sell", "hold", "outperform", "underperform", "overweight", "underweight", "target price")


class ValuationError(Exception):
    pass


def bank_metrics(ds: Dataset, code: str, year: int, as_of: str) -> dict:
    """TTM diluted EPS, P/E, Q3-alone efficiency ratio and CET1 of fiscal `year`, each as (value, unit, facts)."""
    c = ds.companies[code]
    need = {(year - 1, "FY"), (year - 1, "Q3"), (year, "H1"), (year, "Q3")}
    facts = {}
    for y, p in sorted(need):
        st = c.statement(y, p, "CFS")
        if st is None or (st.get("filed") or "") > as_of:
            raise ValuationError(f"{code}: no fiscal {y} {p} report filed by {as_of}")
        facts[(y, p)] = {f.meta["account"]: f for f in statement_facts(c, y, p, "CFS")}
    end = c.statement(year, "Q3", "CFS")["period_end"]
    price = price_fact(c, end)
    if price is None:
        raise ValuationError(f"{code}: no closing price for {end}; add it with its source (fetch --price/--price-source)")
    fy, prev, q3, h1 = facts[(year - 1, "FY")], facts[(year - 1, "Q3")], facts[(year, "Q3")], facts[(year, "H1")]
    eps_facts = [fy["eps_diluted"], prev["eps_diluted"], q3["eps_diluted"]]
    ttm = fy["eps_diluted"].value - prev["eps_diluted"].value + q3["eps_diluted"].value
    nie, rev = "non_interest_expense", "revenue"
    return {
        "code": code, "name": c.name, "period_end": end, "currency": c.currency,
        "price": (price.value, c.currency, [price]),
        "ttm_eps": (ttm, f"{c.currency}/share", eps_facts),
        "pe": (price.value / ttm, "x", [price, *eps_facts]),
        "efficiency": ((q3[nie].value - h1[nie].value) / (q3[rev].value - h1[rev].value) * 100, "%",
                       [q3[nie], h1[nie], q3[rev], h1[rev]]),
        "cet1": (q3["cet1_ratio"].value, "%", [q3["cet1_ratio"]]),
    }


def _combine(rows: list[tuple]) -> list[Fact]:
    return [f for _, _, facts in rows for f in facts]


def _median(rows: list[tuple]) -> tuple:
    return (statistics.median(v for v, _, _ in rows), rows[0][1], _combine(rows))


def snapshot(subject: str = "RY", *, as_of: str = "2026-09-30", year: int = 2026,
             data_dir: str | Path | None = None) -> tuple[str, dict]:
    ds = Dataset(Path(data_dir) if data_dir else REPO_ROOT / "data" / "finance" / "edgar")
    subject = (ds.find(subject).stock_code if ds.find(subject) else subject).upper()
    if subject not in BIG5:
        raise ValuationError(f"unknown bank {subject!r}; choose one of {', '.join(BIG5)}")
    m = {code: bank_metrics(ds, code, year, as_of) for code in BIG5}
    s, peers = m[subject], [m[c] for c in BIG5 if c != subject]
    pes = sorted((p["pe"] for p in peers), key=lambda r: r[0])
    low, high, med = pes[0], pes[-1], _median(pes)
    eps = s["ttm_eps"]
    implied = {k: (eps[0] * r[0], eps[1], [*eps[2], *r[2]]) for k, r in (("low", low), ("median", med), ("high", high))}
    numbers = {"pe": {c: m[c]["pe"][0] for c in BIG5}, "ttm_eps": {c: m[c]["ttm_eps"][0] for c in BIG5},
               "peer_pe": {"low": low[0], "median": med[0], "high": high[0]},
               "implied": {k: v[0] for k, v in implied.items()}, "price": s["price"][0]}
    md = render(ds, subject, m, peers, low, med, high, implied, as_of, year)
    bad = uncited_figures(md)
    if bad:
        raise ValuationError(f"uncited figures in the snapshot: {', '.join(bad)}")
    return md, numbers


def _position(price: float, lo: float, hi: float) -> str:
    if price < lo:
        return "below the range the peer multiples imply"
    if price > hi:
        return "above the range the peer multiples imply"
    return "inside the range the peer multiples imply"


def render(ds, subject, m, peers, low, med, high, implied, as_of, year) -> str:
    s = m[subject]
    short = {"RY": "RBC", "TD": "TD", "BNS": "Scotiabank", "BMO": "BMO", "CM": "CIBC"}
    peer_names = ", ".join(short[p["code"]] for p in peers)
    eff_med = _median([p["efficiency"] for p in peers])
    cet1_med = _median([p["cet1"] for p in peers])
    out = [
        f"# Valuation snapshot: {s['name']} ({subject}) against the Big 5", "",
        "*Educational sample built from public filings by fixed rules; no language model wrote it. Not investment "
        "advice: no rating, no target price.*", "",
        f"- **As of:** {as_of}, using only filings made by then. Prices are the closing share prices on "
        f"{s['period_end']}, the end of fiscal {year} Q3, as printed in each bank's Q3 Report to Shareholders, in "
        "Canadian dollars.",
        f"- **Peers:** the other four of the Big 5 ({peer_names}).",
        "- **Citations:** every number is followed by the fact ids it comes from and the filings behind them: form "
        "and SEC EDGAR accession number, and for prices the exhibit and line they were read from.", "",
        "## Peer table", "",
        "| Bank | Close | TTM diluted EPS | P/E | Efficiency ratio, Q3 alone | CET1 ratio |",
        "|---|---|---|---|---|---|",
    ]
    for code in BIG5:
        r = m[code]
        name = f"**{r['name']} ({code})**" if code == subject else f"{r['name']} ({code})"
        out.append(f"| {name} | {cited(*r['price'])} | {cited(*r['ttm_eps'])} | {cited(*r['pe'])} | "
                   f"{cited(*r['efficiency'])} | {cited(*r['cet1'])} |")
    out += [
        "",
        f"## What the peer multiples imply for {short[subject]}", "",
        f"- Peer P/E: lowest {cited(*low)}, median {cited(*med)}, highest {cited(*high)}.",
        f"- {short[subject]}'s TTM diluted EPS {cited(*s['ttm_eps'])} times those multiples gives "
        f"{cited(*implied['low'])} at the lowest, {cited(*implied['median'])} at the median and "
        f"{cited(*implied['high'])} at the highest.",
        f"- {short[subject]}'s own close was {cited(*s['price'])}, a P/E of {cited(*s['pe'])}: "
        f"{_position(s['price'][0], implied['low'][0], implied['high'][0])}. This is a description of where the "
        "price sat on one date, not a view on where it should be.", "",
        "## Context the multiple leaves out", "",
        f"- **Cost.** {short[subject]}'s efficiency ratio for fiscal {year} Q3 alone was {cited(*s['efficiency'])}, "
        f"against a peer median of {cited(*eff_med)} (lower is leaner).",
        f"- **Capital.** Its CET1 ratio was {cited(*s['cet1'])}, against a peer median of {cited(*cet1_med)}.",
        "- **One-off items.** These are reported, not adjusted, earnings. A bank whose last four quarters include a "
        "large one-off charge or gain shows a P/E that its underlying earnings would not; BMO's reported fiscal "
        "2026 Q3 results, for example, include an after-tax goodwill charge tied to an announced divestiture, which "
        "lowers its TTM EPS and raises its P/E.", "",
        "## Methods", "",
        "- **TTM diluted EPS** = fiscal-year EPS - prior-year Q3 year-to-date EPS + current Q3 year-to-date EPS. "
        "The banks' fiscal year ends October 31, so the TTM window is August of the prior fiscal year to July.",
        "- **P/E** = closing share price on the Q3 period end / TTM diluted EPS. The price is the one each bank "
        "printed in its Q3 Report to Shareholders, checked against the exhibit text.",
        "- **Efficiency ratio** = non-interest expense / total revenue for fiscal Q3 alone (Q3 year-to-date minus "
        "H1 year-to-date), in percent, from the XBRL as filed. Banks also report adjusted ratios; these are not.",
        "- **CET1 ratio** = Common Equity Tier 1 capital / risk-weighted assets at the period end, as reported.",
        "- **Implied range** = the subject's TTM diluted EPS times the lowest, median and highest P/E of the other "
        "four banks. It shows what peer multiples imply; it is not a fair value or a forecast. No forward estimates "
        "are used.",
        "- All five banks report in Canadian dollars under IFRS; figures are as originally filed on SEC EDGAR "
        "(Form 40-F annual and Form 6-K interim reports, the same reports the banks file on SEDAR+).", "",
        "## Disclaimer", "",
        "This is an educational sample that demonstrates a reproducible workflow in which every figure can be traced "
        "to a filing. It is not investment advice, not a recommendation to trade any security, and it gives no "
        "rating and no target price. Figures can contain errors: check the cited filings before relying on any of "
        "them.", "",
    ]
    out += ["## Sources", "", "| Company | Period | Form | Filing | Filed |", "|---|---|---|---|---|"]
    for code in BIG5:
        c = ds.companies[code]
        for y, p in ((year - 1, "Q3"), (year - 1, "FY"), (year, "H1"), (year, "Q3")):
            st = c.statement(y, p, "CFS")
            out.append(f"| {c.name} | fiscal {y} {p} (to {st['period_end']}) | {st['form']} | "
                       f"{filing_link(c, st['doc_id'])} | {st['filed']} |")
    out += ["", "Prices: " + "; ".join(f"{code}: `{ds.companies[code].data['price_sources'][m[code]['period_end']]}`"
                                      for code in BIG5) + ".", "",
            f"Identifiers: {ID_NAMES['EDGAR']} for every filing."]
    return "\n".join(out).rstrip() + "\n"
