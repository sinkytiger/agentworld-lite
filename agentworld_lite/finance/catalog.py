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


# ---------------------------------------------------------------------- Korean desk: the four financial holding companies
# Real figures from data/finance/dart_fhc (DART fnlttSinglAcntAll of each group's periodic reports), KRW millions below.
# The as-of date is after 2026-10-07, when Shinhan re-filed its 2025 annual and 2026 half-year reports ([기재정정]);
# DART serves the amended version with its own filing date, and the figures match the originals' comparatives.
FHC = {"kb": "105560", "shinhan": "055550", "hana": "086790", "woori": "316140"}  # field prefix -> stock code
FHC_NAMES = {"105560": "KB금융", "055550": "신한지주", "086790": "하나금융지주", "316140": "우리금융지주"}
TRAPS_KR_FHC = ("Common traps: consolidated (CFS) vs separate (OFS) statements, net income vs net income attributable to "
                "owners, equity attributable to owners vs common equity (hybrid capital securities, 신종자본증권, sit in "
                "owners' equity), cumulative vs single-quarter figures, issued vs treasury shares, and reports filed after "
                "the as-of date (for an amended report DART serves the amended version, with its later filing date).")
M = 1_000_000
# 2026 H1 (KB in k01, all four in k03): cumulative net income to owners, Q1 net income to owners, owners' equity and
# hybrid capital at 2026-06-30.
FHC_H1 = {"105560": (3_884_590, 1_892_401, 60_890_838, 4_206_721), "055550": (3_442_729, 1_622_594, 60_100_749, 4_163_879),
          "086790": (2_402_868, 1_210_045, 46_876_523, 4_399_244), "316140": (1_608_896, 604_318, 38_884_675, 3_511_032)}
# k02: (general_admin_expense, operating_income, credit_loss_provision) for 2026 Q1 and 2026 H1, cumulative
FHC_COST = {"055550": ((1_545_405, 2_154_453, 520_112), (3_216_096, 4_630_734, 976_425)),
            "316140": ((1_422_809, 808_155, 526_782), (2_632_856, 2_123_827, 966_025))}
KB_TTM = 5_833_162 - 3_435_693 + 3_884_590          # FY 2025 - H1 2025 + H1 2026 net income to owners
KB_CE_PREV = 59_069_434 - 5_162_483                   # common equity at 2025-06-30 (owners' equity - hybrid capital)
KB_SHARES = (354_687_734, 1_300_000)                  # issued common and treasury common shares at 2026-06-30
# k04: net income to owners, (H1, Q1) cumulative, for 2025 (the 2026 figures are in FHC_H1)
FHC_2025 = {"105560": (3_435_693, 1_697_271), "055550": (3_037_399, 1_488_346), "086790": (2_301_028, 1_127_742),
            "316140": (1_552_019, 616_691)}
# k05: (general_admin_expense, operating_income, credit_loss_provision), 2026 H1 cumulative
FHC_H1_COST = {"105560": (3_654_401, 5_440_114, 1_013_014), "055550": (3_216_096, 4_630_734, 976_425),
               "086790": (2_489_027, 3_247_984, 663_830), "316140": (2_632_856, 2_123_827, 966_025)}


def _cir(code: str) -> float:
    (g1, o1, p1), (g2, o2, p2) = FHC_COST[code]
    return round((g2 - g1) / ((o2 + g2 + p2) - (o1 + g1 + p1)) * 100, 6)


def _common_equity(code: str) -> int:
    return FHC_H1[code][2] - FHC_H1[code][3]


def _kr_inputs(code: str, *names: str) -> list[str]:
    return [f"{code}.{n}" for n in names]


