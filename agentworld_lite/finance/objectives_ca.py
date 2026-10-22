"""Canada's Big 5 banks: the financial objectives each set out in its fiscal 2025 annual filing (Form 40-F on SEC
EDGAR), quoted verbatim, the progress the filings show, and a side-by-side with the Korean groups' value-up targets.
No language model.

    python -m agentworld_lite.finance objectives-ca --out reports/objectives_ca.md
    python -m agentworld_lite.finance objectives-ca --verify-local <folder with the five saved exhibits>

Quotes were checked on 2026-10-22 against the five MD&A exhibits as served by sec.gov: the SHA-256 of the served bytes
is in DOCS, and every quote was found in the page's rendered text with whitespace collapsed (read in a browser; this
machine's scripted EDGAR requests were being refused with HTTP 403). verify_local() repeats the check on saved copies:
same SHA-256 (or says it differs), and every quote present in the text once tags, entities and all whitespace are
dropped.

Progress, from filings only:
  ROE         the bank's own fiscal 2025 figure, quoted (reported, and adjusted where the bank gives it). The desk's
              XBRL data has no common equity, so ROE is not recomputed; banks define ROE on average common equity.
  EPS growth  diluted EPS, nine months of fiscal 2026 against nine months of fiscal 2025, from the Q3 XBRL (reported
              basis; most objectives are on an adjusted basis, which filings' XBRL does not carry).
  CET1        the ratio at 2026-07-31 from the Q3 XBRL.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from . import valueup as vu
from .dataset import Dataset, Fact, statement_facts
from .env import REPO_ROOT
from .note import cited, filing_link, uncited_figures

DATA_DIR = REPO_ROOT / "data" / "finance" / "edgar"
BANKS = ("RY", "TD", "BNS", "BMO", "CM")
NAMES = {"RY": "Royal Bank of Canada", "TD": "TD Bank", "BNS": "Scotiabank", "BMO": "BMO", "CM": "CIBC"}
KR_NAMES = {"105560": "KB Financial", "055550": "Shinhan Financial", "086790": "Hana Financial"}
VERIFIED_ON = "2026-10-22"
NOTICE = "Educational example, not investment advice"
QUOTE = r"“[^”]*” \[quote from [^\]]*\]"  # a quoted passage and its source, for uncited_figures()

# The fiscal 2025 annual filing (Form 40-F) exhibit that holds each bank's MD&A, and what sec.gov served on VERIFIED_ON.
DOCS = {
    "RY": {"cik": 1000275, "accession": "0001193125-25-305927", "document": "d95203dex2.htm", "exhibit": "EX-2",
           "bytes": 17246847, "sha256": "feaba06ae7d01a8b7003db83fb107b0cdc2fcee3683558a7c962f20a10a5d7ca"},
    "TD": {"cik": 947263, "accession": "0001562762-25-000289", "document": "ex992.htm", "exhibit": "EX-99.2",
           "bytes": 3988819, "sha256": "b1c4acdbc4c03f1a7306154d991bc73c7780042a9e6f45620465eb154afd7d58"},
    "BNS": {"cik": 9631, "accession": "0001193125-25-304643", "document": "d936350dex992.htm", "exhibit": "EX-99.2",
            "bytes": 8321882, "sha256": "867df78fd1c00a01a9c92b22bd3c2cd41757d82486eff0d2f345595c9df6103f"},
    "BMO": {"cik": 927971, "accession": "0001193125-25-307982", "document": "d938207dex992.htm", "exhibit": "EX-99.2",
            "bytes": 7122204, "sha256": "e64063df75f8cb0935bde27d1ee5b2eb18472baff0187efa4534f1434c0e2fed"},
    "CM": {"cik": 1045520, "accession": "0001193125-25-307488", "document": "d91021dex99b3c.htm", "exhibit": "EX-99.B3(C)",
           "bytes": 6430293, "sha256": "19b8c95fb10199e9d28cbde4cc5acdbffcc225ca41d3e0ccda27b55b7a739af2"},
}


@dataclass(frozen=True)
class Objective:
    bank: str
    metric: str             # roe, eps_growth, cet1, payout, operating_leverage, rotce, tsr
    low: float | None       # percent; None for a qualitative objective
    high: float | None      # upper end of a range; None for "low or more"
    basis: str              # adjusted, or "" when the quoted wording does not say
    horizon: str
    quote: str              # verbatim, as found in the document text


METRIC = {"roe": "ROE", "eps_growth": "EPS growth", "cet1": "CET1 ratio", "payout": "Dividend payout ratio",
          "operating_leverage": "Operating leverage", "rotce": "Return on tangible common equity",
          "tsr": "Total shareholder return"}
RY_H, TD_H, TD_26 = "medium term (3-5 years)", "medium term (fiscal 2029)", "fiscal 2026"
BNS_H, BMO_H, CM_H = "medium term", "medium term (3-5 years)", "through the cycle (3-5 years)"
OBJECTIVES = [
    Objective("RY", "eps_growth", 7, None, "", RY_H, "Diluted EPS growth of 7% +"),
    Objective("RY", "roe", 16, None, "", RY_H, "ROE of 16% +"),
    Objective("RY", "cet1", None, None, "", RY_H, "Strong capital ratio (CET1)"),
    Objective("RY", "payout", 40, 50, "", RY_H, "Dividend payout ratio 40% – 50%"),
    Objective("TD", "roe", 16, 16, "adjusted", TD_H, "~16% Adj."),
    Objective("TD", "eps_growth", 7, 10, "adjusted", TD_H, "7-10% Adj."),
    Objective("TD", "cet1", None, None, "", TD_H, "Strong CET1 Ratio"),
    Objective("TD", "payout", 40, 50, "", TD_H, "40-50% Dividend Payout Ratio"),
    Objective("TD", "roe", 13, 13, "adjusted", TD_26, "~13% Adj. ROE"),
    Objective("TD", "eps_growth", 6, 8, "adjusted", TD_26, "6-8% Y/Y Adj."),
    Objective("TD", "cet1", 13, None, "", TD_26, "13%+ CET1 Ratio"),
    Objective("BNS", "eps_growth", 7, None, "", BNS_H, "Diluted earnings per share growth of 7%+"),
    Objective("BNS", "roe", 14, None, "", BNS_H, "Return on equity of 14%+"),
    Objective("BNS", "operating_leverage", 0, None, "", BNS_H, "Achieve positive operating leverage"),
    Objective("BNS", "cet1", None, None, "", BNS_H, "Maintain strong capital ratios"),
    Objective("BMO", "eps_growth", 7, 10, "adjusted", BMO_H, "Earnings per share growth (%) 7-10%"),
    Objective("BMO", "roe", 15, None, "adjusted", BMO_H, "Return on equity (%) 15% or more"),
    Objective("BMO", "rotce", 18, None, "adjusted", BMO_H, "Return on tangible common equity (%) 18% or more"),
    Objective("BMO", "operating_leverage", 2, None, "adjusted", BMO_H, "Operating leverage (%) (2) 2% or more"),
    Objective("BMO", "cet1", None, None, "", BMO_H, "Common Equity Tier 1 Ratio (%) Exceed regulatory requirement"),
    Objective("BMO", "tsr", None, None, "", BMO_H, "Total shareholder return (%) Top-tier"),
    Objective("CM", "eps_growth", 7, 10, "adjusted", CM_H,
              "Going forward, we will continue to target an adjusted(1) diluted EPS growth rate of 7% to 10% through "
              "the cycle."),
    Objective("CM", "roe", 15, None, "adjusted", CM_H,
              "Going forward, we will continue to target an adjusted(1) ROE of 15%+ through the cycle."),
    Objective("CM", "payout", 40, 50, "adjusted", CM_H,
              "Going forward, we will continue to target an adjusted(1) dividend payout ratio of 40% to 50% through "
              "the cycle."),
    Objective("CM", "operating_leverage", 0, None, "adjusted", CM_H,
              "Going forward, we will continue to target positive adjusted(1) operating leverage through the cycle."),
]
# How each bank frames the horizon, and what it reported for fiscal 2025 (the ROE on the basis its objective uses).
CONTEXT = {
    "RY": "A medium-term (3-5 year) objective is considered to be achieved when the performance goal is met in either a "
          "3- or 5-year period.",
    "TD": "Medium-Term (Fiscal Year 2029) Targets",
    "BNS": "The following table provides a summary of our 2025 performance against our medium-term financial objectives:",
    "BMO": "BMO has established medium-term financial objectives for certain key performance measures, which are set out "
           "below. Medium-term is generally defined as three to five years, and performance is assessed on an adjusted "
           "basis.",
    "CM": "We have set through the cycle targets for each of these measures, which we currently define as three to five "
          "years, assuming a normal business environment and credit cycle.",
}
ROE_FY2025 = {  # bank: (reported, adjusted or None, quote)
    "RY": (16.3, None, "Diluted EPS of $14.07 was up $2.82 or 25% and ROE of 16.3% was up 190 bps."),
    "TD": (17.8, 12.9, "Return on common equity – reported 17.8 % 8.2 % Return on common equity – adjusted 12.9 13.6"),
    "BNS": (9.7, 11.8, "Return on equity of 14%+ 9.7 % 11.8%"),
    "BMO": (10.6, 11.3, "Return on equity (%) 15% or more 10.6 8.8 12.8 11.3 11.2 13.1"),
    "CM": (14.3, 14.4, "In 2025, our reported and adjusted(1) ROE were at 14.3% and 14.4%, respectively"),
}


class ObjectivesError(Exception):
    pass


def quotes(bank: str) -> list[str]:
    return [*(o.quote for o in OBJECTIVES if o.bank == bank), CONTEXT[bank], ROE_FY2025[bank][2]]


# ---------------------------------------------------------------------- checking saved copies
def squash(text: str) -> str:
    """Comparable text: tags dropped, entities decoded, all whitespace removed (layouts split cells differently)."""
    return re.sub(r"\s+", "", unescape(re.sub(r"<[^>]+>", " ", text)))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_local(folder: str | Path) -> dict[str, dict]:
    """For each bank, a saved copy of its exhibit in `folder`: whether its SHA-256 matches what sec.gov served, and which
    quotes are missing from its text. A copy is found by its size and hash (browsers may rename a save), else by a file
    name ending with the document name."""
    out = {}
    files = [f for f in Path(folder).iterdir() if f.is_file()]
    for bank, d in DOCS.items():
        same = (f for f in files if f.stat().st_size == d["bytes"] and _sha256(f) == d["sha256"])
        named = (f for f in files if f.name.endswith(d["document"]) or f.name.endswith(d["document"] + "l"))
        hit = next(same, None) or next(named, None)
        if hit is None:
            out[bank] = {"file": None}
            continue
        raw = hit.read_bytes()
        text = squash(raw.decode("utf-8", "replace"))
        out[bank] = {"file": hit.name, "same_sha256": hashlib.sha256(raw).hexdigest() == d["sha256"],
                     "missing": [q for q in quotes(bank) if squash(q) not in text]}
    return out


# ---------------------------------------------------------------------- progress from the filings
def _doc_fact(bank: str, fid: str, label: str, value: float) -> Fact:
    d = DOCS[bank]
    return Fact(f"{bank}.{fid}", label, float(value), "%", {"source": "EDGAR", "rcept_no": d["accession"], "form": "40-F",
                                                            "company": bank})


def _cite_quote(bank: str, quote: str) -> str:
    d = DOCS[bank]
    return f"“{quote}” [quote from 40-F {d['accession']} {d['exhibit']} `{d['document']}`]"


def progress(bank: str, data_dir: str | Path = DATA_DIR) -> dict:
    c = Dataset(Path(data_dir)).companies[bank]
    q26 = {f.meta["account"]: f for f in statement_facts(c, 2026, "Q3", "CFS")}
    q25 = {f.meta["account"]: f for f in statement_facts(c, 2025, "Q3", "CFS")}
    eps = None
    if "eps_diluted" in q26 and "eps_diluted" in q25 and q25["eps_diluted"].value > 0:
        e1, e0 = q26["eps_diluted"], q25["eps_diluted"]
        eps = ((e1.value / e0.value - 1) * 100, [e1, e0])
    rep, adj, _ = ROE_FY2025[bank]
    return {"cet1": q26.get("cet1_ratio"), "eps_growth_ytd": eps,
            "roe_reported": _doc_fact(bank, "fy2025.roe_reported", f"{NAMES[bank]} fiscal 2025 ROE, reported", rep),
            "roe_adjusted": adj is not None and _doc_fact(bank, "fy2025.roe_adjusted",
                                                          f"{NAMES[bank]} fiscal 2025 ROE, adjusted", adj) or None}


def verdict(o: Objective, value: float | None) -> str:
    if o.low is None or value is None:
        return "no figure to test"
    approx = o.high == o.low                                     # "~16%": within half a point counts
    if approx:
        return "at target" if abs(value - o.low) <= 0.5 else ("above" if value > o.low else "below")
    if value < o.low:
        return "below"
    return "above range" if o.high is not None and value > o.high else "meets"


# ---------------------------------------------------------------------- the scorecard (English Markdown)
def _target(o: Objective) -> str:
    """The objective's figures, each cited to the 40-F it is quoted from."""
    if o.low is None:
        return "qualitative"
    if o.metric == "operating_leverage" and not o.low:
        return "positive"
    key = f"objective.{o.metric}.{'fy2026' if o.horizon == TD_26 else 'medium_term'}"
    lo = cited(o.low, "%", [_doc_fact(o.bank, key + ".low", f"{NAMES[o.bank]} {METRIC[o.metric]} objective", o.low)])
    if o.high is None:
        return f"{lo} or more"
    if o.high == o.low:
        return f"about {lo}"
    return f"{lo} to " + cited(o.high, "%", [_doc_fact(o.bank, key + ".high", f"{NAMES[o.bank]} {METRIC[o.metric]} "
                                                                                  "objective, upper end", o.high)])


