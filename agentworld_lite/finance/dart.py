"""DART OpenAPI client -> normalized company datasets (see dataset.py for the schema).

Get a free key at https://opendart.fss.or.kr (인증키 신청) and set it yourself:
    PowerShell:  $env:DART_API_KEY = "<your key>"
    bash:        export DART_API_KEY=<your key>
The key is read from the environment only; it is never printed, logged or written to disk.

Endpoints used (all JSON except corpCode.xml):
  corpCode.xml          zip of every company: corp_code, corp_name, stock_code   (cached locally)
  fnlttSinglAcntAll     full financial statements of one periodic report
  stockTotqySttus       issued / treasury share counts of one periodic report
Report codes: 11013 Q1, 11012 H1, 11014 Q3, 11011 FY (사업보고서).

Normalization:
  Income-statement accounts are taken from IS, or CIS when the company presents a single statement.
  Quarterly and half-year reports carry the current 3-month figure in `thstrm_amount` and the cumulative
  figure in `thstrm_add_amount`; the dataset stores the CUMULATIVE figure. The annual report's
  `thstrm_amount` is already the full year. Balance-sheet accounts are period-end balances.
  `filed` is the receipt date encoded in the first 8 digits of rcept_no. If the report was later amended,
  DART returns the amended report, so `filed` can be later than the original filing (a conservative as-of guard).
  Closing prices are not in DART; pass them with --price DATE=VALUE (e.g. from KRX data.krx.co.kr).
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
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


def pick_account(rows: list[dict], account: str, period: str) -> int | None:
    """Value of one normalized account from fnlttSinglAcntAll rows (cumulative for income-statement items)."""
    kinds, ids, names = ACCOUNT_RULES[account]
    for kind in kinds:
        cands = [r for r in rows if r.get("sj_div") == kind]
        hit = next((r for r in cands if r.get("account_id") in ids), None)
        if hit is None:
            hit = next((r for r in cands if str(r.get("account_nm", "")).strip() in names
                        and "포괄" not in str(r.get("account_nm", ""))), None)
        if hit is None:
            continue
        if account in IS_ACCOUNTS and period != "FY":
            value = parse_amount(hit.get("thstrm_add_amount"))
            if value is None and period == "Q1":  # Q1 cumulative == the quarter itself
                value = parse_amount(hit.get("thstrm_amount"))
            return value
        return parse_amount(hit.get("thstrm_amount"))
    return None


def normalize_statement(rows: list[dict], period: str) -> dict:
    out = {"filed": filed_date(rows[0].get("rcept_no")) if rows else None, "rcept_no": rows[0].get("rcept_no") if rows else None}
    for account in ACCOUNT_RULES:
        v = pick_account(rows, account, period)
        if v is not None:
            out[account] = v
    return out


def normalize_shares(rows: list[dict]) -> dict:
    by_kind = {str(r.get("se", "")).replace(" ", ""): r for r in rows}
    common = by_kind.get("보통주") or {}
    total = by_kind.get("합계") or {}
    pref = by_kind.get("우선주") or {}
    n_common = parse_amount(common.get("istc_totqy"))
    n_total = parse_amount(total.get("istc_totqy"))
    preferred = parse_amount(pref.get("istc_totqy"))
    if preferred is None and n_total is not None and n_common is not None:
        preferred = n_total - n_common
    rcept = (common or total or {}).get("rcept_no")
    return {"filed": filed_date(rcept), "rcept_no": rcept, "common": n_common, "preferred": preferred or 0,
            "treasury_common": parse_amount(common.get("tesstk_co")) or 0}


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

    def _json(self, endpoint: str, **params) -> list[dict] | None:
        data = json.loads(self._get(endpoint, **params).decode("utf-8"))
        status = str(data.get("status", ""))
        if status == "013":
            return None
        if status != "000":
            raise DartError(f"DART {endpoint} status {status}: {STATUS.get(status, data.get('message', ''))}")
        return data.get("list") or []

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
    def statement(self, corp_code: str, year: int, period: str, basis: str) -> dict | None:
        rows = self._json("fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=str(year),
                          reprt_code=REPORT_CODES[period], fs_div=basis)
        return normalize_statement(rows, period) if rows else None

    def shares(self, corp_code: str, year: int, period: str) -> dict | None:
        rows = self._json("stockTotqySttus.json", corp_code=corp_code, bsns_year=str(year), reprt_code=REPORT_CODES[period])
        return normalize_shares(rows) if rows else None


def fetch_company(client: DartClient, company: str, periods: list[str], bases=("CFS", "OFS"),
                  prices: dict[str, float] | None = None, out_dir: str | Path = "data/finance/dart",
                  log=print) -> Path:
    """Fetch `periods` (e.g. ["2024:FY", "2025:H1"]) into <out_dir>/<stock_code>.json, merging with an existing file."""
    corp = client.resolve(company)
    out = Path(out_dir) / f"{corp['stock_code']}.json"
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    data.update({"name": corp["corp_name"], "stock_code": corp["stock_code"], "corp_code": corp["corp_code"],
                 "currency": "KRW", "source": "DART OpenAPI (fnlttSinglAcntAll, stockTotqySttus)"})
    data.setdefault("statements", {})
    data.setdefault("shares", {})
    data.setdefault("prices", {})
    for item in periods:
        year, period = item.split(":")
        period = period.upper()
        if period not in PERIODS:
            raise DartError(f"bad period {item!r}; use YEAR:Q1|H1|Q3|FY")
        for basis in bases:
            if basis not in BASES:
                raise DartError(f"bad basis {basis!r}")
            st = client.statement(corp["corp_code"], int(year), period, basis)
            log(f"  {corp['corp_name']} {year} {period} {basis}: " + (f"{len(st) - 2} accounts, filed {st['filed']}" if st else "no data"))
            if st:
                data["statements"][f"{year}:{period}:{basis}"] = st
        sh = client.shares(corp["corp_code"], int(year), period)
        log(f"  {corp['corp_name']} {year} {period} shares: " + (f"common {sh['common']:,}" if sh and sh["common"] else "no data"))
        if sh:
            data["shares"][f"{year}:{period}"] = sh
    data["prices"].update(prices or {})
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return out