# k01 is built from filings alone. The Korean desk uses no market prices: DART has none, and the KRX Open API's terms
# forbid passing its data to third parties, so PER and PBR are left out; BPS and ROE need no price.
def _k01() -> dict:
    kb = FHC["kb"]
    ce = _common_equity(kb)
    return {
        "pair": "k01", "slug": "kb_bps_roe", "max_rounds": 18, "market": "kr_fhc",
        "company": "KB금융",
        "name": "BPS and trailing ROE of KB금융 at the end of 2026 H1",
        "name_ko": "KB금융 BPS·TTM ROE (2026년 상반기 말)",
        "description_ko": ("KB금융(105560)의 2026년 상반기 말(2026-06-30) 기준 주당순자산(BPS)과 TTM ROE를 2026-10-15까지 "
                           "접수된 보고서만으로 구합니다. collector가 공시를 모으고 analyst가 계산하며 reporter가 제출합니다."),
        "description": ("The desk needs the book value per share (BPS) and the trailing twelve-month (TTM) return on equity "
                        "of KB금융 (KB Financial Group, stock code 105560) at the end of 2026 H1 (2026-06-30), using only "
                        "reports filed by 2026-10-15. The collector pulls filings, the analyst computes, the reporter submits."),
        "primary": ("Submit three answers: ttm_net_income_owners (TTM net income attributable to owners of the parent, in "
                    "KRW), bps (common equity per common share outstanding at 2026-06-30, in KRW) and ttm_roe "
                    "(ttm_net_income_owners divided by average common equity, in percent, e.g. 9.5 for 9.5%)."),
        "facts": {
            "analyst": [
                "TTM = latest fiscal-year (FY) figure - prior-year cumulative figure for the same period as the latest "
                "report + latest cumulative figure. For an H1 report: FY(2025) - H1(2025) + H1(2026).",
                "Net income means net_income_owners (attributable to owners of the parent) from consolidated (CFS) statements.",
                "Common equity = equity_owners - hybrid_capital (hybrid capital securities, 신종자본증권, are part of "
                "owners' equity but not common shareholders' equity), from the CFS balance sheet.",
                "BPS = common equity at 2026-06-30 / common shares outstanding, where shares outstanding = issued common "
                "shares - treasury common shares from the 2026 H1 report (KB금융 has no preferred shares).",
                "TTM ROE (%) = TTM net income / average common equity x 100, where average common equity = (common equity "
                "at 2025-06-30 + common equity at 2026-06-30) / 2. Do not annualize half-year or quarterly figures.",
                "The latest report filed by 2026-10-15 is the 2026 H1 report, so the inputs are the 2025 FY, 2025 H1 and "
                "2026 H1 CFS reports and the 2026 H1 share counts.",
            ],
        },
        "initial": {},
        "checkpoints": [
            {"type": "answer", "name": "ttm_net_income_owners", "field": "ttm_net_income_owners", "expected": KB_TTM * M,
             "rel_tol": 0.001},
            {"type": "answer", "name": "bps", "field": "bps", "expected": round(ce * M / (KB_SHARES[0] - KB_SHARES[1]), 6),
             "rel_tol": 0.001},
            {"type": "answer", "name": "ttm_roe", "field": "ttm_roe", "expected": round(KB_TTM / ((KB_CE_PREV + ce) / 2) * 100, 6),
             "rel_tol": 0.001},
        ],
        "reference": {
            "collector": [
                ["fetch_financials", {"company": kb, "year": 2025, "period": "FY", "basis": "CFS"}],
                ["fetch_financials", {"company": kb, "year": 2025, "period": "H1", "basis": "CFS"}],
                ["fetch_financials", {"company": kb, "year": 2026, "period": "H1", "basis": "CFS"}],
                ["fetch_shares", {"company": kb, "year": 2026, "period": "H1"}],
                ["share_facts", {"to_agent": "analyst", "fact_ids": _kr_inputs(
                    kb, "2025FY.CFS.net_income_owners", "2025H1.CFS.net_income_owners", "2026H1.CFS.net_income_owners",
                    "2025H1.CFS.equity_owners", "2025H1.CFS.hybrid_capital", "2026H1.CFS.equity_owners",
                    "2026H1.CFS.hybrid_capital", "2026H1.shares_common", "2026H1.shares_treasury_common")}],
            ],
            "analyst": [
                ["wait", {"reason": "waiting for inputs"},
                 {"until_have": {f"{kb}.2026H1.shares_treasury_common": 1}, "retries": 30}],
                ["calculate", {"name": "ttm_net_income_owners", "formula": "fy - h1_prev + h1", "unit": "KRW",
                               "inputs": _inputs(fy=f"{kb}.2025FY.CFS.net_income_owners",
                                                 h1_prev=f"{kb}.2025H1.CFS.net_income_owners",
                                                 h1=f"{kb}.2026H1.CFS.net_income_owners")}],
                ["calculate", {"name": "common_equity", "formula": "e - h", "unit": "KRW",
                               "inputs": _inputs(e=f"{kb}.2026H1.CFS.equity_owners", h=f"{kb}.2026H1.CFS.hybrid_capital")}],
                ["calculate", {"name": "common_equity_prev", "formula": "e - h", "unit": "KRW",
                               "inputs": _inputs(e=f"{kb}.2025H1.CFS.equity_owners", h=f"{kb}.2025H1.CFS.hybrid_capital")}],
                ["calculate", {"name": "bps", "formula": "c / (s - t)", "unit": "KRW",
                               "inputs": _inputs(c="calc.common_equity", s=f"{kb}.2026H1.shares_common",
                                                 t=f"{kb}.2026H1.shares_treasury_common")}],
                ["calculate", {"name": "ttm_roe", "formula": "n / ((c0 + c1) / 2) * 100", "unit": "%",
                               "inputs": _inputs(n="calc.ttm_net_income_owners", c0="calc.common_equity_prev",
                                                 c1="calc.common_equity")}],
                ["share_facts", {"to_agent": "reporter", "fact_ids": ["calc.ttm_net_income_owners", "calc.bps", "calc.ttm_roe"]}],
            ],
            "reporter": [
                ["wait", {"reason": "waiting for results"},
                 {"until_have": {"calc.bps": 1, "calc.ttm_roe": 1, "calc.ttm_net_income_owners": 1}, "retries": 30}],
                ["submit_answer", {"answers": [{"field": "ttm_net_income_owners", "fact_id": "calc.ttm_net_income_owners"},
                                               {"field": "bps", "fact_id": "calc.bps"},
                                               {"field": "ttm_roe", "fact_id": "calc.ttm_roe"}]}],
            ],
        },
    }


