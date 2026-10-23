"""Korea and Canada side by side on filed figures: the four Korean financial holding companies (DART) and Canada's
Big 5 banks (SEC EDGAR) on CET1, cost-to-income and ROE in one table, every figure cited. No language model.

    python -m agentworld_lite.finance compare --out reports/compare.md

One formula in both markets where the filings allow it, each market's own convention where they do not:

  ROE             TTM net income attributable to shareholders / average shareholders' equity at the start and end of
                  the twelve months. Shareholders' equity includes preferred shares and hybrid capital in both markets
                  (Korea's 신종자본증권, Canada's other equity instruments), because neither data set separates common
                  equity for every group; this is the basis Korea's groups quote in their value-up plans, and it reads
                  lower than the common-equity ROE Canada's banks quote. TD tags neither line, so its ROE uses net
                  income and total equity, both including any non-controlling interests.
  Cost-to-income  the latest quarter alone. Korea: general and administrative expense / (operating income + G&A +
                  credit-loss provisions), i.e. G&A over operating revenue before provisions. Canada: non-interest
                  expense / total revenue, the banks' reported efficiency ratio.
  CET1            Korea: the ratio each group printed in its 2026 H1 report (DART's financial statements carry no
                  capital ratios), quoted with its receipt number; Woori's H1 report gives none. Canada: the ratio in
                  each bank's fiscal 2026 Q3 XBRL.

Periods: Korea to 2026-06-30 (calendar 2026 Q2), Canada to 2026-07-31 (fiscal 2026 Q3). Educational example, not
investment advice.
"""

from __future__ import annotations

import statistics
from pathlib import Path

from . import valueup as vu
from .catalog import BIG5
from .dataset import Dataset, Fact, statement_facts
from .env import REPO_ROOT
from .note import filing_link, fmt_figure, uncited_figures

KR_DIR = REPO_ROOT / "data" / "finance" / "dart_fhc"
CA_DIR = REPO_ROOT / "data" / "finance" / "edgar"
KR_GROUPS = {"105560": "KB Financial", "055550": "Shinhan Financial", "086790": "Hana Financial",
             "316140": "Woori Financial"}
CA_BANKS = {"RY": "Royal Bank of Canada", "TD": "TD Bank", "BNS": "Scotiabank", "BMO": "BMO", "CM": "CIBC"}
KR_PERIOD, CA_PERIOD = "2026-06-30", "2026-07-31"
NOTICE = "Educational example, not investment advice"


class CompareError(Exception):
    pass


# ---------------------------------------------------------------------- citations
def _form_en(form: str) -> str:
    kind = "half-year report" if "반기" in form else "quarterly report" if "분기" in form else \
        "annual report" if "사업" in form else form
    return f"amended {kind}" if "정정" in form else kind


def where(fact: Fact) -> str:
    m = fact.meta
    if m.get("source") == "DART":
        return f"DART {_form_en(m['form'])} {m['rcept_no']}" if m.get("form") else f"DART {m['rcept_no']}"
    return f"{m['form']} {m['rcept_no']}"


def cite(facts: list[Fact]) -> str:
    facts = list({f.id: f for f in facts}.values())
    wheres = list(dict.fromkeys(where(f) for f in facts))
    ids = ", ".join(f"`{f.id}`" for f in facts)
    return f"[{'fact' if len(facts) == 1 else 'facts'} {ids} from {'; '.join(wheres)}]"


def cited(m: tuple | None) -> str:
    return f"{fmt_figure(m[0], m[1])} {cite(m[2])}" if m else "n/a"


# ---------------------------------------------------------------------- measures
def _stmt(c, year: int, period: str) -> dict[str, Fact]:
    return {f.meta["account"]: f for f in statement_facts(c, year, period, "CFS")}


def _roe(ni: tuple[Fact, Fact, Fact], e0: Fact, e1: Fact) -> tuple:
    ttm = ni[0].value - ni[1].value + ni[2].value
    return ttm / ((e0.value + e1.value) / 2) * 100, "%", [*ni, e0, e1]


