"""함정 목록: 한국 금융지주 과제(k01-k06)의 답마다, 흔한 실수를 했을 때 나오는 값. leaderboard.py가 모델 실행의 오답과 중간
계산을 이 목록과 대조해 어떤 함정에 걸렸는지 분류한다.

Values come from the committed dataset (data/finance/dart_fhc) and the value-up file, with the same formulas as the trap
tests in tests/test_finance_kr.py and tests/test_finance_valueup.py; tests/test_finance_leaderboard.py checks that every
trap here misses its answer's 0.1% tolerance.
"""

from __future__ import annotations

import json
import statistics
from functools import lru_cache
from pathlib import Path

from . import valueup as vu
from .catalog import FHC
from .env import REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "finance" / "dart_fhc"
GROUPS = tuple(FHC)             # kb, shinhan, hana, woori
NI, NCI = "net_income_owners", "net_income"


@lru_cache(maxsize=None)
def _data(code: str, data_dir: str = str(DATA_DIR)) -> dict:
    return json.loads((Path(data_dir) / f"{code}.json").read_text(encoding="utf-8"))


def _st(code: str, key: str, basis: str = "CFS") -> dict:
    return _data(code)["statements"][f"{key}:{basis}"]


def _q(code: str, year: int, account: str, quarter: str = "Q2") -> float:
    """A standalone quarter from cumulative reports: Q2 = H1 - Q1, Q4 = FY - Q3."""
    a, b = {"Q2": ("H1", "Q1"), "Q3": ("Q3", "H1"), "Q4": ("FY", "Q3")}[quarter]
    return _st(code, f"{year}:{a}")[account] - _st(code, f"{year}:{b}")[account]


def _ce(code: str, key: str) -> float:
    s = _st(code, key)
    return s["equity_owners"] - s["hybrid_capital"]


def _cir(g: float, o: float, p: float) -> float:
    return g / (o + g + p) * 100


def _cir_of(code: str, key: str, quarter: bool = False) -> float:
    acc = ("general_admin_expense", "operating_income", "credit_loss_provision")
    if quarter:  # Q2 of the year in key
        year = int(key.split(":")[0])
        return _cir(*(_q(code, year, a) for a in acc))
    s = _st(code, key)
    return _cir(*(s[a] for a in acc))


def _ttm(code: str, account: str = NI, basis: str = "CFS") -> float:
    return (_st(code, "2025:FY", basis)[account] - _st(code, "2025:H1", basis)[account]
            + _st(code, "2026:H1", basis)[account])


def _k01() -> dict[str, list[tuple[str, float]]]:
    kb = FHC["kb"]
    d, h1 = _data(kb), _st(kb, "2026:H1")
    ttm, ttm_nci = _ttm(kb), _ttm(kb, NCI)
    common, treasury = d["shares"]["2026:H1"]["common"], d["shares"]["2026:H1"]["treasury_common"]
    out_now = common - treasury
    fy_sh = d["shares"]["2025:FY"]
    ce_avg = (_ce(kb, "2025:H1") + _ce(kb, "2026:H1")) / 2
    roe = ttm / ce_avg * 100
    avg = lambda acc: (_st(kb, "2025:H1")[acc] + h1[acc]) / 2
    traps = {
        "ttm_net_income_owners": [("비지배지분 포함 순이익", ttm_nci), ("직전 연간 순이익만", _st(kb, "2025:FY")[NI]),
                                  ("상반기 순이익 × 2", 2 * h1[NI]), ("2분기 순이익 × 4", 4 * _q(kb, 2026, NI))],
        "bps": [("자기주식을 빼지 않은 주식 수", _ce(kb, "2026:H1") / common),
                ("신종자본증권을 빼지 않음", h1["equity_owners"] / out_now),
                ("자본총계(비지배지분 포함)", h1["equity_total"] / out_now),
                ("2025년 말 주식 수", _ce(kb, "2026:H1") / (fy_sh["common"] - fy_sh["treasury_common"])),
                ("2025년 말 자본", _ce(kb, "2025:FY") / out_now)],
        "ttm_roe": [("기말 자본만", ttm / _ce(kb, "2026:H1") * 100),
                    ("2025년 말과 6월 말의 평균", ttm / ((_ce(kb, "2025:FY") + _ce(kb, "2026:H1")) / 2) * 100),
                    ("상반기 연환산", 2 * h1[NI] / ((_ce(kb, "2025:FY") + _ce(kb, "2026:H1")) / 2) * 100),
                    ("신종자본증권을 빼지 않음", ttm / avg("equity_owners") * 100),
                    ("자본총계(비지배지분 포함)", ttm / avg("equity_total") * 100),
                    ("비지배지분 포함 순이익", ttm_nci / ce_avg * 100), ("소수로 표기", roe / 100)],
    }
    ofs = [_data(kb)["statements"].get(f"{k}:OFS") or {} for k in ("2025:FY", "2025:H1", "2026:H1")]
    if all(NCI in s for s in ofs):  # separate statements carry net income only (no owners' line)
        traps["ttm_net_income_owners"].append(("별도 재무제표", _ttm(kb, NCI, "OFS")))
    return traps