K02 = {
    "pair": "k02", "slug": "q2_cost_income", "max_rounds": 20, "market": "kr_fhc",
    "company": "신한지주; 우리금융지주",
    "name": "Second-quarter 2026 cost-to-income ratios of 신한지주 and 우리금융지주",
    "name_ko": "신한지주·우리금융지주 2026년 2분기 영업이익경비율(CIR)",
    "description_ko": ("신한지주(055550)와 우리금융지주(316140)의 2026년 2분기(4~6월) 단독 영업이익경비율을 2026-10-15까지 "
                       "접수된 보고서만으로 비교합니다. collector가 공시를 모으고 analyst가 계산하며 reporter가 제출합니다."),
    "description": ("The desk compares the cost-to-income ratios of 신한지주 (Shinhan Financial Group, stock code 055550) "
                    "and 우리금융지주 (Woori Financial Group, stock code 316140) for the second quarter of 2026 alone "
                    "(April-June 2026), using only reports filed by 2026-10-15. The collector pulls filings, the analyst "
                    "computes, the reporter submits."),
    "primary": ("Submit two answers: shinhan_q2_cost_income_ratio and woori_q2_cost_income_ratio (general and "
                "administrative expenses divided by operating revenue for that single quarter, in percent, e.g. 45.0 for "
                "45.0%)."),
    "facts": {
        "analyst": [
            "Report figures are cumulative from January, so a standalone Q2 figure = H1 cumulative - Q1 cumulative.",
            "Cost-to-income ratio (%) = general_admin_expense / operating revenue x 100, where operating revenue "
            "(총영업이익, income before G&A and credit-loss provisions) = operating_income + general_admin_expense + "
            "credit_loss_provision, all for the same single quarter from consolidated (CFS) statements.",
            "The inputs are each group's 2026 Q1 CFS and 2026 H1 CFS reports.",
        ],
    },
    "initial": {},
    "checkpoints": [
        {"type": "answer", "name": f"{g}_q2_cost_income_ratio", "field": f"{g}_q2_cost_income_ratio",
         "expected": _cir(FHC[g]), "rel_tol": 0.001} for g in ("shinhan", "woori")],
    "reference": {
        "collector": [
            *[["fetch_financials", {"company": FHC[g], "year": 2026, "period": p, "basis": "CFS"}]
              for g in ("shinhan", "woori") for p in ("Q1", "H1")],
            ["share_facts", {"to_agent": "analyst", "fact_ids": [
                f"{FHC[g]}.2026{p}.CFS.{a}" for g in ("shinhan", "woori") for p in ("Q1", "H1")
                for a in ("general_admin_expense", "operating_income", "credit_loss_provision")]}],
        ],
        "analyst": [
            ["wait", {"reason": "waiting for inputs"},
             {"until_have": {f"{FHC['woori']}.2026H1.CFS.credit_loss_provision": 1}, "retries": 30}],
            *[step for g in ("shinhan", "woori") for step in (
                ["calculate", {"name": f"{g}_q2_general_admin_expense", "formula": "h1 - q1", "unit": "KRW",
                               "inputs": _inputs(h1=f"{FHC[g]}.2026H1.CFS.general_admin_expense",
                                                 q1=f"{FHC[g]}.2026Q1.CFS.general_admin_expense")}],
                ["calculate", {"name": f"{g}_q2_operating_revenue",
                               "formula": "(h1_oi + h1_ga + h1_pv) - (q1_oi + q1_ga + q1_pv)", "unit": "KRW",
                               "inputs": _inputs(**{f"{p.lower()}_{v}": f"{FHC[g]}.2026{p}.CFS.{a}" for p in ("H1", "Q1")
                                                    for v, a in (("oi", "operating_income"), ("ga", "general_admin_expense"),
                                                                 ("pv", "credit_loss_provision"))})}],
                ["calculate", {"name": f"{g}_q2_cost_income_ratio", "formula": "g / r * 100", "unit": "%",
                               "inputs": _inputs(g=f"calc.{g}_q2_general_admin_expense",
                                                 r=f"calc.{g}_q2_operating_revenue")}])],
            ["share_facts", {"to_agent": "reporter",
                             "fact_ids": ["calc.shinhan_q2_cost_income_ratio", "calc.woori_q2_cost_income_ratio"]}],
        ],
        "reporter": [
            ["wait", {"reason": "waiting for results"},
             {"until_have": {"calc.shinhan_q2_cost_income_ratio": 1, "calc.woori_q2_cost_income_ratio": 1}, "retries": 30}],
            ["submit_answer", {"answers": [
                {"field": f"{g}_q2_cost_income_ratio", "fact_id": f"calc.{g}_q2_cost_income_ratio"}
                for g in ("shinhan", "woori")]}],
        ],
    },
}

