"""SEC EDGAR -> normalized company datasets (schema in ../dataset.py).

Canadian issuers that are cross-listed in the U.S. file their annual report on Form 40-F and their interim
reports on Form 6-K. Those are the same documents they file on SEDAR+, and since fiscal 2017 the financial
statements come with XBRL. EDGAR is free, needs no key, and allows automated access within its fair-access
policy, so it is the automated route to Canadian filings here.

Set a User-Agent with your own contact e-mail, as the SEC requires (requests without one are refused):
    PowerShell:  $env:SEC_USER_AGENT = "Your Name your.email@example.com"
    bash:        export SEC_USER_AGENT="Your Name your.email@example.com"

What it reads:
  www.sec.gov/files/company_tickers.json     ticker -> CIK (cached)
  data.sec.gov/submissions/CIK##########.json   filing list with form, filing date, period end, isXBRL
  Archives/edgar/data/<cik>/<accession>/          the XBRL instance of each periodic filing (cached)

Why the per-filing instance and not the companyfacts API: companyfacts lags (in October 2026 it still had
no bank 6-K after February 2026), and it mixes every comparative a value was ever repeated in. Reading the
filing that first reported a period gives the figure as originally filed, with that filing's date, which
is what the desk's as-of guard needs.

Normalization:
  Periods are FISCAL and year-to-date, like DART: Q1 = first 3 months, H1 = 6 months, Q3 = 9 months, FY = 12.
  A filing is matched to a period by its period-end date (from the company's fiscal year end) and checked
  against dei:DocumentFiscalPeriodFocus (a Q2 report is the H1 period). Duration accounts use the context
  that ends on the period end and spans the expected number of months; balance-sheet accounts use the
  period-end instant. Only contexts without dimensions are used (consolidated totals). Each account lists
  candidate concepts in order; `ext:Name` matches a company extension element by its local name, because
  banks tag items such as non-interest expense in their own namespace. The concept that matched is stored.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import SourceError, parse_periods

UA_ENV = "SEC_USER_AGENT"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
PERIODIC_FORMS = ("40-F", "6-K", "10-K", "10-Q", "20-F")
MONTHS = {"Q1": 3, "H1": 6, "Q3": 9, "FY": 12}
FOCUS_TO_PERIOD = {"Q1": "Q1", "Q2": "H1", "H1": "H1", "Q3": "Q3", "FY": "FY", "Q4": "FY"}

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"


@dataclass(frozen=True)
class Rule:
    kind: str                 # "duration" or "instant"
    concepts: tuple[str, ...]  # "ifrs-full:Name", "us-gaap:Name" or "ext:Name" (company extension)
    label: str
    unit: str                 # "money", "money/share" or "pure"
    scale: float = 1.0        # e.g. 100 to store a ratio in percent


ACCOUNT_RULES: dict[str, Rule] = {
    "revenue": Rule("duration", ("ifrs-full:Revenue", "us-gaap:Revenues",
                                 "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"),
                    "total revenue", "money"),
    "non_interest_expense": Rule("duration", ("ext:NonInterestExpense", "ext:NonInterestExpense1",
                                              "us-gaap:NoninterestExpense"),
                                 "non-interest expense", "money"),
    "net_income": Rule("duration", ("ifrs-full:ProfitLoss", "us-gaap:ProfitLoss"),
                       "net income (incl. non-controlling interests)", "money"),
    "net_income_owners": Rule("duration", ("ifrs-full:ProfitLossAttributableToOwnersOfParent", "us-gaap:NetIncomeLoss"),
                              "net income attributable to shareholders (common, preferred and other equity holders)",
                              "money"),
    "net_income_common": Rule("duration", ("ifrs-full:ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity",
                                           "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic"),
                              "net income available to common shareholders", "money"),
    "eps_basic": Rule("duration", ("ifrs-full:BasicEarningsLossPerShare", "us-gaap:EarningsPerShareBasic"),
                      "basic earnings per common share", "money/share"),
    "eps_diluted": Rule("duration", ("ifrs-full:DilutedEarningsLossPerShare", "us-gaap:EarningsPerShareDiluted"),
                        "diluted earnings per common share", "money/share"),
    "equity_owners": Rule("instant", ("ifrs-full:EquityAttributableToOwnersOfParent", "us-gaap:StockholdersEquity"),
                          "equity attributable to shareholders (incl. preferred shares and other equity instruments)",
                          "money"),
    "equity_total": Rule("instant", ("ifrs-full:Equity",
                                     "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
                         "total equity (incl. non-controlling interests)", "money"),
    "cet1_ratio": Rule("instant", ("ext:CommonEquityTierOneCapitalRatio1", "ext:CommonEquityTier1CapitalRatio1",
                                   "ext:CET1CapitalRatio", "ext:CommonEquityTierOneCapitalRatio"),
                       "Common Equity Tier 1 (CET1) capital ratio", "pure", 100.0),
    "total_assets": Rule("instant", ("ifrs-full:Assets", "us-gaap:Assets"), "total assets", "money"),
    # Each bank names it differently (BNS, RY and CM, TD, BMO in that order). Canadian banks report one RWA figure
    # for the CET1, Tier 1 and total capital ratios, so these are the same quantity.
    "rwa": Rule("instant", ("ext:RiskWeightedAssets", "ext:TotalCapitalRiskWeightedAssets",
                            "ext:RiskWeightedAssetsUsedInCalculatingTotalCapitalRatio",
                            "ext:CommonEquityTierOneCapitalRatioRiskWeightedAssets"),
                "total risk-weighted assets (RWA)", "money"),
}


# ---------------------------------------------------------------------- XBRL instance parsing
@dataclass(frozen=True)
class XbrlFact:
    prefix: str               # ifrs-full, us-gaap, dei, srt or ext
    name: str
    dims: tuple
    start: str | None
    end: str | None           # duration end, or the instant
    instant: bool
    unit: str | None          # "CAD", "CAD/shares", "shares", "pure"
    text: str


def _prefix(namespace: str) -> str:
    if "xbrl.ifrs.org" in namespace or "/ifrs" in namespace:
        return "ifrs-full"
    if "fasb.org/us-gaap" in namespace:
        return "us-gaap"
    if "xbrl.sec.gov/dei" in namespace:
        return "dei"
    if "fasb.org/srt" in namespace:
        return "srt"
    return "ext"


def _measure(el) -> str:
    return (el.text or "").strip().split(":")[-1]


def parse_instance(xml_bytes: bytes) -> list[XbrlFact]:
    root = ET.fromstring(xml_bytes)
    contexts = {}
    for c in root.iter(XBRLI + "context"):
        seg = c.find(f"{XBRLI}entity/{XBRLI}segment")
        dims = ()
        if seg is not None:
            dims = tuple(sorted((m.get("dimension", "").split(":")[-1], (m.text or "").strip().split(":")[-1])
                                for m in seg if m.tag in (XBRLDI + "explicitMember", XBRLDI + "typedMember")))
        period = c.find(XBRLI + "period")
        instant = period.findtext(XBRLI + "instant")
        if instant:
            contexts[c.get("id")] = (dims, None, instant.strip(), True)
        else:
            contexts[c.get("id")] = (dims, (period.findtext(XBRLI + "startDate") or "").strip(),
                                     (period.findtext(XBRLI + "endDate") or "").strip(), False)
    units = {}
    for u in root.iter(XBRLI + "unit"):
        div = u.find(XBRLI + "divide")
        if div is not None:
            num = [_measure(m) for m in div.find(XBRLI + "unitNumerator").iter(XBRLI + "measure")]
            den = [_measure(m) for m in div.find(XBRLI + "unitDenominator").iter(XBRLI + "measure")]
            units[u.get("id")] = "*".join(num) + "/" + "*".join(den)
        else:
            units[u.get("id")] = "*".join(_measure(m) for m in u.iter(XBRLI + "measure"))
    facts = []
    for el in root:
        ref = el.get("contextRef")
        if ref is None or ref not in contexts or not isinstance(el.tag, str) or not el.tag.startswith("{"):
            continue
        ns, name = el.tag[1:].split("}", 1)
        dims, start, end, instant = contexts[ref]
        facts.append(XbrlFact(_prefix(ns), name, dims, start, end, instant, units.get(el.get("unitRef")),
                              (el.text or "").strip()))
    return facts


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _unit_ok(rule: Rule, unit: str | None) -> bool:
    if unit is None:
        return False
    if rule.unit == "money":
        return len(unit) == 3 and unit.isupper()
    if rule.unit == "money/share":
        return unit.endswith("/shares") and len(unit.split("/")[0]) == 3
    return unit == "pure"


def _matches(spec: str, fact: XbrlFact) -> bool:
    prefix, _, name = spec.partition(":")
    return fact.name == name and fact.prefix == prefix


def extract_period(facts: list[XbrlFact], period_end: str, period: str) -> tuple[dict, dict, str | None]:
    """Values of ACCOUNT_RULES for one fiscal year-to-date period -> (values, concepts used, currency)."""
    end = _date(period_end)
    want_days = MONTHS[period] * 30.44
    plain = [f for f in facts if not f.dims and f.end and abs((_date(f.end) - end).days) <= 3]
    values, used, currency = {}, {}, None
    for account, rule in ACCOUNT_RULES.items():
        for spec in rule.concepts:
            hits = [f for f in plain if _matches(spec, f) and f.instant == (rule.kind == "instant")
                    and _unit_ok(rule, f.unit) and _number(f.text) is not None]
            if rule.kind == "duration":
                hits = [f for f in hits if f.start and abs((_date(f.end) - _date(f.start)).days - want_days) <= 20]
            if hits:
                value = _number(hits[0].text) * rule.scale
                values[account] = round(value, 6) if rule.unit != "money" else value
                used[account] = spec
                if rule.unit == "money" and currency is None:
                    currency = hits[0].unit
                break
    return values, used, currency


def dei_value(facts: list[XbrlFact], name: str) -> str | None:
    return next((f.text for f in facts if f.prefix == "dei" and f.name == name and f.text), None)


# ---------------------------------------------------------------------- fiscal calendar
def _add_months(d: dt.date, months: int, month_end: bool) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, last if month_end else min(d.day, last))


def period_end_date(fiscal_year_end: str, year: int, period: str) -> dt.date:
    """fiscal_year_end is MMDD as in EDGAR submissions (e.g. "1031"). Fiscal year YEAR ends in calendar YEAR."""
    month, day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    fye = dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))
    month_end = fye.day == calendar.monthrange(fye.year, fye.month)[1]
    return _add_months(fye, MONTHS[period] - 12, month_end)


def display_name(conformed: str) -> str:
    """EDGAR conformed names are upper case with suffixes such as /CAN/; make them readable."""
    words = conformed.split("/")[0].strip().title().split()
    small = {"Of", "The", "And", "For", "De", "Du", "La"}
    return " ".join(w.lower() if i and w in small else w for i, w in enumerate(words))


# ---------------------------------------------------------------------- client
def _rows(cols: dict) -> list[dict]:
    keys = ("form", "filingDate", "reportDate", "accessionNumber", "isXBRL")
    return [{k: cols[k][i] for k in keys} for i in range(len(cols["form"]))]


class EdgarClient:
    def __init__(self, user_agent: str | None = None, cache_dir: str | Path = "data/finance/cache/edgar",
                 pause: float = 0.15, timeout: float = 60.0, fetch: Callable[[str], bytes] | None = None) -> None:
        self.user_agent = (user_agent or os.environ.get(UA_ENV, "")).strip()
        if fetch is None and "@" not in self.user_agent:
            raise SourceError(f"Set {UA_ENV} to your name and contact e-mail, e.g. \"Jane Doe jane@example.com\". "
                              "The SEC refuses automated requests without a declared contact.")
        self.cache_dir = Path(cache_dir)
        self.pause = pause
        self.timeout = timeout
        self._fetch = fetch or self._http

    def _http(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            hint = " (the SEC refused the User-Agent; use a real contact e-mail)" if exc.code == 403 else ""
            raise SourceError(f"EDGAR returned HTTP {exc.code} for {url}{hint}") from None
        except urllib.error.URLError as exc:
            raise SourceError(f"Cannot reach EDGAR ({exc.reason})") from None
        time.sleep(self.pause)
        return body

    def _cached(self, url: str, name: str, refresh: bool = False) -> bytes:
        path = self.cache_dir / name
        if path.exists() and not refresh:
            return path.read_bytes()
        body = self._fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body

    def resolve(self, company: str) -> tuple[int, str, str]:
        """Ticker, CIK or exact name -> (cik, ticker, name)."""
        q = str(company).strip()
        if q.isdigit():
            return int(q), q, q
        rows = json.loads(self._cached(TICKERS_URL, "company_tickers.json")).values()
        hits = [r for r in rows if q.upper() == r["ticker"].upper() or q.lower() == r["title"].lower()]
        if not hits:
            raise SourceError(f"'{company}' is not an EDGAR ticker or company name")
        r = hits[0]
        return int(r["cik_str"]), r["ticker"], r["title"]

    def submissions(self, cik: int, refresh: bool = True) -> dict:
        return json.loads(self._cached(SUBMISSIONS_URL.format(cik=cik), f"submissions_{cik}.json", refresh=refresh))

    def filings(self, cik: int, since: str | None = None) -> tuple[dict, list[dict]]:
        """All filings in the recent list, plus older pages that reach back to `since` (YYYY-MM-DD).
        Frequent filers (banks issuing structured notes) have only a few months in the recent list."""
        sub = self.submissions(cik)
        rows = _rows(sub["filings"]["recent"])
        for page in sub["filings"].get("files", []):
            if since and page.get("filingTo", "") >= since:
                url = "https://data.sec.gov/submissions/" + page["name"]
                rows += _rows(json.loads(self._cached(url, "pages/" + page["name"])))
        return sub, rows

    def instance(self, cik: int, accession: str) -> bytes:
        acc = accession.replace("-", "")
        index = json.loads(self._fetch(ARCHIVE_URL.format(cik=cik, acc=acc, name="index.json")))
        files = [i["name"] for i in index["directory"]["item"]]
        candidates = [f for f in files if f.endswith("_htm.xml")] or [
            f for f in files if f.endswith(".xml") and not f.startswith(("FilingSummary", "MetaLinks"))
            and not f.endswith(("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml"))]
        if not candidates:
            raise SourceError(f"filing {accession} has no XBRL instance")
        return self._fetch(ARCHIVE_URL.format(cik=cik, acc=acc, name=candidates[0]))

    def facts(self, cik: int, accession: str) -> list[XbrlFact]:
        """Facts without dimensions (plus dei) of one filing. Cached as small JSON; the raw instance is not kept."""
        path = self.cache_dir / "facts" / f"{cik}_{accession.replace('-', '')}.json"
        if path.exists():
            return [XbrlFact(*row[:2], tuple(), *row[2:]) for row in json.loads(path.read_text(encoding="utf-8"))]
        facts = [f for f in parse_instance(self.instance(cik, accession)) if not f.dims and (f.prefix == "dei" or f.unit)]
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [[f.prefix, f.name, f.start, f.end, f.instant, f.unit, f.text[:200]] for f in facts]
        path.write_text(json.dumps(rows), encoding="utf-8")
        return facts


# ---------------------------------------------------------------------- source
class EdgarSource:
    name = "edgar"

    def __init__(self, client: EdgarClient | None = None, cache_dir: str | Path = "data/finance/cache/edgar",
                 log=print) -> None:
        self.client = client or EdgarClient(cache_dir=cache_dir)
        self.log = log

    def find_filing(self, filings: list[dict], fiscal_year_end: str, year: int, period: str) -> list[dict]:
        target = period_end_date(fiscal_year_end, year, period)
        rows = [r for r in filings if r["form"] in PERIODIC_FORMS and r["isXBRL"] and r["reportDate"]
                and abs((_date(r["reportDate"]) - target).days) <= 7]
        return sorted(rows, key=lambda r: r["filingDate"])

    def statement(self, cik: int, filings: list[dict], fiscal_year_end: str, year: int, period: str) -> dict | None:
        for row in self.find_filing(filings, fiscal_year_end, year, period):
            facts = self.client.facts(cik, row["accessionNumber"])
            focus = FOCUS_TO_PERIOD.get((dei_value(facts, "DocumentFiscalPeriodFocus") or "").upper())
            end = dei_value(facts, "DocumentPeriodEndDate") or row["reportDate"]
            if focus != period:
                continue  # an XBRL 6-K that is not this period's report
            values, used, currency = extract_period(facts, end, period)
            if not values:
                continue
            return {"filed": row["filingDate"], "doc_id": row["accessionNumber"], "form": row["form"],
                    "period_end": end[:10], "currency": currency, **values, "concepts": used}
        return None

    def build_company(self, company: str, periods: list[str], *, prices: dict | None = None,
                      price_sources: dict | None = None, out_dir: str | Path = "data/finance/edgar",
                      market: str = "ca", name: str | None = None, exchange: str | None = None,
                      aliases: list[str] | None = None, **_) -> Path:
        cik, ticker, title = self.client.resolve(company)
        targets = parse_periods(periods)
        sub, filings = self.client.filings(cik)
        fye = sub.get("fiscalYearEnd") or "1231"
        since = min(period_end_date(fye, y, p) for y, p in targets).isoformat() if targets else None
        if since and (not filings or min(r["filingDate"] for r in filings) > since):
            sub, filings = self.client.filings(cik, since=since)
        ticker = (sub.get("tickers") or [ticker])[0] if ticker.isdigit() else ticker
        out = Path(out_dir) / f"{ticker}.json"
        data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        data.update({
            "name": name or data.get("name") or display_name(sub.get("name") or title), "stock_code": ticker, "cik": f"{cik:010d}", "lang": "en", "market": market,
            "fiscal_year_end": f"{fye[:2]}-{fye[2:]}",
            "source": "SEC EDGAR: XBRL instance of each periodic filing (40-F annual, 6-K interim)",
            "accounts": {k: {"label": r.label, "unit": r.unit} for k, r in ACCOUNT_RULES.items()},
        })
        if exchange:
            data["exchange"] = exchange
        if aliases:
            data["aliases"] = sorted(set(data.get("aliases", [])) | set(aliases))
        for key in ("statements", "shares", "prices", "price_sources"):
            data.setdefault(key, {})
        for year, period in targets:
            st = self.statement(cik, filings, fye, year, period)
            if st is None:
                self.log(f"  {ticker} {year} {period}: no XBRL periodic filing found")
                continue
            data["statements"][f"{year}:{period}:CFS"] = st
            data["currency"] = data.get("currency") or st["currency"]
            if st["currency"] != data["currency"]:
                raise SourceError(f"{ticker} {year} {period} is in {st['currency']}, other periods in {data['currency']}")
            n = len(st["concepts"])
            self.log(f"  {ticker} {year} {period}: {n} accounts from {st['form']} {st['doc_id']} filed {st['filed']}")
        data["prices"].update(prices or {})
        data["price_sources"].update(price_sources or {})
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        return out
