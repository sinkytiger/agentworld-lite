"""Normalized company datasets built from DART OpenAPI responses (see dart.py).

One JSON file per company:
{
  "name": "샘플전자", "stock_code": "999990", "corp_code": "00000000", "currency": "KRW",
  "statements": {                      # key = "<year>:<period>:<basis>"
    "2026:H1:CFS": {"filed": "2026-08-14", "rcept_no": "...",
                    "revenue": ..., "operating_income": ..., "net_income": ...,
                    "net_income_owners": ..., "equity_owners": ..., "equity_total": ...}
  },
  "shares": {"2026:H1": {"filed": "...", "rcept_no": "...", "common": ..., "preferred": ..., "treasury_common": ...}},
  "prices": {"2026-10-06": 60000}      # KRX close, common shares
}

Periods follow DART report codes: FY (사업보고서, 11011), H1 (반기, 11012), Q1 (11013), Q3 (11014).
Income-statement values are CUMULATIVE from the start of the fiscal year (DART `thstrm_add_amount`;
for the annual report `thstrm_amount`). A standalone quarter must be derived, e.g. Q2 = H1 - Q1.
Balance-sheet values are period-end balances.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PERIODS = ("Q1", "H1", "Q3", "FY")
BASES = ("CFS", "OFS")  # consolidated / separate
ACCOUNTS = {
    "revenue": ("매출액", "KRW"),
    "operating_income": ("영업이익", "KRW"),
    "net_income": ("당기순이익(비지배지분 포함)", "KRW"),
    "net_income_owners": ("지배기업 소유주지분 순이익", "KRW"),
    "equity_owners": ("지배기업 소유주지분 자본", "KRW"),
    "equity_total": ("자본총계", "KRW"),
}
PERIOD_LABEL = {"Q1": "1분기보고서(누적)", "H1": "반기보고서(누적)", "Q3": "3분기보고서(누적)", "FY": "사업보고서(연간)"}
BASIS_LABEL = {"CFS": "연결", "OFS": "별도"}


@dataclass
class Fact:
    id: str
    label: str
    value: float
    unit: str
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "value": self.value, "unit": self.unit, "meta": self.meta}


def fmt_value(value: float, unit: str) -> str:
    if unit == "KRW" and abs(value) >= 1e8:
        return f"{value / 1e8:,.1f}억원 ({value:,.0f}원)"
    if unit == "KRW":
        return f"{value:,.0f}원"
    if unit == "shares":
        return f"{value:,.0f}주"
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "x":
        return f"{value:,.2f}배"
    return f"{value:,.4g} {unit}".strip()


class Company:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.name: str = data["name"]
        self.stock_code: str = data["stock_code"]

    def statement(self, year: int, period: str, basis: str) -> dict | None:
        return self.data.get("statements", {}).get(f"{year}:{period}:{basis}")

    def shares(self, year: int, period: str) -> dict | None:
        return self.data.get("shares", {}).get(f"{year}:{period}")

    def price(self, date: str) -> float | None:
        return self.data.get("prices", {}).get(date)


class Dataset:
    """All company files in one directory, looked up by name or stock code."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.companies: dict[str, Company] = {}
        for p in sorted(self.directory.glob("*.json")):
            c = Company(json.loads(p.read_text(encoding="utf-8")))
            self.companies[c.stock_code] = c

    def find(self, query: str) -> Company | None:
        q = str(query).strip()
        if q in self.companies:
            return self.companies[q]
        for c in self.companies.values():
            if c.name == q or c.name.replace(" ", "") == q.replace(" ", ""):
                return c
        return None


def statement_fact_id(company: Company, year: int, period: str, basis: str, account: str) -> str:
    return f"{company.stock_code}.{year}{period}.{basis}.{account}"


def statement_facts(company: Company, year: int, period: str, basis: str) -> list[Fact]:
    st = company.statement(year, period, basis)
    if st is None:
        return []
    out = []
    for account, (ko, unit) in ACCOUNTS.items():
        if st.get(account) is None:
            continue
        label = f"{company.name} {year} {PERIOD_LABEL[period]} {BASIS_LABEL[basis]} {ko}"
        out.append(Fact(statement_fact_id(company, year, period, basis, account), label, float(st[account]), unit,
                        {"source": "DART", "rcept_no": st.get("rcept_no"), "filed": st.get("filed"),
                         "company": company.stock_code, "year": year, "period": period, "basis": basis,
                         "account": account}))
    return out


def share_facts(company: Company, year: int, period: str) -> list[Fact]:
    sh = company.shares(year, period)
    if sh is None:
        return []
    names = {"common": "발행주식 총수(보통주)", "preferred": "발행주식 총수(우선주)", "treasury_common": "자기주식수(보통주)"}
    return [Fact(f"{company.stock_code}.{year}{period}.shares_{k}", f"{company.name} {year} {PERIOD_LABEL[period]} {v}",
                 float(sh[k]), "shares",
                 {"source": "DART", "rcept_no": sh.get("rcept_no"), "filed": sh.get("filed"), "company": company.stock_code})
            for k, v in names.items() if sh.get(k) is not None]


def price_fact(company: Company, date: str) -> Fact | None:
    px = company.price(date)
    if px is None:
        return None
    return Fact(f"{company.stock_code}.price.{date}", f"{company.name} 종가 ({date}, KRX)", float(px), "KRW",
                {"source": "KRX", "company": company.stock_code, "date": date})