K03 = {
    # Single-quarter net income to owners (H1 - Q1) and common equity (owners' equity - hybrid capital) at 2026-06-30.
    "pair": "k03", "slug": "big4_q2_sector", "max_rounds": 30, "market": "kr_fhc",
    "company": "KB금융; 신한지주; 하나금융지주; 우리금융지주",
    "name": "Korea's four financial holding companies: Q2 2026 earnings and common equity",
    "name_ko": "4대 금융지주 2026년 2분기 지배주주순이익과 보통주자본",
    "description_ko": ("KB금융(105560), 신한지주(055550), 하나금융지주(086790), 우리금융지주(316140)의 2026년 2분기(4~6월) "
                       "단독 지배주주순이익과 2분기 말 보통주자본을 2026-10-15까지 접수된 보고서만으로 비교합니다. "
                       "collector가 공시를 모으고 analyst가 계산하며 reporter가 제출합니다."),
    "description": ("The desk compares Korea's four largest financial holding companies, KB금융 (105560), 신한지주 (055550), "
                    "하나금융지주 (086790) and 우리금융지주 (316140), on earnings for the second quarter of 2026 alone "
                    "(April-June 2026) and on common equity at the end of that quarter, using only reports filed by "
                    "2026-10-15. The collector pulls filings, the analyst computes, the reporter submits."),
    "primary": ("Submit six answers: kb_q2_net_income_owners, shinhan_q2_net_income_owners, hana_q2_net_income_owners and "
                "woori_q2_net_income_owners (each group's net income attributable to owners of the parent for that single "
                "quarter, in KRW); common_equity_average (the simple average of the four groups' common equity at "
                "2026-06-30, in KRW); and common_equity_spread (the largest of those four common equity figures minus the "
                "smallest, in KRW)."),
    "facts": {
        "analyst": [
            "Report figures are cumulative from January, so a standalone Q2 figure = H1 cumulative - Q1 cumulative.",
            "Net income means net_income_owners (attributable to owners of the parent) from consolidated (CFS) statements, "
            "for every group, so the four figures are comparable; do not mix in net_income, which includes "
            "non-controlling interests.",
            "Common equity = equity_owners - hybrid_capital (hybrid capital securities, 신종자본증권, are part of owners' "
            "equity but not common equity), from each group's 2026 H1 CFS balance sheet. Balance-sheet items are "
            "period-end balances and need no single-quarter adjustment.",
            "The inputs are each group's 2026 Q1 CFS and 2026 H1 CFS reports.",
        ],
    },
    "initial": {},
    "checkpoints": [
        *[{"type": "answer", "name": f"{g}_q2_net_income_owners", "field": f"{g}_q2_net_income_owners",
           "expected": (FHC_H1[code][0] - FHC_H1[code][1]) * M, "rel_tol": 0.001} for g, code in FHC.items()],
        {"type": "answer", "name": "common_equity_average", "field": "common_equity_average",
         "expected": sum(_common_equity(c) for c in FHC.values()) * M / 4, "rel_tol": 0.001},
        {"type": "answer", "name": "common_equity_spread", "field": "common_equity_spread",
         "expected": (_common_equity(FHC["kb"]) - _common_equity(FHC["woori"])) * M, "rel_tol": 0.001},
    ],
    "reference": {
        "collector": [
            *[["fetch_financials", {"company": code, "year": 2026, "period": p, "basis": "CFS"}]
              for code in FHC.values() for p in ("Q1", "H1")],
            ["share_facts", {"to_agent": "analyst", "fact_ids": [
                *[f for code in FHC.values() for f in _kr_inputs(code, "2026Q1.CFS.net_income_owners",
                                                                 "2026H1.CFS.net_income_owners")],
                *[f for code in FHC.values() for f in _kr_inputs(code, "2026H1.CFS.equity_owners",
                                                                 "2026H1.CFS.hybrid_capital")]]}],
        ],
        "analyst": [
            ["wait", {"reason": "waiting for inputs"},
             {"until_have": {f"{FHC['woori']}.2026H1.CFS.hybrid_capital": 1}, "retries": 30}],
            *[["calculate", {"name": f"{g}_q2_net_income_owners", "formula": "h1 - q1", "unit": "KRW",
                             "inputs": _inputs(h1=f"{code}.2026H1.CFS.net_income_owners",
                                               q1=f"{code}.2026Q1.CFS.net_income_owners")}]
              for g, code in FHC.items()],
            ["calculate", {"name": "common_equity_average",
                           "formula": "((kb_e - kb_h) + (shinhan_e - shinhan_h) + (hana_e - hana_h) + (woori_e - woori_h)) / 4",
                           "unit": "KRW",
                           "inputs": _inputs(**{f"{g}_{v}": f"{code}.2026H1.CFS.{a}" for g, code in FHC.items()
                                                for v, a in (("e", "equity_owners"), ("h", "hybrid_capital"))})}],
            ["calculate", {"name": "common_equity_spread", "formula": "(kb_e - kb_h) - (woori_e - woori_h)", "unit": "KRW",
                           "inputs": _inputs(kb_e=f"{FHC['kb']}.2026H1.CFS.equity_owners",
                                             kb_h=f"{FHC['kb']}.2026H1.CFS.hybrid_capital",
                                             woori_e=f"{FHC['woori']}.2026H1.CFS.equity_owners",
                                             woori_h=f"{FHC['woori']}.2026H1.CFS.hybrid_capital")}],
            ["share_facts", {"to_agent": "reporter", "fact_ids": [
                *[f"calc.{g}_q2_net_income_owners" for g in FHC], "calc.common_equity_average",
                "calc.common_equity_spread"]}],
        ],
        "reporter": [
            ["wait", {"reason": "waiting for results"},
             {"until_have": {"calc.common_equity_spread": 1, "calc.common_equity_average": 1,
                             **{f"calc.{g}_q2_net_income_owners": 1 for g in FHC}}, "retries": 30}],
            ["submit_answer", {"answers": [
                *[{"field": f"{g}_q2_net_income_owners", "fact_id": f"calc.{g}_q2_net_income_owners"} for g in FHC],
                {"field": "common_equity_average", "fact_id": "calc.common_equity_average"},
                {"field": "common_equity_spread", "fact_id": "calc.common_equity_spread"}]}],
        ],
    },
}


