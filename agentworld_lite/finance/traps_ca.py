"""Trap catalog for the Canadian desk (c01-c04): for each answer, the value a common mistake gives. leaderboard.py matches
models' wrong answers and intermediate calculations against it.

Values come from the committed dataset (data/finance/edgar) with the same formulas as the trap tests in
tests/test_finance_ca.py and the mistake tables in docs/finance_canada.md; tests/test_finance_leaderboard_ca.py checks
that every trap here misses its answer's 0.1% tolerance.
"""

from __future__ import annotations

import json
import statistics
from functools import lru_cache
from pathlib import Path

from .catalog import BIG5
from .env import REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "finance" / "edgar"
NIC = "net_income_common"


@lru_cache(maxsize=None)
def _data(code: str) -> dict:
    return json.loads((DATA_DIR / f"{code}.json").read_text(encoding="utf-8"))


def _st(code: str, key: str) -> dict:
    return _data(code)["statements"][f"{key}:CFS"]


def _q3(code: str, account: str) -> float:
    """Fiscal Q3 2026 alone: Q3 year-to-date minus H1 year-to-date."""
    return _st(code, "2026:Q3")[account] - _st(code, "2026:H1")[account]


def _ttm(code: str, account: str) -> float:
    return _st(code, "2025:FY")[account] - _st(code, "2025:Q3")[account] + _st(code, "2026:Q3")[account]


def _ratio(code: str, key: str) -> float:
    s = _st(code, key)
    return s["non_interest_expense"] / s["revenue"] * 100


def _c01() -> dict[str, list[tuple[str, float]]]:
    price, price_prev = _data("RY")["prices"]["2026-07-31"], _data("RY")["prices"]["2025-07-31"]
    eps = _ttm("RY", "eps_diluted")
    q3_eps = _q3("RY", "eps_diluted")
    return {
        "ttm_net_income_common": [
            ("net income to all shareholders (incl. preferred)", _ttm("RY", "net_income_owners")),
            ("net income incl. non-controlling interests", _ttm("RY", "net_income")),
            ("nine months as the year", _st("RY", "2026:Q3")[NIC]),
            ("fiscal 2025 (stale)", _st("RY", "2025:FY")[NIC]),
            ("one quarter times four", 4 * _q3("RY", NIC)),
            ("in millions", _ttm("RY", NIC) / 1e6),
        ],
        "pe": [
            ("basic instead of diluted EPS", price / _ttm("RY", "eps_basic")),
            ("nine-month EPS as the year", price / _st("RY", "2026:Q3")["eps_diluted"]),
            ("fiscal 2025 EPS (stale)", price / _st("RY", "2025:FY")["eps_diluted"]),
            ("one quarter's EPS times four", price / (4 * q3_eps)),
            ("price a year earlier", price_prev / eps),
            ("inverted (EPS / price)", eps / price),
        ],
    }


def _c02() -> dict[str, list[tuple[str, float]]]:
    out = {}
    for code in ("RY", "TD"):
        right = _q3(code, "non_interest_expense") / _q3(code, "revenue") * 100
        out[f"{code.lower()}_q3_efficiency_ratio"] = [
            ("nine-month ratio", _ratio(code, "2026:Q3")),
            ("six-month ratio", _ratio(code, "2026:H1")),
            ("fiscal Q1 ratio", _ratio(code, "2026:Q1")),
            ("fraction, not percent", right / 100),
            ("inverted (revenue / expense)", 1e4 / right),
        ]
    return out


def _c03() -> dict[str, list[tuple[str, float]]]:
    out = {}
    for code in BIG5:
        q1, h1, q3 = (_st(code, k) for k in ("2026:Q1", "2026:H1", "2026:Q3"))
        traps = [("nine months", q3[NIC]), ("six months", h1[NIC]),
                 ("net income incl. non-controlling interests", _q3(code, "net_income")),
                 ("nine months / 3", q3[NIC] / 3), ("fiscal Q2 alone", h1[NIC] - q1[NIC]),
                 ("fiscal Q4 2025", _st(code, "2025:FY")[NIC] - _st(code, "2025:Q3")[NIC]),
                 ("in millions", _q3(code, NIC) / 1e6)]
        if "net_income_owners" in q3:  # TD tags no shareholders' line
            traps.insert(3, ("net income to all shareholders (incl. preferred)", _q3(code, "net_income_owners")))
        out[f"{code.lower()}_q3_net_income_common"] = traps
    cet1 = lambda key: [_st(c, key)["cet1_ratio"] for c in BIG5]
    spread = lambda v: max(v) - min(v)
    now = cet1("2026:Q3")
    out["cet1_average"] = [
        ("H1 ratios", statistics.mean(cet1("2026:H1"))), ("fiscal Q1 ratios", statistics.mean(cet1("2026:Q1"))),
        ("fiscal 2025 ratios", statistics.mean(cet1("2025:FY"))),
        ("a year-earlier Q3", statistics.mean(cet1("2025:Q3"))), ("median, not mean", statistics.median(now)),
        ("TD left out", statistics.mean([v for c, v in zip(BIG5, now) if c != "TD"])),
        ("fraction, not percent", statistics.mean(now) / 100)]
    out["cet1_spread_pp"] = [  # H1 ratios give the right spread (TD and BMO unchanged), so they are not a trap here
        ("fiscal Q1 ratios", spread(cet1("2026:Q1"))), ("fiscal 2025 ratios", spread(cet1("2025:FY"))),
        ("a year-earlier Q3", spread(cet1("2025:Q3"))), ("fraction, not percent", spread(now) / 100),
        ("basis points", spread(now) * 100), ("relative spread (%)", (max(now) / min(now) - 1) * 100),
        ("highest minus average", max(now) - statistics.mean(now))]
    return out


def _c04() -> dict[str, list[tuple[str, float]]]:
    from .catalog import ROE_OBJECTIVE, _roe_gap
    out = {}
    for code in BIG5:
        objective, _, reported, adjusted, _ = ROE_OBJECTIVE[code]
        right = _roe_gap(code)
        used = objective - right                                   # the ROE on the objective's basis
        traps = [("sign reversed (ROE - objective)", -right), ("gap relative to the objective (%)", right / objective * 100),
                 ("the ROE itself, not the gap", used), ("the objective itself, not the gap", objective)]
        other = reported if used == adjusted else adjusted
        if other is not None:
            traps.insert(0, ("the other basis (reported vs adjusted)", objective - other))
        if code == "TD":
            traps.append(("TD's fiscal 2026 objective (~13%) instead of the medium-term one", 13.0 - used))
        out[f"{code.lower()}_roe_gap_pp"] = traps
    return out


BUILDERS = {"c01": _c01, "c02": _c02, "c03": _c03, "c04": _c04}


@lru_cache(maxsize=None)
def trap_values(pair: str) -> dict[str, tuple[tuple[str, float], ...]]:
    """{answer field: ((mistake, value it gives), ...)} for one task pair (c01 ... c04)."""
    return {f: tuple(v) for f, v in BUILDERS[pair]().items()}


def match_trap(pair: str, field: str, value: float, rel_tol: float = 0.001) -> str | None:
    """The mistake whose value lies within rel_tol of `value`, if any (closest first)."""
    hits = [(abs(value - v) / (abs(v) or 1), label) for label, v in trap_values(pair).get(field, ())
            if abs(value - v) <= rel_tol * abs(v)]
    return min(hits)[1] if hits else None
