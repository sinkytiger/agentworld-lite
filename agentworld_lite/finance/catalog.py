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


# ---------------------------------------------------------------------- Canadian desk (EDGAR filings of the Big 5)
# Real figures from data/finance/edgar (XBRL of each bank's 40-F and 6-K filings). The inputs below were checked
# against the banks' own Q3 2026 releases: RBC total revenue 18,538, non-interest expense 9,789 and diluted EPS
# 4.23 / 12.10 (9M) / 10.31 (9M 2025); TD total revenue 16,885 and reported efficiency ratio 50.2% (Q3), 52.0% (9M).
AGENTS_CA = [
    {"username": "collector", "role": "data collector", "restricted_tools": ["calculate", "submit_answer"]},
    {"username": "analyst", "role": "analyst", "restricted_tools": ["fetch_financials", "submit_answer"]},
    {"username": "reporter", "role": "reporter", "restricted_tools": ["fetch_financials", "calculate"]},
]
TRAPS_CA = ("Common traps: fiscal vs calendar periods (the banks' fiscal year ends October 31), year-to-date vs "
            "single-quarter figures, net income attributable to shareholders vs net income available to common "
            "shareholders, basic vs diluted EPS, ratios as fractions vs percent, and filings made after the as-of date.")


def _ca_ids(code: str, *names: str) -> list[str]:
    return [f"{code}.{n}" for n in names]