# ---------------------------------------------------------------------- harder Korean tasks
# k04 is long: sixteen reports, five answers, and a combined-group growth rate that the average of the four growth
# rates misses by 9.5%. k05 states no data plan (the team must find the latest period itself) and asks for a combined
# ratio (the average of the four ratios misses by 2.0%). Both have tighter round budgets than k01-k03.
def _q2(code: str, year: int) -> int:
    h1, q1 = (FHC_H1[code][0], FHC_H1[code][1]) if year == 2026 else FHC_2025[code]
    return h1 - q1


def _h1_cir(code: str) -> float:
    g, o, p = FHC_H1_COST[code]
    return g / (o + g + p) * 100


K04 = {
    "pair": "k04", "slug": "big4_q2_yoy", "max_rounds": 28, "market": "kr_fhc",
    "company": "KB금융; 신한지주; 하나금융지주; 우리금융지주",
    "name": "Korea's four financial holding companies: year-on-year growth of Q2 2026 earnings",
    "name_ko": "4대 금융지주 2026년 2분기 지배주주순이익 전년 대비 증가율",
    "description": ("The desk measures how much each of Korea's four largest financial holding companies, KB금융 (105560), "
                    "신한지주 (055550), 하나금융지주 (086790) and 우리금융지주 (316140), grew its earnings in the second "
                    "quarter of 2026 alone (April-June) against the same quarter of 2025, and how much the four grew "
                    "together, using only reports filed by 2026-10-15. The collector pulls filings, the analyst computes, "
                    "the reporter submits."),
    "description_ko": ("4대 금융지주(KB금융 105560, 신한지주 055550, 하나금융지주 086790, 우리금융지주 316140)의 2026년 2분기(4~6월) "
                       "단독 지배주주순이익이 2025년 같은 분기보다 얼마나 늘었는지, 네 그룹을 합치면 얼마나 늘었는지를 "
                       "2026-10-15까지 접수된 보고서만으로 구합니다. 보고서 16개가 필요한 긴 과제입니다."),
    "primary": ("Submit five answers: kb_q2_yoy_growth, shinhan_q2_yoy_growth, hana_q2_yoy_growth and woori_q2_yoy_growth "
                "(each group's year-on-year growth of net income attributable to owners for that single quarter, in "
                "percent, e.g. 12.5 for +12.5%), and sector_q2_yoy_growth (the same growth for the four groups together, in "
                "percent)."),
    "facts": {
        "analyst": [
            "Report figures are cumulative from January, so a standalone Q2 figure = H1 cumulative - Q1 cumulative, for "
            "2025 and for 2026.",
            "Net income means net_income_owners (attributable to owners of the parent) from consolidated (CFS) statements.",
            "Year-on-year growth (%) = (Q2 2026 / Q2 2025 - 1) x 100.",
            "sector_q2_yoy_growth treats the four groups as one combined group.",
            "The inputs are each group's 2025 Q1, 2025 H1, 2026 Q1 and 2026 H1 CFS reports.",
        ],
    },
    "initial": {},
    "checkpoints": [
        *[{"type": "answer", "name": f"{g}_q2_yoy_growth", "field": f"{g}_q2_yoy_growth",
           "expected": round((_q2(code, 2026) / _q2(code, 2025) - 1) * 100, 6), "rel_tol": 0.001}
          for g, code in FHC.items()],
        {"type": "answer", "name": "sector_q2_yoy_growth", "field": "sector_q2_yoy_growth",
         "expected": round((sum(_q2(c, 2026) for c in FHC.values()) / sum(_q2(c, 2025) for c in FHC.values()) - 1) * 100, 6),
         "rel_tol": 0.001},
    ],
    "reference": {
        "collector": [
            *[["fetch_financials", {"company": code, "year": y, "period": p, "basis": "CFS"}]
              for code in FHC.values() for y in (2025, 2026) for p in ("Q1", "H1")],
            ["share_facts", {"to_agent": "analyst", "fact_ids": [
                f"{code}.{y}{p}.CFS.net_income_owners" for code in FHC.values() for y in (2025, 2026) for p in ("Q1", "H1")]}],
        ],
        "analyst": [
            ["wait", {"reason": "waiting for inputs"},
             {"until_have": {f"{FHC['woori']}.2026H1.CFS.net_income_owners": 1}, "retries": 40}],
            *[["calculate", {"name": f"{g}_q2_yoy_growth", "formula": "((h26 - q26) / (h25 - q25) - 1) * 100", "unit": "%",
                             "inputs": _inputs(**{f"{p[0].lower()}{str(y)[2:]}": f"{code}.{y}{p}.CFS.net_income_owners"
                                                  for y in (2025, 2026) for p in ("Q1", "H1")})}]
              for g, code in FHC.items()],
            ["calculate", {"name": "sector_q2_yoy_growth",
                           "formula": "((kb_h26 - kb_q26 + shinhan_h26 - shinhan_q26 + hana_h26 - hana_q26 + woori_h26 - woori_q26)"
                                      " / (kb_h25 - kb_q25 + shinhan_h25 - shinhan_q25 + hana_h25 - hana_q25 + woori_h25 - woori_q25)"
                                      " - 1) * 100",
                           "unit": "%",
                           "inputs": _inputs(**{f"{g}_{p[0].lower()}{str(y)[2:]}": f"{code}.{y}{p}.CFS.net_income_owners"
                                                for g, code in FHC.items() for y in (2025, 2026) for p in ("Q1", "H1")})}],
            ["share_facts", {"to_agent": "reporter", "fact_ids": [
                *[f"calc.{g}_q2_yoy_growth" for g in FHC], "calc.sector_q2_yoy_growth"]}],
        ],
        "reporter": [
            ["wait", {"reason": "waiting for results"},
             {"until_have": {"calc.sector_q2_yoy_growth": 1, **{f"calc.{g}_q2_yoy_growth": 1 for g in FHC}}, "retries": 40}],
            ["submit_answer", {"answers": [
                *[{"field": f"{g}_q2_yoy_growth", "fact_id": f"calc.{g}_q2_yoy_growth"} for g in FHC],
                {"field": "sector_q2_yoy_growth", "fact_id": "calc.sector_q2_yoy_growth"}]}],
        ],
    },
}

