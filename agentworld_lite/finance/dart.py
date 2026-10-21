"""DART OpenAPI client -> normalized company datasets (see dataset.py for the schema).

Get a free key at https://opendart.fss.or.kr (인증키 신청) and set it yourself:
    PowerShell:  $env:DART_API_KEY = "<your key>"
    bash:        export DART_API_KEY=<your key>
The key is read from the environment only; it is never printed, logged or written to disk.

Endpoints used (all JSON except corpCode.xml):
  corpCode.xml          zip of every company: corp_code, corp_name, stock_code   (cached locally)
  fnlttSinglAcntAll     full financial statements of one periodic report
  stockTotqySttus       issued / treasury share counts of one periodic report
  list.json             periodic filings of a company (pblntf_ty=A), to find the original of an amended report
Report codes: 11013 Q1, 11012 H1, 11014 Q3, 11011 FY (사업보고서).

Normalization:
  Income-statement accounts are taken from IS, or CIS when the company presents a single statement.
  Quarterly and half-year reports carry the current 3-month figure in `thstrm_amount` and the cumulative
  figure in `thstrm_add_amount`; the dataset stores the CUMULATIVE figure. The annual report's
  `thstrm_amount` is already the full year. Balance-sheet accounts are period-end balances.
  `filed` is the receipt date encoded in the first 8 digits of rcept_no. If the report was later amended,
  DART returns the amended report, so `filed` can be later than the original filing (a conservative as-of guard).
  With first_filings=True the original report is looked up in list.json and stored as `first_rcept_no` /
  `first_filed`; the figures and `filed` stay those of the version DART served.
  Closing prices are not in DART; pass them with --price DATE=VALUE and say where they come from with --price-source.

Account profiles:
  default  operating companies: revenue, operating income, net income, equity
  fhc      financial holding companies (금융지주), which present a bank-format statement of comprehensive income
           with no revenue line: net interest and fee income, general and administrative expenses, credit-loss
           provisions, net income, and equity with hybrid capital securities (신종자본증권) shown on their own.
           The XBRL account_id that matched is stored per statement under "concepts".
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .dataset import BASES, PERIODS

BASE_URL = "https://opendart.fss.or.kr/api/"
REPORT_CODES = {"Q1": "11013", "H1": "11012", "Q3": "11014", "FY": "11011"}
KEY_ENV = "DART_API_KEY"
STATUS = {
    "010": "unregistered key", "011": "key not usable", "012": "IP address not allowed for this key",
    "013": "no data", "014": "file does not exist", "020": "daily request limit exceeded",
    "021": "too many companies in one request", "100": "invalid field value", "101": "inappropriate access",
    "800": "DART system maintenance", "900": "undefined error", "901": "key expired (personal-information retention period)",
}

# account -> (statement kinds, XBRL account ids, Korean name fallbacks)
ACCOUNT_RULES = {
    "revenue": (("IS", "CIS"), ("ifrs-full_Revenue", "ifrs_Revenue"), ("매출액", "수익(매출액)", "영업수익", "매출")),
    "operating_income": (("IS", "CIS"), ("dart_OperatingIncomeLoss",), ("영업이익", "영업이익(손실)")),
    "net_income": (("IS", "CIS"), ("ifrs-full_ProfitLoss", "ifrs_ProfitLoss"),
                   ("당기순이익", "당기순이익(손실)", "반기순이익", "반기순이익(손실)", "분기순이익", "분기순이익(손실)")),
    "net_income_owners": (("IS", "CIS"),
                          ("ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs_ProfitLossAttributableToOwnersOfParent"),
                          ("지배기업 소유주지분", "지배기업의 소유주에게 귀속되는 당기순이익", "지배기업소유주지분")),
    "equity_owners": (("BS",), ("ifrs-full_EquityAttributableToOwnersOfParent", "ifrs_EquityAttributableToOwnersOfParent"),
                      ("지배기업 소유주지분", "지배기업의 소유주에게 귀속되는 자본", "지배기업소유주지분")),
    "equity_total": (("BS",), ("ifrs-full_Equity", "ifrs_Equity"), ("자본총계",)),
}
IS_ACCOUNTS = {"revenue", "operating_income", "net_income", "net_income_owners"}

# Financial holding companies. The ids are the ones KB금융지주, 신한지주, 하나금융지주 and 우리금융지주 use in their
# 2025-2026 reports. G&A is the one account they tag differently (Shinhan: GeneralAndAdministrativeExpense, the
# other three: SellingGeneralAndAdministrativeExpense), so both are listed. Names are only a fallback: Hana labels
# its owners' net income "반기순이익(손실)" and Shinhan labels the comprehensive-income line "지배기업 소유주지분",
# so a name alone can pick the wrong row.
FHC_ACCOUNT_RULES = {
    "net_interest_income": (("IS", "CIS"), ("ifrs-full_InterestRevenueExpense",), ("순이자이익", "순이자손익")),
    "net_fee_income": (("IS", "CIS"), ("ifrs-full_FeeAndCommissionIncomeExpense",),
                       ("순수수료이익", "순수수료손익", "순수수료수익(비용)")),
    "general_admin_expense": (("IS", "CIS"), ("ifrs-full_SellingGeneralAndAdministrativeExpense",
                                              "ifrs-full_GeneralAndAdministrativeExpense"), ("일반관리비", "판매비와관리비")),
    # From the 2025 H1 reports on, all four tag the IFRS 9 impairment element. Their 2025 Q1 (and 2024 FY) reports used
    # a DART element (KB, Shinhan; Woori in 2024 FY) or the allowance-account element (Hana).
    "credit_loss_provision": (("IS", "CIS"),
                              ("ifrs-full_ImpairmentLossImpairmentGainAndReversalOfImpairmentLossDeterminedInAccordanceWithIFRS9",
                               "dart_AdditionReversalOfCreditLossFinancialAssets",
                               "ifrs-full_AdditionalAllowanceRecognisedInProfitOrLossAllowanceAccountForCreditLossesOfFinancialAssets"),
                              ("신용손실충당금전입액", "신용손실충당금 전입액", "신용손실에대한손상차손")),
    "operating_income": (("IS", "CIS"), ("ifrs-full_ProfitLossFromOperatingActivities", "dart_OperatingIncomeLoss"),
                         ("영업이익", "영업이익(손실)")),
    "net_income": ACCOUNT_RULES["net_income"],
    "net_income_owners": (("IS", "CIS"), ACCOUNT_RULES["net_income_owners"][1],
                          ("지배기업주주지분순이익", *ACCOUNT_RULES["net_income_owners"][2])),
    "equity_owners": (("BS",), ACCOUNT_RULES["equity_owners"][1], ("지배기업주주지분", *ACCOUNT_RULES["equity_owners"][2])),
    "hybrid_capital": (("BS",), ("dart_HybridBonds",), ("신종자본증권",)),
    "equity_total": ACCOUNT_RULES["equity_total"],
    "total_assets": (("BS",), ("ifrs-full_Assets",), ("자산총계",)),
}
FHC_LABELS = {
    "net_interest_income": "순이자이익",
    "net_fee_income": "순수수료이익",
    "general_admin_expense": "일반관리비",
    "credit_loss_provision": "신용손실충당금 전입액",
    "operating_income": "영업이익",
    "net_income": "당기순이익(비지배지분 포함)",
    "net_income_owners": "지배주주순이익(신종자본증권 배당 몫 포함)",
    "equity_owners": "지배주주지분(신종자본증권 포함)",
    "hybrid_capital": "신종자본증권",
    "equity_total": "자본총계(비지배지분 포함)",
    "total_assets": "자산총계",
}
# Names and aliases agents may use for the four groups (DART's corp_name is kept as the dataset name).
FHC_ALIASES = {
    "105560": ["KB금융", "KB금융지주", "KB", "KB Financial Group"],
    "055550": ["신한지주", "신한금융지주", "신한금융", "Shinhan", "Shinhan Financial Group"],
    "086790": ["하나금융", "하나금융지주", "Hana", "Hana Financial Group"],
    "316140": ["우리금융", "우리금융지주", "Woori", "Woori Financial Group"],
}


@dataclass(frozen=True)
class Profile:
    rules: dict
    cumulative: frozenset            # income-statement accounts (cumulative in Q1 / H1 / Q3 reports)
    labels: dict | None = None       # written into the dataset as "accounts"; None = the built-in Korean table
    market: str = "kr"


PROFILES = {
    "default": Profile(ACCOUNT_RULES, frozenset(IS_ACCOUNTS)),
    "fhc": Profile(FHC_ACCOUNT_RULES, frozenset(k for k, (kinds, _, _) in FHC_ACCOUNT_RULES.items() if "BS" not in kinds),
                   FHC_LABELS, market="kr_fhc"),
}
# list.json report names (calendar fiscal year): period -> (report kind, month of the period end)
REPORT_NAMES = {"Q1": ("분기보고서", "03"), "H1": ("반기보고서", "06"), "Q3": ("분기보고서", "09"), "FY": ("사업보고서", "12")}


class DartError(RuntimeError):
    pass


def parse_amount(text) -> int | None:
    s = str(text if text is not None else "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def filed_date(rcept_no: str) -> str | None:
    s = str(rcept_no or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 and s[:8].isdigit() else None


def _profile(profile: str | Profile) -> Profile:
    if isinstance(profile, Profile):
        return profile
    if profile not in PROFILES:
        raise DartError(f"unknown account profile {profile!r}; choose {', '.join(PROFILES)}")
    return PROFILES[profile]


def _pick(rows: list[dict], rule: tuple, cumulative: bool, period: str) -> tuple[int | None, str | None]:
    kinds, ids, names = rule
    for kind in kinds:
        cands = [r for r in rows if r.get("sj_div") == kind]
        hit = next((r for i in ids for r in cands if r.get("account_id") == i), None)  # the first listed id wins
        if hit is not None:
            concept = hit["account_id"]
        else:
            hit = next((r for r in cands if str(r.get("account_nm", "")).strip() in names
                        and "포괄" not in str(r.get("account_nm", ""))
                        and "ComprehensiveIncome" not in str(r.get("account_id", ""))), None)
            concept = f"name:{str(hit.get('account_nm', '')).strip()}" if hit is not None else None
        if hit is None:
            continue
        if cumulative and period != "FY":
            value = parse_amount(hit.get("thstrm_add_amount"))
            if value is None and period == "Q1":  # Q1 cumulative == the quarter itself
                value = parse_amount(hit.get("thstrm_amount"))
            return value, concept
        return parse_amount(hit.get("thstrm_amount")), concept
    return None, None


def pick_account(rows: list[dict], account: str, period: str, profile: str | Profile = "default") -> int | None:
    """Value of one normalized account from fnlttSinglAcntAll rows (cumulative for income-statement items)."""
    p = _profile(profile)
    return _pick(rows, p.rules[account], account in p.cumulative, period)[0]


def normalize_statement(rows: list[dict], period: str, profile: str | Profile = "default") -> dict:
    p = _profile(profile)
    out = {"filed": filed_date(rows[0].get("rcept_no")) if rows else None, "rcept_no": rows[0].get("rcept_no") if rows else None}
    concepts = {}
    for account, rule in p.rules.items():
        v, concept = _pick(rows, rule, account in p.cumulative, period)
        if v is not None:
            out[account] = v
            concepts[account] = concept
    if p.labels is not None:
        out["concepts"] = concepts
    return out


def normalize_shares(rows: list[dict]) -> dict | None:
    """stockTotqySttus rows -> issued common / preferred and treasury common shares. The `se` labels vary by filer
    ("보통주", "의결권 있는 보통주", "의결권 있는\\n보통주"), so whitespace is dropped and the share class is matched by
    suffix. Quarterly reports may leave the table empty ("-"); that gives None."""
    by_kind = {}
    for r in rows:
        se = re.sub(r"\s+", "", str(r.get("se", "")))
        kind = "합계" if se == "합계" else "보통주" if se.endswith("보통주") else "우선주" if se.endswith("우선주") else None
        if kind and kind not in by_kind:
            by_kind[kind] = r
    common = by_kind.get("보통주") or {}
    total = by_kind.get("합계") or {}
    pref = by_kind.get("우선주") or {}
    n_common = parse_amount(common.get("istc_totqy"))
    n_total = parse_amount(total.get("istc_totqy"))
    if n_common is None and n_total is None:
        return None
    if n_common is None and not pref:
        n_common = n_total  # only one class of shares
    preferred = parse_amount(pref.get("istc_totqy"))
    if preferred is None and n_total is not None and n_common is not None:
        preferred = n_total - n_common
    rcept = (common or total or {}).get("rcept_no")
    return {"filed": filed_date(rcept), "rcept_no": rcept, "common": n_common, "preferred": preferred or 0,
            "treasury_common": parse_amount(common.get("tesstk_co")) or 0}


def report_form(report_nm: str) -> str:
    """'[기재정정]반기보고서 (2026.06)' -> '[기재정정]반기보고서'"""
    return re.sub(r"\s*\(\d{4}\.\d{2}\)\s*$", "", str(report_nm or "")).strip()


def find_filings(rows: list[dict], year: int, period: str) -> list[dict]:
    """The filings of one periodic report in list.json rows, oldest first: the original, then any amendments."""
    kind, month = REPORT_NAMES[period]
    pattern = re.compile(rf"^(?:\[[^\]]*\]\s*)*{kind}\s*\({year}\.{month}\)")
    return sorted((r for r in rows if pattern.match(str(r.get("report_nm", "")).strip())), key=lambda r: r["rcept_no"])


class DartClient:
    def __init__(self, api_key: str | None = None, cache_dir: str | Path = "data/finance/cache",
                 pause: float = 0.3, timeout: float = 30.0) -> None:
        self._key = api_key or os.environ.get(KEY_ENV, "").strip()
        if not self._key:
            raise DartError(f"Set the {KEY_ENV} environment variable to your own OpenDART key "
                            "(free at https://opendart.fss.or.kr). The key is never stored by this tool.")
        self.cache_dir = Path(cache_dir)
        self.pause = pause
        self.timeout = timeout

    def _get(self, endpoint: str, **params) -> bytes:
        query = urllib.parse.urlencode({"crtfc_key": self._key, **params})
        try:
            with urllib.request.urlopen(BASE_URL + endpoint + "?" + query, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:  # message deliberately excludes the URL (it contains the key)
            raise DartError(f"DART {endpoint} returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise DartError(f"Cannot reach DART ({exc.reason})") from None
        time.sleep(self.pause)
        return body

    def _data(self, endpoint: str, **params) -> dict | None:
        data = json.loads(self._get(endpoint, **params).decode("utf-8"))
        status = str(data.get("status", ""))
        if status == "013":
            return None
        if status != "000":
            raise DartError(f"DART {endpoint} status {status}: {STATUS.get(status, data.get('message', ''))}")
        return data

    def _json(self, endpoint: str, **params) -> list[dict] | None:
        data = self._data(endpoint, **params)
        return None if data is None else data.get("list") or []

    # -------------------------------------------------------------- company codes
    def corp_codes(self, refresh: bool = False) -> list[dict]:
        cache = self.cache_dir / "corp_codes.json"
        if cache.exists() and not refresh:
            return json.loads(cache.read_text(encoding="utf-8"))
        body = self._get("corpCode.xml")
        if not body.startswith(b"PK"):  # error responses come back as XML/JSON, not a zip
            raise DartError(f"DART corpCode.xml did not return a zip: {body[:200].decode('utf-8', 'replace')}")
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            root = ET.fromstring(zf.read(zf.namelist()[0]))
        rows = [{k: (el.findtext(k) or "").strip() for k in ("corp_code", "corp_name", "stock_code", "modify_date")}
                for el in root.iter("list")]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return rows

    def resolve(self, company: str) -> dict:
        q = str(company).strip()
        listed = [r for r in self.corp_codes() if r["stock_code"]]
        hits = [r for r in listed if q in (r["stock_code"], r["corp_code"], r["corp_name"])]
        if len(hits) != 1:
            near = [r["corp_name"] for r in listed if q and q in r["corp_name"]][:10]
            raise DartError(f"'{company}' matched {len(hits)} listed companies. Similar names: {', '.join(near) or '-'}")
        return hits[0]

    # -------------------------------------------------------------- reports
    def statement(self, corp_code: str, year: int, period: str, basis: str, profile: str | Profile = "default") -> dict | None:
        rows = self._json("fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=str(year),
                          reprt_code=REPORT_CODES[period], fs_div=basis)
        return normalize_statement(rows, period, profile) if rows else None

    def shares(self, corp_code: str, year: int, period: str) -> dict | None:
        rows = self._json("stockTotqySttus.json", corp_code=corp_code, bsns_year=str(year), reprt_code=REPORT_CODES[period])
        return normalize_shares(rows) if rows else None

    def filing_list(self, corp_code: str, bgn_de: str, end_de: str) -> list[dict]:
        """Periodic filings (사업·반기·분기보고서, including amendments) received between two dates (YYYYMMDD)."""
        rows, page = [], 1
        while True:
            data = self._data("list.json", corp_code=corp_code, bgn_de=bgn_de, end_de=end_de, pblntf_ty="A",
                              page_no=str(page), page_count="100")
            if data is None:
                return rows
            rows += data.get("list") or []
            if page >= int(data.get("total_page") or 1):
                return rows
            page += 1


def _mark_amendments(st: dict, filings: list[dict]) -> None:
    """Add the report form and, for an amended report, the original filing (figures stay the served version's)."""
    served = next((r for r in filings if r["rcept_no"] == st.get("rcept_no")), None)
    if served is not None:
        st["form"] = report_form(served["report_nm"])
    if filings and filings[0]["rcept_no"] != st.get("rcept_no"):
        st["first_rcept_no"] = filings[0]["rcept_no"]
        st["first_filed"] = filed_date(filings[0]["rcept_no"])


def fetch_company(client: DartClient, company: str, periods: list[str], bases=("CFS", "OFS"),
                  prices: dict[str, float] | None = None, out_dir: str | Path = "data/finance/dart",
                  log=print, profile: str = "default", price_sources: dict[str, str] | None = None,
                  aliases: list[str] | None = None, first_filings: bool = False) -> Path:
    """Fetch `periods` (e.g. ["2024:FY", "2025:H1"]) into <out_dir>/<stock_code>.json, merging with an existing file."""
    prof = _profile(profile)
    corp = client.resolve(company)
    out = Path(out_dir) / f"{corp['stock_code']}.json"
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    data.update({"name": corp["corp_name"], "stock_code": corp["stock_code"], "corp_code": corp["corp_code"],
                 "currency": "KRW", "source": "DART OpenAPI (fnlttSinglAcntAll, stockTotqySttus)"})
    if prof.labels is not None:
        data.update({"market": prof.market, "profile": profile, "fiscal_year_end": "12-31",
                     "source": "DART OpenAPI (fnlttSinglAcntAll, stockTotqySttus, list.json)",
                     "accounts": {k: {"label": v, "unit": "money"} for k, v in prof.labels.items()}})
        aliases = [*FHC_ALIASES.get(corp["stock_code"], []), *(aliases or [])] if profile == "fhc" else aliases
    if aliases:
        data["aliases"] = sorted(set(data.get("aliases", [])) | set(aliases))
    for key in ("statements", "shares", "prices"):
        data.setdefault(key, {})
    if price_sources:
        data.setdefault("price_sources", {}).update(price_sources)
    targets = []
    for item in periods:
        year, _, period = item.partition(":")
        period = period.upper()
        if not year.isdigit() or period not in PERIODS:
            raise DartError(f"bad period {item!r}; use YEAR:Q1|H1|Q3|FY")
        targets.append((int(year), period))
    listing = []
    if first_filings and targets:
        listing = client.filing_list(corp["corp_code"], f"{min(y for y, _ in targets)}0101", dt.date.today().strftime("%Y%m%d"))
    for year, period in targets:
        filings = find_filings(listing, year, period) if first_filings else []
        for basis in bases:
            if basis not in BASES:
                raise DartError(f"bad basis {basis!r}")
            st = client.statement(corp["corp_code"], year, period, basis, **({} if profile == "default" else {"profile": profile}))
            if st and first_filings:
                _mark_amendments(st, filings)
            n = len([k for k in st if k in prof.rules]) if st else 0
            note = f", first filed {st['first_filed']}" if st and st.get("first_filed") else ""
            log(f"  {corp['corp_name']} {year} {period} {basis}: " + (f"{n} accounts, filed {st['filed']}{note}" if st else "no data"))
            if st:
                data["statements"][f"{year}:{period}:{basis}"] = st
        sh = client.shares(corp["corp_code"], year, period)
        log(f"  {corp['corp_name']} {year} {period} shares: " + (f"common {sh['common']:,}" if sh and sh["common"] else "no data"))
        if sh and sh["common"]:
            data["shares"][f"{year}:{period}"] = sh
    data["prices"].update(prices or {})
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return out
