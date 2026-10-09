"""Hand-written finance task specs, written out as matched shared/private pairs.

Both variants of a pair have the same agents, roles, data, objective, verifier, round budget and reference
solution. Only the location of the methodology sentences changes (the same design as paired.py):
  shared   every sentence is in the shared "Relevant Context"
  private  each sentence goes, once, into the role briefing of the agent that naturally owns it
           (methodology -> analyst, price holdings -> reporter); the collector must be told what to fetch.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

AGENTS = [
    {"username": "collector", "role": "data collector", "restricted_tools": ["calculate", "submit_answer"]},
    {"username": "analyst", "role": "analyst", "restricted_tools": ["fetch_financials", "fetch_shares", "submit_answer"]},
    {"username": "reporter", "role": "reporter", "restricted_tools": ["fetch_financials", "fetch_shares", "calculate"]},
]
TRAPS = ("Common traps: consolidated (CFS) vs separate (OFS) statements, net income vs net income attributable to owners, "
         "cumulative vs single-quarter figures, and reports filed after the as-of date.")
C = "999990"  # synthetic company in data/finance/sample


def _ids(*names: str) -> list[str]:
    return [f"{C}.{n}" for n in names]


def _inputs(**binds: str) -> list[dict]:
    return [{"var": v, "fact_id": f} for v, f in binds.items()]


SPECS = [
    {
        "pair": "f01", "slug": "ttm_per", "max_rounds": 20,
        "name": "Trailing PER of 샘플전자",
        "description": ("The desk needs the trailing twelve-month (TTM) PER of 샘플전자 (stock code 999990) as of 2025-10-01, "
                        "using only reports filed by that date. The collector pulls filings, the analyst computes, "
                        "the reporter submits."),
        "primary": ("Submit two answers: ttm_net_income (TTM net income attributable to owners of the parent, in KRW) and "
                    "per (market capitalization divided by ttm_net_income, as a multiple such as 12.3)."),
        "facts": {
            "analyst": [
                "TTM = latest fiscal-year (FY) figure - prior-year cumulative figure for the same period as the latest "
                "report + latest cumulative figure. For an H1 report: FY(prev year) - H1(prev year) + H1(this year).",
                "Net income means net_income_owners (attributable to owners of the parent) from consolidated (CFS) statements.",
                "Market capitalization = closing price x issued common shares from the latest report "
                "(do not subtract treasury shares; exclude preferred shares).",
                "The latest report filed by 2025-10-01 is the 2025 H1 report, so the inputs are 2024 FY CFS, 2024 H1 CFS, "
                "2025 H1 CFS and the 2025 H1 share counts.",
            ],
            "reporter": ["The reporter holds the closing price of 2025-09-30 from the market-data desk."],
        },
        "initial": {"reporter": [{"price_of": C, "date": "2025-09-30"}]},
        "checkpoints": [
            {"type": "answer", "name": "ttm_net_income", "field": "ttm_net_income", "expected": 50_000_000_000, "rel_tol": 0.005},
            {"type": "answer", "name": "per", "field": "per", "expected": 24.0, "rel_tol": 0.005},
        ],
        "reference": {
            "collector": [
                ["fetch_financials", {"company": C, "year": 2024, "period": "FY", "basis": "CFS"}],
                ["fetch_financials", {"company": C, "year": 2024, "period": "H1", "basis": "CFS"}],
                ["fetch_financials", {"company": C, "year": 2025, "period": "H1", "basis": "CFS"}],
                ["fetch_shares", {"company": C, "year": 2025, "period": "H1"}],
                ["share_facts", {"to_agent": "analyst", "fact_ids": _ids(
                    "2024FY.CFS.net_income_owners", "2024H1.CFS.net_income_owners", "2025H1.CFS.net_income_owners",
                    "2025H1.shares_common")}],
            ],
            "analyst": [
                ["wait", {"reason": "waiting for inputs"},
                 {"until_have": {f"{C}.2025H1.shares_common": 1, f"{C}.price.2025-09-30": 1}, "retries": 30}],
                ["calculate", {"name": "ttm_net_income", "formula": "fy - h1_prev + h1", "unit": "KRW", "inputs": _inputs(
                    fy=f"{C}.2024FY.CFS.net_income_owners", h1_prev=f"{C}.2024H1.CFS.net_income_owners",
                    h1=f"{C}.2025H1.CFS.net_income_owners")}],
                ["calculate", {"name": "market_cap", "formula": "p * s", "unit": "KRW", "inputs": _inputs(
                    p=f"{C}.price.2025-09-30", s=f"{C}.2025H1.shares_common")}],
                ["calculate", {"name": "per", "formula": "m / n", "unit": "x", "inputs": _inputs(
                    m="calc.market_cap", n="calc.ttm_net_income")}],
                ["share_facts", {"to_agent": "reporter", "fact_ids": ["calc.ttm_net_income", "calc.per"]}],
            ],
            "reporter": [
                ["share_facts", {"to_agent": "analyst", "fact_ids": [f"{C}.price.2025-09-30"]}],
                ["wait", {"reason": "waiting for results"},
                 {"until_have": {"calc.per": 1, "calc.ttm_net_income": 1}, "retries": 30}],
                ["submit_answer", {"answers": [{"field": "ttm_net_income", "fact_id": "calc.ttm_net_income"},
                                               {"field": "per", "fact_id": "calc.per"}]}],
            ],
        },
    },
    {
        "pair": "f02", "slug": "q2_margin", "max_rounds": 16,
        "name": "Second-quarter operating margin of 샘플전자",
        "description": ("The desk needs the standalone second-quarter 2025 revenue and operating margin of 샘플전자 "
                        "(stock code 999990) as of 2025-10-01. The collector pulls filings, the analyst computes, "
                        "the reporter submits."),
        "primary": ("Submit two answers: q2_revenue (revenue of April-June 2025 only, in KRW) and q2_operating_margin "
                    "(operating income / revenue of that quarter, in percent, e.g. 7.5 for 7.5%)."),
        "facts": {
            "analyst": [
                "Report figures are cumulative from January, so a standalone Q2 figure = H1 cumulative - Q1 cumulative.",
                "Operating margin (%) = operating income / revenue x 100, both from consolidated (CFS) statements.",
                "The inputs are the 2025 Q1 CFS and 2025 H1 CFS reports.",
            ],
        },
        "initial": {},
        "checkpoints": [
            {"type": "answer", "name": "q2_revenue", "field": "q2_revenue", "expected": 230_000_000_000, "rel_tol": 0.005},
            {"type": "answer", "name": "q2_operating_margin", "field": "q2_operating_margin",
             "expected": round(19 / 230 * 100, 6), "rel_tol": 0.005},
        ],
        "reference": {
            "collector": [
                ["fetch_financials", {"company": C, "year": 2025, "period": "Q1", "basis": "CFS"}],
                ["fetch_financials", {"company": C, "year": 2025, "period": "H1", "basis": "CFS"}],
                ["share_facts", {"to_agent": "analyst", "fact_ids": _ids(
                    "2025Q1.CFS.revenue", "2025H1.CFS.revenue", "2025Q1.CFS.operating_income", "2025H1.CFS.operating_income")}],
            ],
            "analyst": [
                ["wait", {"reason": "waiting for inputs"},
                 {"until_have": {f"{C}.2025H1.CFS.operating_income": 1}, "retries": 30}],
                ["calculate", {"name": "q2_revenue", "formula": "h1 - q1", "unit": "KRW", "inputs": _inputs(
                    h1=f"{C}.2025H1.CFS.revenue", q1=f"{C}.2025Q1.CFS.revenue")}],
                ["calculate", {"name": "q2_operating_income", "formula": "h1 - q1", "unit": "KRW", "inputs": _inputs(
                    h1=f"{C}.2025H1.CFS.operating_income", q1=f"{C}.2025Q1.CFS.operating_income")}],
                ["calculate", {"name": "q2_operating_margin", "formula": "o / r * 100", "unit": "%", "inputs": _inputs(
                    o="calc.q2_operating_income", r="calc.q2_revenue")}],
                ["share_facts", {"to_agent": "reporter", "fact_ids": ["calc.q2_revenue", "calc.q2_operating_margin"]}],
            ],
            "reporter": [
                ["wait", {"reason": "waiting for results"},
                 {"until_have": {"calc.q2_operating_margin": 1, "calc.q2_revenue": 1}, "retries": 30}],
                ["submit_answer", {"answers": [{"field": "q2_revenue", "fact_id": "calc.q2_revenue"},
                                               {"field": "q2_operating_margin", "fact_id": "calc.q2_operating_margin"}]}],
            ],
        },
    },
]


def build_task(spec: dict, placement: str) -> dict:
    if placement not in ("shared", "private"):
        raise ValueError(f"unknown placement {placement!r}")
    agents = copy.deepcopy(AGENTS)
    for a in agents:
        if spec["initial"].get(a["username"]):
            a["initial_facts"] = spec["initial"][a["username"]]
        if placement == "private" and spec["facts"].get(a["username"]):
            a["role_brief"] = " ".join(spec["facts"][a["username"]])
    shared = [TRAPS]
    if placement == "shared":
        for owner in ("collector", "analyst", "reporter"):
            shared += spec["facts"].get(owner, [])
    return {
        "id": f"{spec['pair']}_{spec['slug']}_{placement}",
        "name": spec["name"], "category": "finance", "domain": "finance", "world_guide": False,
        "description": spec["description"],
        "objectives": {"primary": spec["primary"], "secondary": ["Use as few rounds as possible."]},
        "max_rounds": spec["max_rounds"],
        "inputs": {"as_of": "2025-10-01", "company": "샘플전자"},
        "data": {"dir": "data/finance/sample"},
        "matched": {"pair_id": spec["pair"], "placement": placement,
                    "fact_counts": {k: len(v) for k, v in spec["facts"].items()}},
        "relevant_game_context": "\n".join(f"- {s}" for s in shared),
        "agents": agents,
        "verifier": {"checkpoints": spec["checkpoints"]},
        "reference_solution": spec["reference"],
    }


def write_tasks(out_dir: str | Path = "tasks/finance") -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in SPECS:
        for placement in ("shared", "private"):
            td = build_task(spec, placement)
            p = out / f"{td['id']}.yaml"
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                yaml.safe_dump(td, f, allow_unicode=True, sort_keys=False, width=120)
            paths.append(p)
    return paths