K05 = {
    "pair": "k05", "slug": "big4_combined_cir", "max_rounds": 14, "market": "kr_fhc",
    "company": "KB금융; 신한지주; 하나금융지주; 우리금융지주",
    "name": "Korea's four financial holding companies: combined cost-to-income ratio",
    "name_ko": "4대 금융지주 합산 영업이익경비율(CIR)과 그룹 간 격차",
    "description": ("The desk needs the cost-to-income ratio of Korea's four largest financial holding companies, KB금융 "
                    "(105560), 신한지주 (055550), 하나금융지주 (086790) and 우리금융지주 (316140), taken together, and the "
                    "gap between the most and the least efficient of them, for the latest reporting period available on "
                    "2026-10-15. The collector pulls filings, the analyst computes, the reporter submits."),
    "description_ko": ("4대 금융지주를 하나로 합친 영업이익경비율(CIR)과, 가장 효율적인 그룹과 가장 비효율적인 그룹의 CIR 차이를 "
                       "2026-10-15에 볼 수 있는 가장 최근 보고 기간으로 구합니다. 어떤 보고서를 쓸지는 알려 주지 않습니다."),
    "primary": ("Submit two answers: sector_cost_income_ratio (the cost-to-income ratio of the four groups as one, in "
                "percent, e.g. 40.0 for 40.0%) and cost_income_spread_pp (the highest of the four groups' ratios minus the "
                "lowest, in percentage points, e.g. 5.0)."),
    "facts": {
        "analyst": [
            "Cost-to-income ratio (%) = general_admin_expense / operating revenue x 100, where operating revenue "
            "(총영업이익, income before G&A and credit-loss provisions) = operating_income + general_admin_expense + "
            "credit_loss_provision, all from consolidated (CFS) statements.",
            "Use the latest reporting period whose reports were filed by the as-of date, with the figures as reported "
            "(cumulative from January).",
            "sector_cost_income_ratio treats the four groups as one combined group; cost_income_spread_pp is the highest "
            "of the four groups' ratios minus the lowest.",
        ],
    },
    "initial": {},
    "checkpoints": [
        {"type": "answer", "name": "sector_cost_income_ratio", "field": "sector_cost_income_ratio",
         "expected": round(sum(v[0] for v in FHC_H1_COST.values()) / sum(sum(v) for v in FHC_H1_COST.values()) * 100, 6),
         "rel_tol": 0.001},
        {"type": "answer", "name": "cost_income_spread_pp", "field": "cost_income_spread_pp",
         "expected": round(max(_h1_cir(c) for c in FHC.values()) - min(_h1_cir(c) for c in FHC.values()), 6),
         "rel_tol": 0.001},
    ],
    "reference": {
        "collector": [
            *[["fetch_financials", {"company": code, "year": 2026, "period": "H1", "basis": "CFS"}] for code in FHC.values()],
            ["share_facts", {"to_agent": "analyst", "fact_ids": [
                f"{code}.2026H1.CFS.{a}" for code in FHC.values()
                for a in ("general_admin_expense", "operating_income", "credit_loss_provision")]}],
        ],
        "analyst": [
            ["wait", {"reason": "waiting for inputs"},
             {"until_have": {f"{FHC['woori']}.2026H1.CFS.credit_loss_provision": 1}, "retries": 30}],
            *[["calculate", {"name": f"{g}_cost_income_ratio", "formula": "g / (o + g + p) * 100", "unit": "%",
                             "inputs": _inputs(g=f"{code}.2026H1.CFS.general_admin_expense",
                                               o=f"{code}.2026H1.CFS.operating_income",
                                               p=f"{code}.2026H1.CFS.credit_loss_provision")}]
              for g, code in FHC.items()],
            ["calculate", {"name": "sector_cost_income_ratio",
                           "formula": "(kb_g + shinhan_g + hana_g + woori_g) / (kb_o + kb_g + kb_p + shinhan_o + shinhan_g"
                                      " + shinhan_p + hana_o + hana_g + hana_p + woori_o + woori_g + woori_p) * 100",
                           "unit": "%",
                           "inputs": _inputs(**{f"{g}_{v}": f"{code}.2026H1.CFS.{a}" for g, code in FHC.items()
                                                for v, a in (("g", "general_admin_expense"), ("o", "operating_income"),
                                                             ("p", "credit_loss_provision"))})}],
            ["calculate", {"name": "cost_income_spread_pp", "formula": "w - k", "unit": "pp",
                           "inputs": _inputs(w="calc.woori_cost_income_ratio", k="calc.kb_cost_income_ratio")}],
            ["share_facts", {"to_agent": "reporter",
                             "fact_ids": ["calc.sector_cost_income_ratio", "calc.cost_income_spread_pp"]}],
        ],
        "reporter": [
            ["wait", {"reason": "waiting for results"},
             {"until_have": {"calc.sector_cost_income_ratio": 1, "calc.cost_income_spread_pp": 1}, "retries": 30}],
            ["submit_answer", {"answers": [
                {"field": "sector_cost_income_ratio", "fact_id": "calc.sector_cost_income_ratio"},
                {"field": "cost_income_spread_pp", "fact_id": "calc.cost_income_spread_pp"}]}],
        ],
    },
}