def _spec(o: Objective) -> str:
    return " ".join(x for x in (_target(o), o.basis, f"({o.horizon})") if x)


def render(data_dir: str | Path = DATA_DIR) -> str:
    out = ["# Canada's Big 5: financial objectives vs progress, from filings", "",
           f"> **{NOTICE}.** Written by fixed rules from the banks' filings on SEC EDGAR; no language model wrote it. "
           "Objectives are quoted verbatim from each bank's fiscal 2025 annual filing (Form 40-F, MD&A exhibit); every "
           "figure carries its source.", "",
           f"- **Quotes checked on {VERIFIED_ON}** against the five MD&A exhibits as served by sec.gov (SHA-256 of each "
           "document in the Sources table); every quote was found in the document text.",
           "- **Progress:** ROE is each bank's own fiscal 2025 figure (banks compute it on average common equity, which "
           "the desk's data does not hold). EPS growth compares diluted EPS for the first nine months of fiscal 2026 "
           "with the same months of fiscal 2025 (reported basis; most objectives are on an adjusted basis). CET1 is the "
           "ratio at 2026-07-31.", ""]
    out += ["## Summary", "", "| Bank | ROE objective | Fiscal 2025 ROE | ROE vs objective | EPS growth objective | "
            "EPS growth, 9 months of fiscal 2026 | CET1 at 2026-07-31 |", "|---|---|---|---|---|---|---|"]
    for bank in BANKS:
        p = progress(bank, data_dir)
        roe = next(o for o in OBJECTIVES if o.bank == bank and o.metric == "roe")
        eps_o = next(o for o in OBJECTIVES if o.bank == bank and o.metric == "eps_growth")
        tested = p["roe_adjusted"] if roe.basis == "adjusted" and p["roe_adjusted"] else p["roe_reported"]
        roe_cell = cited(p["roe_reported"].value, "%", [p["roe_reported"]]) + " reported"
        if p["roe_adjusted"]:
            roe_cell += f", {cited(p['roe_adjusted'].value, '%', [p['roe_adjusted']])} adjusted"
        eps = cited(p["eps_growth_ytd"][0], "%", p["eps_growth_ytd"][1]) + " reported" if p["eps_growth_ytd"] else "n/a"
        cet1 = cited(p["cet1"].value, "%", [p["cet1"]]) if p["cet1"] else "n/a"
        out.append(f"| {NAMES[bank]} | {_spec(roe)} | {roe_cell} | "
                   f"**{verdict(roe, tested.value)}** ({'adjusted' if tested is p['roe_adjusted'] else 'reported'}) | "
                   f"{_spec(eps_o)} | {eps} | {cet1} |")
    out += ["", "EPS growth is shown without a verdict: it is on a reported basis, which includes items of note that the "
            "adjusted objectives exclude, so a single year's gain or charge can swing it far in either direction."]
    out += ["", "## Objectives as written", ""]
    for bank in BANKS:
        out += [f"### {NAMES[bank]}", "", f"- Horizon: {_cite_quote(bank, CONTEXT[bank])}"]
        out += [f"- {METRIC[o.metric]} ({o.horizon}): {_cite_quote(bank, o.quote)}" for o in OBJECTIVES if o.bank == bank]
        out += [f"- Fiscal 2025 ROE: {_cite_quote(bank, ROE_FY2025[bank][2])}", ""]
    out += ["## Korea and Canada side by side", "",
            "Targets as the groups state them. Korea's groups quote ROE on average owners' equity (hybrid capital "
            "included) in their value-up plans; Canada's banks on average common equity, mostly adjusted. The levels "
            "are comparable as ambitions, not as like-for-like ratios.", "",
            "| Market | Group | ROE target | CET1 target | Payout target |", "|---|---|---|---|---|"]
    for t_code in ("105560", "055550", "086790"):
        ts = {t.metric: t for t in vu.TARGETS if t.code == t_code}
        cell = lambda m: (cited(ts[m].threshold, "%", [vu.target_fact(ts[m])])
                          + (" or more" if "이상" in ts[m].quote or "+" in ts[m].quote else "")  # as the plan words it
                          + (f" by {ts[m].deadline[:4]}" if ts[m].deadline else "")) if m in ts else "-"
        out.append(f"| Korea | {KR_NAMES[t_code]} | {cell('roe')} | {cell('cet1')} | {cell('payout')} |")
    for bank in BANKS:
        by = {}
        for o in OBJECTIVES:
            if o.bank == bank and o.horizon != TD_26:
                by.setdefault(o.metric, o)
        cell = lambda m: (_target(by[m]) + (f" ({by[m].basis})" if by[m].basis else "")) if m in by else "-"
        out.append(f"| Canada | {NAMES[bank]} | {cell('roe')} | {cell('cet1')} | {cell('payout')} |")
    out += ["", "Korean targets are quoted, with their DART receipt numbers, in the Korean value-up scorecard "
            "(`python -m agentworld_lite.finance valueup`); Canadian targets in the quotes above.", "",
            "## Sources", "", "| Bank | Form 40-F | Document | Bytes | SHA-256 |", "|---|---|---|---|---|"]
    ds = Dataset(Path(data_dir))
    for bank in BANKS:
        d = DOCS[bank]
        out.append(f"| {NAMES[bank]} | {filing_link(ds.companies[bank], d['accession'])} | {d['exhibit']} "
                   f"`{d['document']}` | `{d['bytes']}` | `{d['sha256']}` |")
    out += ["", "No rating, no target price, no share price. Check the cited filings before relying on anything here.", ""]
    md = "\n".join(out)
    bad = uncited_figures(md, extra=(QUOTE,))
    if bad:
        raise ObjectivesError(f"uncited figures in the objectives scorecard: {', '.join(bad[:5])}")
    return md


def objectives_lines(bank: str) -> list[str]:
    """The quoted objectives of one bank, for its earnings review."""
    lines = [f"- Horizon: {_cite_quote(bank, CONTEXT[bank])}"]
    lines += [f"- {METRIC[o.metric]} ({o.horizon}): {_cite_quote(bank, o.quote)}" for o in OBJECTIVES if o.bank == bank]
    return lines