def korea_rows(data_dir: str | Path = KR_DIR) -> list[dict]:
    ds = Dataset(Path(data_dir))
    rows = []
    for code, name in KR_GROUPS.items():
        c = ds.companies[code]
        fy25, h25, q126, h126 = _stmt(c, 2025, "FY"), _stmt(c, 2025, "H1"), _stmt(c, 2026, "Q1"), _stmt(c, 2026, "H1")
        ni, eq = "net_income_owners", "equity_owners"
        g, o, p = "general_admin_expense", "operating_income", "credit_loss_provision"
        q2 = lambda a: h126[a].value - q126[a].value
        cir = (q2(g) / (q2(o) + q2(g) + q2(p)) * 100, "%", [h126[g], q126[g], h126[o], q126[o], h126[p], q126[p]])
        c1 = vu.cet1_fact(code)
        ref = vu.cet1_reference_fact(code)
        rows.append({
            "market": "Korea", "code": code, "name": name, "period": KR_PERIOD,
            "cet1": (c1.value, "%", [c1]) if c1 else None,
            "cet1_note": (f"its 2026 H1 report gives no CET1 ratio; it reported {cited((ref.value, '%', [ref]))} at "
                          "the end of 2025") if ref else "",
            "cir": cir, "cir_basis": "G&A / operating revenue before provisions, Q2 2026 alone",
            "roe": _roe((fy25[ni], h25[ni], h126[ni]), h25[eq], h126[eq]),
            "roe_basis": "net income to owners / average owners' equity incl. hybrid capital",
        })
    return rows


def canada_rows(data_dir: str | Path = CA_DIR) -> list[dict]:
    ds = Dataset(Path(data_dir))
    rows = []
    for code in BIG5:
        c = ds.companies[code]
        fy25, q325, h126, q326 = _stmt(c, 2025, "FY"), _stmt(c, 2025, "Q3"), _stmt(c, 2026, "H1"), _stmt(c, 2026, "Q3")
        nie, rev = "non_interest_expense", "revenue"
        cir = ((q326[nie].value - h126[nie].value) / (q326[rev].value - h126[rev].value) * 100, "%",
               [q326[nie], h126[nie], q326[rev], h126[rev]])
        ni, eq = ("net_income_owners", "equity_owners") if "equity_owners" in q326 else ("net_income", "equity_total")
        rows.append({
            "market": "Canada", "code": code, "name": CA_BANKS[code], "period": CA_PERIOD,
            "cet1": (q326["cet1_ratio"].value, "%", [q326["cet1_ratio"]]), "cet1_note": "",
            "cir": cir, "cir_basis": "non-interest expense / total revenue, fiscal Q3 2026 alone",
            "roe": _roe((fy25[ni], q325[ni], q326[ni]), q325[eq], q326[eq]),
            "roe_basis": ("net income to shareholders / average shareholders' equity incl. preferred and other equity "
                          "instruments") if ni == "net_income_owners" else
                         "net income / average total equity (TD tags no shareholders' lines; includes any NCI)",
        })
    return rows


def _median(rows: list[dict], key: str) -> tuple | None:
    ms = [r[key] for r in rows if r[key]]
    if not ms:
        return None
    return statistics.median(m[0] for m in ms), ms[0][1], [f for m in ms for f in m[2]]


def comparison(kr_dir: str | Path = KR_DIR, ca_dir: str | Path = CA_DIR) -> dict:
    kr, ca = korea_rows(kr_dir), canada_rows(ca_dir)
    medians = {mk: {k: _median(rs, k) for k in ("cet1", "cir", "roe")} for mk, rs in (("Korea", kr), ("Canada", ca))}
    return {"rows": kr + ca, "medians": medians}