def _k02() -> dict[str, list[tuple[str, float]]]:
    out = {}
    pair = {"shinhan": FHC["shinhan"], "woori": FHC["woori"]}
    right = {g: _cir_of(c, "2026:H1", quarter=True) for g, c in pair.items()}
    for g, c in pair.items():
        q = {a: _q(c, 2026, a) for a in ("general_admin_expense", "operating_income", "net_interest_income",
                                          "net_fee_income")}
        other = right["woori" if g == "shinhan" else "shinhan"]
        out[f"{g}_q2_cost_income_ratio"] = [
            ("상반기 누적", _cir_of(c, "2026:H1")), ("1분기", _cir_of(c, "2026:Q1")),
            ("순이자이익 + 순수수료이익 분모", q["general_admin_expense"] / (q["net_interest_income"] + q["net_fee_income"]) * 100),
            ("영업이익 + 일반관리비 분모", q["general_admin_expense"] / (q["operating_income"] + q["general_admin_expense"]) * 100),
            ("소수로 표기", right[g] / 100), ("두 그룹을 바꿈", other)]
    return out


def _k03() -> dict[str, list[tuple[str, float]]]:
    out = {}
    for g, c in FHC.items():
        h1, q1 = _st(c, "2026:H1"), _st(c, "2026:Q1")
        out[f"{g}_q2_net_income_owners"] = [
            ("비지배지분 포함 순이익", _q(c, 2026, NCI)), ("상반기 누적", h1[NI]), ("상반기 ÷ 2", h1[NI] / 2),
            ("1분기", q1[NI]), ("전년 2분기", _q(c, 2025, NI)), ("2025년 4분기", _q(c, 2025, NI, "Q4"))]
    ce = [_ce(c, "2026:H1") for c in FHC.values()]
    variants = {"신종자본증권을 빼지 않음": [_st(c, "2026:H1")["equity_owners"] for c in FHC.values()],
                "자본총계(비지배지분 포함)": [_st(c, "2026:H1")["equity_total"] for c in FHC.values()],
                "3월 말 잔액": [_ce(c, "2026:Q1") for c in FHC.values()],
                "2025년 말 잔액": [_ce(c, "2025:FY") for c in FHC.values()]}
    out["common_equity_average"] = [(k, sum(v) / len(v)) for k, v in variants.items()] + [
        ("중앙값", statistics.median(ce)), *[(f"{g}을(를) 뺀 세 그룹 평균", (sum(ce) - x) / 3) for g, x in zip(GROUPS, ce)]]
    out["common_equity_spread"] = [(k, max(v) - min(v)) for k, v in variants.items()]
    return out


def _growth(now: float, before: float) -> float:
    return (now / before - 1) * 100


def _k04() -> dict[str, list[tuple[str, float]]]:
    out, rates = {}, []
    for g, c in FHC.items():
        right = _growth(_q(c, 2026, NI), _q(c, 2025, NI))
        rates.append(right)
        out[f"{g}_q2_yoy_growth"] = [
            ("상반기 누적끼리", _growth(_st(c, "2026:H1")[NI], _st(c, "2025:H1")[NI])),
            ("1분기끼리", _growth(_st(c, "2026:Q1")[NI], _st(c, "2025:Q1")[NI])),
            ("전분기 대비", _growth(_q(c, 2026, NI), _st(c, "2026:Q1")[NI])),
            ("비지배지분 포함 순이익", _growth(_q(c, 2026, NCI), _q(c, 2025, NCI))),
            ("증가율 대신 비율", _q(c, 2026, NI) / _q(c, 2025, NI) * 100), ("소수로 표기", right / 100)]
    combined = lambda year, account=NI: sum(_q(c, year, account) for c in FHC.values())
    h1 = lambda year: sum(_st(c, f"{year}:H1")[NI] for c in FHC.values())
    sector = _growth(combined(2026), combined(2025))
    out["sector_q2_yoy_growth"] = [
        ("네 증가율의 평균", statistics.mean(rates)), ("네 증가율의 중앙값", statistics.median(rates)),
        ("비지배지분 포함 순이익", _growth(combined(2026, NCI), combined(2025, NCI))),
        ("상반기 누적끼리", _growth(h1(2026), h1(2025))), ("소수로 표기", sector / 100)]
    return out