RY_PRICE = 293.41  # TSX close on 2026-07-31, as reported in RBC's Q3 2026 Report to Shareholders
BIG5 = ("RY", "TD", "BNS", "BMO", "CM")
SPECS_CA = [
    {
        "pair": "c01", "slug": "ry_ttm_pe", "max_rounds": 16, "market": "ca",
        "name": "Trailing P/E of Royal Bank of Canada",
        "description": ("The desk needs the trailing twelve-month (TTM) P/E of Royal Bank of Canada (TSX: RY) at the end "
                        "of its fiscal 2026 third quarter (July 31, 2026), using only filings made by 2026-09-30. The "
                        "collector pulls filings, the analyst computes, the reporter submits."),
        "primary": ("Submit two answers: ttm_net_income_common (TTM net income available to common shareholders, in CAD) "
                    "and pe (the closing share price on 2026-07-31 divided by TTM diluted EPS, as a multiple such as 12.3)."),
        "facts": {
            "analyst": [
                "TTM = latest fiscal-year (FY) figure - prior-year year-to-date figure for the same period as the latest "
                "report + latest year-to-date figure. For a fiscal Q3 report: FY(fiscal 2025) - Q3(fiscal 2025) + "
                "Q3(fiscal 2026).",
                "Net income means net_income_common (available to common shareholders, after preferred dividends and "
                "distributions on other equity instruments) from consolidated (CFS) statements.",
                "P/E = closing share price / TTM diluted EPS, where TTM diluted EPS applies the same TTM formula to eps_diluted.",
                "RBC's fiscal year ends October 31. The latest filing made by 2026-09-30 is the fiscal 2026 Q3 report "
                "(period ended July 31, 2026), so the inputs are the fiscal 2025 FY, fiscal 2025 Q3 and fiscal 2026 Q3 "
                "CFS reports.",
            ],
            "reporter": ["The reporter holds RBC's TSX closing price of 2026-07-31 from the market-data desk."],
        },
        "initial": {"reporter": [{"price_of": "RY", "date": "2026-07-31"}]},
        "checkpoints": [
            {"type": "answer", "name": "ttm_net_income_common", "field": "ttm_net_income_common",
             "expected": 19_868_000_000 - 14_575_000_000 + 16_894_000_000, "rel_tol": 0.001},
            {"type": "answer", "name": "pe", "field": "pe", "expected": round(RY_PRICE / (14.07 - 10.31 + 12.10), 6),
             "rel_tol": 0.001},
        ],
        "reference": {
            "collector": [
                ["fetch_financials", {"company": "RY", "year": 2025, "period": "FY", "basis": "CFS"}],
                ["fetch_financials", {"company": "RY", "year": 2025, "period": "Q3", "basis": "CFS"}],
                ["fetch_financials", {"company": "RY", "year": 2026, "period": "Q3", "basis": "CFS"}],
                ["share_facts", {"to_agent": "analyst", "fact_ids": _ca_ids(
                    "RY", "2025FY.CFS.net_income_common", "2025Q3.CFS.net_income_common", "2026Q3.CFS.net_income_common",
                    "2025FY.CFS.eps_diluted", "2025Q3.CFS.eps_diluted", "2026Q3.CFS.eps_diluted")}],
            ],
            "analyst": [
                ["wait", {"reason": "waiting for inputs"},
                 {"until_have": {"RY.2026Q3.CFS.eps_diluted": 1, "RY.price.2026-07-31": 1}, "retries": 30}],
                ["calculate", {"name": "ttm_net_income_common", "formula": "fy - ytd_prev + ytd", "unit": "CAD",
                               "inputs": _inputs(fy="RY.2025FY.CFS.net_income_common",
                                                 ytd_prev="RY.2025Q3.CFS.net_income_common",
                                                 ytd="RY.2026Q3.CFS.net_income_common")}],
                ["calculate", {"name": "ttm_eps_diluted", "formula": "fy - ytd_prev + ytd", "unit": "CAD/share",
                               "inputs": _inputs(fy="RY.2025FY.CFS.eps_diluted", ytd_prev="RY.2025Q3.CFS.eps_diluted",
                                                 ytd="RY.2026Q3.CFS.eps_diluted")}],
                ["calculate", {"name": "pe", "formula": "p / e", "unit": "x",
                               "inputs": _inputs(p="RY.price.2026-07-31", e="calc.ttm_eps_diluted")}],
                ["share_facts", {"to_agent": "reporter", "fact_ids": ["calc.ttm_net_income_common", "calc.pe"]}],
            ],
            "reporter": [
                ["share_facts", {"to_agent": "analyst", "fact_ids": ["RY.price.2026-07-31"]}],
                ["wait", {"reason": "waiting for results"},
                 {"until_have": {"calc.pe": 1, "calc.ttm_net_income_common": 1}, "retries": 30}],
                ["submit_answer", {"answers": [{"field": "ttm_net_income_common", "fact_id": "calc.ttm_net_income_common"},
                                               {"field": "pe", "fact_id": "calc.pe"}]}],
            ],
        },
    },
    {
        "pair": "c02", "slug": "q3_efficiency", "max_rounds": 18, "market": "ca",
        "company": "Royal Bank of Canada; The Toronto-Dominion Bank",
        "name": "Fiscal Q3 2026 efficiency ratios of RBC and TD",
        "description": ("The desk compares the efficiency ratios of Royal Bank of Canada (TSX: RY) and The Toronto-Dominion "
                        "Bank (TSX: TD) for the third quarter of fiscal 2026 alone (May-July 2026), using only filings made "
                        "by 2026-09-30. The collector pulls filings, the analyst computes, the reporter submits."),
        "primary": ("Submit two answers: ry_q3_efficiency_ratio and td_q3_efficiency_ratio (non-interest expense divided "
                    "by total revenue for that single quarter, in percent, e.g. 55.0 for 55.0%)."),
        "facts": {
            "analyst": [
                "Filing figures are fiscal year-to-date, so a single fiscal Q3 figure = Q3 year-to-date (9 months) - "
                "H1 year-to-date (6 months).",
                "Efficiency ratio (%) = non_interest_expense / revenue x 100, both for the same single quarter from "
                "consolidated (CFS) statements.",
                "Both banks' fiscal years end October 31, so the inputs are each bank's fiscal 2026 H1 and fiscal 2026 Q3 "
                "CFS reports.",
            ],
        },
        "initial": {},
        "checkpoints": [
            {"type": "answer", "name": "ry_q3_efficiency_ratio", "field": "ry_q3_efficiency_ratio",
             "expected": round((28_689 - 18_900) / (53_951 - 35_413) * 100, 6), "rel_tol": 0.001},
            {"type": "answer", "name": "td_q3_efficiency_ratio", "field": "td_q3_efficiency_ratio",
             "expected": round((25_600 - 17_125) / (49_267 - 32_382) * 100, 6), "rel_tol": 0.001},
        ],
        "reference": {
            "collector": [
                ["fetch_financials", {"company": "RY", "year": 2026, "period": "H1", "basis": "CFS"}],
                ["fetch_financials", {"company": "RY", "year": 2026, "period": "Q3", "basis": "CFS"}],
                ["fetch_financials", {"company": "TD", "year": 2026, "period": "H1", "basis": "CFS"}],
                ["fetch_financials", {"company": "TD", "year": 2026, "period": "Q3", "basis": "CFS"}],
                ["share_facts", {"to_agent": "analyst", "fact_ids": [
                    *_ca_ids("RY", "2026H1.CFS.revenue", "2026Q3.CFS.revenue",
                             "2026H1.CFS.non_interest_expense", "2026Q3.CFS.non_interest_expense"),
                    *_ca_ids("TD", "2026H1.CFS.revenue", "2026Q3.CFS.revenue",
                             "2026H1.CFS.non_interest_expense", "2026Q3.CFS.non_interest_expense")]}],
            ],
            "analyst": [
                ["wait", {"reason": "waiting for inputs"},
                 {"until_have": {"TD.2026Q3.CFS.non_interest_expense": 1}, "retries": 30}],
                ["calculate", {"name": "ry_q3_revenue", "formula": "q3 - h1", "unit": "CAD",
                               "inputs": _inputs(q3="RY.2026Q3.CFS.revenue", h1="RY.2026H1.CFS.revenue")}],
                ["calculate", {"name": "ry_q3_non_interest_expense", "formula": "q3 - h1", "unit": "CAD",
                               "inputs": _inputs(q3="RY.2026Q3.CFS.non_interest_expense",
                                                 h1="RY.2026H1.CFS.non_interest_expense")}],
                ["calculate", {"name": "ry_q3_efficiency_ratio", "formula": "e / r * 100", "unit": "%",
                               "inputs": _inputs(e="calc.ry_q3_non_interest_expense", r="calc.ry_q3_revenue")}],
                ["calculate", {"name": "td_q3_revenue", "formula": "q3 - h1", "unit": "CAD",
                               "inputs": _inputs(q3="TD.2026Q3.CFS.revenue", h1="TD.2026H1.CFS.revenue")}],
                ["calculate", {"name": "td_q3_non_interest_expense", "formula": "q3 - h1", "unit": "CAD",
                               "inputs": _inputs(q3="TD.2026Q3.CFS.non_interest_expense",
                                                 h1="TD.2026H1.CFS.non_interest_expense")}],
                ["calculate", {"name": "td_q3_efficiency_ratio", "formula": "e / r * 100", "unit": "%",
                               "inputs": _inputs(e="calc.td_q3_non_interest_expense", r="calc.td_q3_revenue")}],
                ["share_facts", {"to_agent": "reporter",
                                 "fact_ids": ["calc.ry_q3_efficiency_ratio", "calc.td_q3_efficiency_ratio"]}],
            ],
            "reporter": [
                ["wait", {"reason": "waiting for results"},
                 {"until_have": {"calc.ry_q3_efficiency_ratio": 1, "calc.td_q3_efficiency_ratio": 1}, "retries": 30}],
                ["submit_answer", {"answers": [
                    {"field": "ry_q3_efficiency_ratio", "fact_id": "calc.ry_q3_efficiency_ratio"},
                    {"field": "td_q3_efficiency_ratio", "fact_id": "calc.td_q3_efficiency_ratio"}]}],
            ],
        },
    },
    {
        # Single-quarter net income available to common (Q3 YTD - H1 YTD, C$ millions) and CET1 ratios at July 31,
        # 2026. The spread alone would not catch H1 ratios (TD 14.3 and BMO 13.0 in both), so the average is asked too.
        "pair": "c03", "slug": "big5_q3_sector", "max_rounds": 30, "market": "ca",
        "company": ("Royal Bank of Canada; The Toronto-Dominion Bank; The Bank of Nova Scotia; Bank of Montreal; "
                    "Canadian Imperial Bank of Commerce"),
        "name": "Big 5 Canadian banks: fiscal Q3 2026 earnings and capital",
        "description": ("The desk compares Canada's five largest banks, Royal Bank of Canada (TSX: RY), The Toronto-Dominion "
                        "Bank (TSX: TD), The Bank of Nova Scotia (TSX: BNS), Bank of Montreal (TSX: BMO) and Canadian "
                        "Imperial Bank of Commerce (TSX: CM), on earnings for the third quarter of fiscal 2026 alone "
                        "(May-July 2026) and on capital at the end of that quarter, using only filings made by 2026-09-30. "
                        "The collector pulls filings, the analyst computes, the reporter submits."),
        "primary": ("Submit seven answers: ry_q3_net_income_common, td_q3_net_income_common, bns_q3_net_income_common, "
                    "bmo_q3_net_income_common and cm_q3_net_income_common (each bank's net income available to common "
                    "shareholders for that single quarter, in CAD); cet1_average (the simple average of the five banks' "
                    "CET1 ratios at July 31, 2026, in percent, e.g. 12.5 for 12.5%); and cet1_spread_pp (the highest of "
                    "those five CET1 ratios minus the lowest, in percentage points, e.g. 0.5)."),
        "facts": {
            "analyst": [
                "Filing figures are fiscal year-to-date, so a single fiscal Q3 figure = Q3 year-to-date (9 months) - "
                "H1 year-to-date (6 months).",
                "Net income means net_income_common (available to common shareholders) from consolidated (CFS) statements, "
                "for every bank, so the five figures are comparable; do not mix in net_income or net_income_owners.",
                "cet1_ratio is a period-end ratio already in percent, so it needs no single-quarter adjustment: use each "
                "bank's fiscal 2026 Q3 value (July 31, 2026).",
                "All five fiscal years end October 31, so the inputs are each bank's fiscal 2026 H1 and fiscal 2026 Q3 "
                "CFS reports.",
            ],
        },
        "initial": {},
        "checkpoints": [
            *[{"type": "answer", "name": f"{code.lower()}_q3_net_income_common", "field": f"{code.lower()}_q3_net_income_common",
               "expected": (q3 - h1) * 1_000_000, "rel_tol": 0.001}
              for code, q3, h1 in (("RY", 16_894, 11_015), ("TD", 12_512, 7_991), ("BNS", 7_401, 4_623),
                                   ("BMO", 6_563, 4_896), ("CM", 7_601, 5_330))],
            {"type": "answer", "name": "cet1_average", "field": "cet1_average",
             "expected": round((13.5 + 14.3 + 13.1 + 13.0 + 13.4) / 5, 6), "rel_tol": 0.001},
            {"type": "answer", "name": "cet1_spread_pp", "field": "cet1_spread_pp", "expected": round(14.3 - 13.0, 6),
             "rel_tol": 0.001},
        ],
        "reference": {
            "collector": [
                *[["fetch_financials", {"company": code, "year": 2026, "period": period, "basis": "CFS"}]
                  for code in BIG5 for period in ("H1", "Q3")],
                ["share_facts", {"to_agent": "analyst", "fact_ids": [
                    *[f for code in BIG5 for f in _ca_ids(code, "2026H1.CFS.net_income_common",
                                                          "2026Q3.CFS.net_income_common")],
                    *[f"{code}.2026Q3.CFS.cet1_ratio" for code in BIG5]]}],
            ],
            "analyst": [
                ["wait", {"reason": "waiting for inputs"},
                 {"until_have": {"CM.2026Q3.CFS.cet1_ratio": 1}, "retries": 30}],
                *[["calculate", {"name": f"{code.lower()}_q3_net_income_common", "formula": "q3 - h1", "unit": "CAD",
                                 "inputs": _inputs(q3=f"{code}.2026Q3.CFS.net_income_common",
                                                   h1=f"{code}.2026H1.CFS.net_income_common")}]
                  for code in BIG5],
                ["calculate", {"name": "cet1_average", "formula": "(ry + td + bns + bmo + cm) / 5", "unit": "%",
                               "inputs": _inputs(**{code.lower(): f"{code}.2026Q3.CFS.cet1_ratio" for code in BIG5})}],
                ["calculate", {"name": "cet1_spread_pp", "formula": "td - bmo", "unit": "pp",
                               "inputs": _inputs(td="TD.2026Q3.CFS.cet1_ratio", bmo="BMO.2026Q3.CFS.cet1_ratio")}],
                ["share_facts", {"to_agent": "reporter", "fact_ids": [
                    *[f"calc.{code.lower()}_q3_net_income_common" for code in BIG5], "calc.cet1_average",
                    "calc.cet1_spread_pp"]}],
            ],
            "reporter": [
                ["wait", {"reason": "waiting for results"},
                 {"until_have": {"calc.cet1_spread_pp": 1, "calc.cet1_average": 1,
                                 **{f"calc.{code.lower()}_q3_net_income_common": 1 for code in BIG5}}, "retries": 30}],
                ["submit_answer", {"answers": [
                    *[{"field": f"{code.lower()}_q3_net_income_common",
                       "fact_id": f"calc.{code.lower()}_q3_net_income_common"} for code in BIG5],
                    {"field": "cet1_average", "fact_id": "calc.cet1_average"},
                    {"field": "cet1_spread_pp", "fact_id": "calc.cet1_spread_pp"}]}],
            ],
        },
    },
]