# ---------------------------------------------------------------------- report
def render(data: dict | None = None, kr_dir: str | Path = KR_DIR, ca_dir: str | Path = CA_DIR) -> str:
    data = data or comparison(kr_dir, ca_dir)
    rows, med = data["rows"], data["medians"]
    out = [
        "# Korea and Canada side by side: CET1, cost-to-income and ROE", "",
        f"*Built from filings by fixed rules; no language model wrote it. {NOTICE}: no rating, no target price.*", "",
        f"- **Who:** Korea's four largest financial holding companies (DART filings) and Canada's Big 5 banks (SEC "
        "EDGAR copies of their SEDAR+ filings).",
        f"- **When:** Korea to {KR_PERIOD} (calendar 2026 Q2), Canada to {CA_PERIOD} (fiscal 2026 Q3, the banks' year "
        "ending October 31): the latest quarter each market has filed, one month apart.",
        "- **Citations:** every figure is followed by the facts it comes from and the filings behind them: DART receipt "
        "numbers for Korea, SEC EDGAR accession numbers for Canada.", "",
        "## The table", "",
        "| Market | Group | CET1 ratio | Cost-to-income, latest quarter | ROE, trailing twelve months |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        cet1 = cited(r["cet1"]) if r["cet1"] else "n/a (see note)"
        out.append(f"| {r['market']} | {r['name']} | {cet1} | {cited(r['cir'])} | {cited(r['roe'])} |")
    out += ["", "## Market medians", "", "| Market | CET1 ratio | Cost-to-income | ROE |", "|---|---|---|---|"]
    for mk in ("Korea", "Canada"):
        m = med[mk]
        out.append(f"| {mk} | {cited(m['cet1'])} | {cited(m['cir'])} | {cited(m['roe'])} |")
    notes = [f"- **{r['name']}:** {r['cet1_note']}." for r in rows if r["cet1_note"]]
    out += ["", "Korea's CET1 median covers the three groups that printed a 2026 H1 ratio.", ""]
    out += ["## Notes on the figures", "", *notes,
            "- **TD:** " + next(r["roe_basis"] for r in rows if r["code"] == "TD") + ".", ""]
    out += [
        "## How each measure is built", "",
        "- **ROE** = trailing twelve-month net income attributable to shareholders / the average of shareholders' "
        "equity at the start and end of those twelve months. Korea: FY 2025 - H1 2025 + H1 2026, equity at 2025-06-30 "
        "and 2026-06-30. Canada: fiscal 2025 - Q3 YTD 2025 + Q3 YTD 2026, equity at 2025-07-31 and 2026-07-31. Equity "
        "includes preferred shares and hybrid capital in both markets, because neither data set separates common equity "
        "for every group. This is the basis Korea's groups use in their value-up plans; Canada's banks quote ROE on "
        "common equity, which reads higher than the figures here.",
        "- **Cost-to-income** is each market's own reporting convention, for the latest quarter alone (year-to-date "
        "minus the previous year-to-date). Korea: general and administrative expense / (operating income + G&A + "
        "credit-loss provisions), which is G&A over operating revenue before provisions. Canada: non-interest expense / "
        "total revenue, the efficiency ratio as reported (not adjusted). Both put operating costs over revenue before "
        "credit losses, but revenue is defined in each market's own way, so read gaps of a few points with care.",
        "- **CET1** is a regulatory ratio that DART's financial statements do not carry. Korea's figures are quoted "
        "from the capital adequacy tables of the 2026 H1 reports, with their receipt numbers (provisional at filing). "
        "Canada's are tagged in the fiscal Q3 XBRL. Both markets apply Basel III; national rules differ in detail.",
        "- **Not adjusted.** All figures are reported, as filed. One-off items (for example, BMO's goodwill charge in "
        "fiscal 2026 Q3, or TD's gain on selling Schwab shares in fiscal 2025) move cost-to-income and ROE.", "",
        "## Sources", "", "| Market | Group | Filing | Form | Filed |", "|---|---|---|---|---|",
    ]
    kr_ds, ca_ds = Dataset(Path(kr_dir)), Dataset(Path(ca_dir))
    seen = set()
    for r in rows:
        ds = kr_ds if r["market"] == "Korea" else ca_ds
        for key in ("cet1", "cir", "roe"):
            for f in (r[key][2] if r[key] else []):
                acc = f.meta["rcept_no"]
                if acc in seen:
                    continue
                seen.add(acc)
                form = _form_en(f.meta["form"]) if r["market"] == "Korea" and f.meta.get("form") else \
                    f.meta.get("form") or "capital adequacy table quote"
                out.append(f"| {r['market']} | {r['name']} | {filing_link(ds.companies[r['code']], acc)} | {form} | "
                           f"{f.meta.get('filed') or 'n/a'} |")
    out += ["", f"{NOTICE}. Check the cited filings before relying on any figure.", ""]
    md = "\n".join(out)
    bad = uncited_figures(md)
    if bad:
        raise CompareError(f"uncited figures in the comparison: {', '.join(bad[:5])}")
    return md