SPECS_KR_FHC = [_k01(), K02, K03, K04, K05]


MARKETS = {
    "kr": {"agents": AGENTS, "traps": TRAPS, "as_of": "2025-10-01", "company": "샘플전자", "data_dir": "data/finance/sample"},
    "ca": {"agents": AGENTS_CA, "traps": TRAPS_CA, "as_of": "2026-09-30", "company": "Royal Bank of Canada",
           "data_dir": "data/finance/edgar"},
    "kr_fhc": {"agents": AGENTS, "traps": TRAPS_KR_FHC, "as_of": "2026-10-15", "company": "KB금융",
               "data_dir": "data/finance/dart_fhc"},
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
    extra = {k: spec[k] for k in ("name_ko", "description_ko") if spec.get(k)}
    return {
        "id": f"{spec['pair']}_{spec['slug']}_{placement}",
        "name": spec["name"], **extra, "category": "finance", "domain": "finance", "world_guide": False,
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


def write_tasks(out_dir: str | Path = "tasks/finance", ca_out_dir: str | Path = "tasks/finance_ca",
                kr_out_dir: str | Path = "tasks/finance_kr") -> list[Path]:
    paths = []
    for specs, directory in ((SPECS, out_dir), (SPECS_CA, ca_out_dir), (SPECS_KR_FHC, kr_out_dir)):
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