MARKETS = {
    "kr": {"agents": AGENTS, "traps": TRAPS, "as_of": "2025-10-01", "company": "샘플전자", "data_dir": "data/finance/sample"},
    "ca": {"agents": AGENTS_CA, "traps": TRAPS_CA, "as_of": "2026-09-30", "company": "Royal Bank of Canada",
           "data_dir": "data/finance/edgar"},
}


def build_task(spec: dict, placement: str) -> dict:
    if placement not in ("shared", "private"):
        raise ValueError(f"unknown placement {placement!r}")
    market = MARKETS[spec.get("market", "kr")]
    agents = copy.deepcopy(market["agents"])
    for a in agents:
        if spec["initial"].get(a["username"]):
            a["initial_facts"] = spec["initial"][a["username"]]
        if placement == "private" and spec["facts"].get(a["username"]):
            a["role_brief"] = " ".join(spec["facts"][a["username"]])
    shared = [market["traps"]]
    if placement == "shared":
        for owner in ("collector", "analyst", "reporter"):
            shared += spec["facts"].get(owner, [])
    return {
        "id": f"{spec['pair']}_{spec['slug']}_{placement}",
        "name": spec["name"], "category": "finance", "domain": "finance", "world_guide": False,
        "description": spec["description"],
        "objectives": {"primary": spec["primary"], "secondary": ["Use as few rounds as possible."]},
        "max_rounds": spec["max_rounds"],
        "inputs": {"as_of": spec.get("as_of", market["as_of"]), "company": spec.get("company", market["company"])},
        "data": {"dir": spec.get("data_dir", market["data_dir"])},
        "matched": {"pair_id": spec["pair"], "placement": placement,
                    "fact_counts": {k: len(v) for k, v in spec["facts"].items()}},
        "relevant_game_context": "\n".join(f"- {s}" for s in shared),
        "agents": agents,
        "verifier": {"checkpoints": spec["checkpoints"]},
        "reference_solution": spec["reference"],
    }


def write_tasks(out_dir: str | Path = "tasks/finance", ca_out_dir: str | Path = "tasks/finance_ca") -> list[Path]:
    paths = []
    for specs, directory in ((SPECS, out_dir), (SPECS_CA, ca_out_dir)):
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        for spec in specs:
            for placement in ("shared", "private"):
                td = build_task(spec, placement)
                p = out / f"{td['id']}.yaml"
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    yaml.safe_dump(td, f, allow_unicode=True, sort_keys=False, width=120)
                paths.append(p)
    return paths