def _k05() -> dict[str, list[tuple[str, float]]]:
    acc = ("general_admin_expense", "operating_income", "credit_loss_provision")

    def combined(values: list[dict]) -> float:
        return sum(v["general_admin_expense"] for v in values) / sum(sum(v[a] for a in acc) for v in values) * 100

    def at(key: str) -> list[dict]:
        return [_st(c, key) for c in FHC.values()]

    def q2() -> list[dict]:
        return [{a: _q(c, 2026, a) for a in acc} for c in FHC.values()]
    ratios = lambda vals: [_cir(*(v[a] for a in acc)) for v in vals]
    h1 = at("2026:H1")
    rs = ratios(h1)
    others = {"2분기 단독": q2(), "1분기": at("2026:Q1"), "2025년 연간": at("2025:FY")}
    return {
        "sector_cost_income_ratio": [
            ("네 CIR의 평균", statistics.mean(rs)), ("네 CIR의 중앙값", statistics.median(rs)),
            ("순이자이익 + 순수수료이익 분모", sum(v["general_admin_expense"] for v in h1)
             / sum(v["net_interest_income"] + v["net_fee_income"] for v in h1) * 100),
            ("소수로 표기", combined(h1) / 100), *[(k, combined(v)) for k, v in others.items()]],
        "cost_income_spread_pp": [
            ("상대 격차(최고 ÷ 최저 − 1)", (max(rs) / min(rs) - 1) * 100), ("최고 − 평균", max(rs) - statistics.mean(rs)),
            *[(k, max(ratios(v)) - min(ratios(v))) for k, v in others.items()]],
    }


def _k06() -> dict[str, list[tuple[str, float]]]:
    out = {}
    hist = vu.load()["history"]
    for g, target in (("shinhan", 10.0), ("hana", 12.0)):
        c = FHC[g]
        ttm = _ttm(c)
        e0, e1 = _st(c, "2025:H1")["equity_owners"], _st(c, "2026:H1")["equity_owners"]
        right = ttm / ((e0 + e1) / 2) * 100
        fy, dec25 = _st(c, "2025:FY")[NI], _st(c, "2025:FY")["equity_owners"]
        roe_traps = [("기말 자본만", ttm / e1 * 100),
                     ("신종자본증권을 뺀 보통주자본", ttm / ((_ce(c, "2025:H1") + _ce(c, "2026:H1")) / 2) * 100),
                     ("비지배지분 포함 순이익", _ttm(c, NCI) / ((e0 + e1) / 2) * 100),
                     ("상반기 연환산", 2 * _st(c, "2026:H1")[NI] / ((dec25 + e1) / 2) * 100),
                     ("2025 사업연도 ROE", fy / ((hist[c]["2024:FY"]["equity_owners"] + dec25) / 2) * 100),
                     ("소수로 표기", right / 100)]
        out[f"{g}_ttm_roe"] = roe_traps
        out[f"{g}_roe_gap_pp"] = [("부호 반대(ROE − 목표)", right - target),
                                  ("목표 대비 비율(격차 ÷ 목표)", (target - right) / target * 100),
                                  *[(f"ROE를 {k}(으)로 계산", target - v) for k, v in roe_traps if k != "소수로 표기"]]
    return out


BUILDERS = {"k01": _k01, "k02": _k02, "k03": _k03, "k04": _k04, "k05": _k05, "k06": _k06}


@lru_cache(maxsize=None)
def trap_values(pair: str) -> dict[str, tuple[tuple[str, float], ...]]:
    """{answer field: ((mistake, value it gives), ...)} for one task pair (k01 ... k06)."""
    return {f: tuple(v) for f, v in BUILDERS[pair]().items()}


def match_trap(pair: str, field: str, value: float, rel_tol: float = 0.001) -> str | None:
    """The mistake whose value lies within rel_tol of `value`, if any (closest first)."""
    hits = [(abs(value - v) / (abs(v) or 1), label) for label, v in trap_values(pair).get(field, ())
            if abs(value - v) <= rel_tol * abs(v)]
    return min(hits)[1] if hits else None
