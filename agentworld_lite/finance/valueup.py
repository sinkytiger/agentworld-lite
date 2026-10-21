"""밸류업 약속 점검: 4대 금융지주가 기업가치 제고 계획에 적은 목표와, 공시만으로 잰 진척도. 언어 모델은 쓰지 않는다.

    python -m agentworld_lite.finance valueup --build      # DART에서 근거를 받아 data/finance/valueup/valueup.json 생성
    python -m agentworld_lite.finance valueup --out valueup.md

Targets are never inferred. Each one is a fixed quote (TARGETS below) from a value-up plan filed on DART, and `build`
downloads that filing's original document (document.xml) and refuses to write the data file unless every quote appears
in the document text verbatim (whitespace and table separators collapsed). The same check covers the CET1 figures,
which are quoted from each group's 2026 H1 report because DART's financial statements do not carry capital ratios, and
the progress figures a group reported about itself. Progress is measured from filings only:

  ROE        TTM net income to owners / average owners' equity at the start and end of the twelve months (data set).
  CET1       the group's own figure in its 2026 H1 report (capital adequacy table), quoted with its receipt number.
  주주환원율  (cash dividends of the fiscal year, from the dividend API alotMatter, + buybacks the board decided in that
             calendar year, from the major-matter APIs tsstkAqDecsn and tsstkAqTrctrCnsDecsn, at the decided amount)
             / net income to owners of that year.
  주식 수     issued common shares in the half-year report (stockTotqySttus).

A target is 달성 when the current value meets it, 순항 when it does not yet but the improvement over the last twelve months
is at least the yearly pace the stated deadline requires, 지연 when it is slower, and 미달 when no deadline is stated.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path

from .catalog import FHC
from .dart import DartClient, DartError, filed_date, parse_amount
from .dataset import Dataset, Fact, share_facts, statement_facts
from .env import REPO_ROOT
from .note_ko import cite_facts_ko, cited_ko, dart_link, uncited_figures_ko

DATA_DIR = REPO_ROOT / "data" / "finance" / "dart_fhc"
VALUEUP_FILE = REPO_ROOT / "data" / "finance" / "valueup" / "valueup.json"  # not in DATA_DIR: Dataset reads every JSON there
AS_OF = "2026-10-15"
CODES = tuple(FHC.values())
NAMES = {"105560": "KB금융", "055550": "신한지주", "086790": "하나금융지주", "316140": "우리금융지주"}
METRIC_KO = {"roe": "ROE", "cet1": "CET1 비율", "payout": "주주환원율", "shares": "발행주식 수", "policy": "주주환원 방식"}
NOTICE = "교육용 예시·투자 권유 아님"


class ValueupError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    code: str
    metric: str                  # roe | cet1 | payout | shares | policy
    threshold: float | None      # percent for ratios, shares for shares; None for a policy without a number
    direction: str               # ">=", "<=" or "policy"
    deadline: str | None         # ISO date the target is due, only when the quoted wording names one
    rcept_no: str                # the value-up plan (or report) the quote comes from
    quote: str                   # exact wording, as it appears in the document text


TARGETS = [
    Target("105560", "roe", 10.0, ">=", None, "20241024800258", "1. Target ROE : 10% 이상(Double digit)"),
    Target("105560", "cet1", 13.0, ">=", None, "20241024800258", "2. Target CET1비율 : 13% 이상"),
    Target("105560", "policy", None, "policy", None, "20241024800258",
           "1차 주주환원 결정 : 연말 CET1비율 13%를 초과하는 자본에 상응하는 금액을 다음 연도 주주환원 재원으로 활용"),
    Target("055550", "roe", 10.0, ">=", "2027-12-31", "20260423800235",
           "2027년을 달성 기한으로 다음의 핵심 목표를 설정하였습니다. ① ROE 10%, ② 주주환원율 50%, ③ 발행주식수 50백만주 감축"),
    Target("055550", "payout", 50.0, ">=", "2027-12-31", "20260423800235",
           "2027년을 달성 기한으로 다음의 핵심 목표를 설정하였습니다. ① ROE 10%, ② 주주환원율 50%, ③ 발행주식수 50백만주 감축"),
    Target("055550", "cet1", 13.0, ">=", None, "20260423800235", "③ CET1비율 13%+(규제,시장 환경을 고려한 안정적 관리)"),
    Target("055550", "shares", 450_000_000, "<=", "2027-12-31", "20250425800271", "2027년말 450백만주까지 감축함으로써"),
    Target("086790", "roe", 12.0, ">=", None, "20260724800373", "목표 1) ROE 12%"),
    Target("086790", "payout", 50.0, ">=", None, "20260724800373", "목표 2) 주주환원율 50% 이상"),
    Target("086790", "cet1", 13.0, ">=", None, "20260724800373", "목표 3) 보통주자본비율 13% 이상"),
]
# The group's own CET1 at 2026-06-30 as printed in its 2026 H1 report; `index` picks the column of the quoted row.
CET1 = {
    "105560": {"rcept_no": "20260814004200", "quote": "보통주자본비율(c/f) 13.74 13.82 13.53", "index": 0,
               "note": "[KB금융지주] BIS기준 자기자본비율 표, 2026년 반기말 열(잠정)"},
    "055550": {"rcept_no": "20261007000223",
               "quote": "보통주자본비율 13.11% 13.06% 13.17% 13.02% 13.26% 13.62% 13.56% 13.35% 13.30% 13.43%", "index": -1,
               "note": "조건부자본증권 항목의 자본적정성 지표 추이 표, 26년6월말 열"},
    "086790": {"rcept_no": "20260814003800", "quote": "보통주자본비율 13.27% 2.30% 10.97%", "index": 0,
               "note": "금융지주회사 감독규정 제43조에 따른 자본여력 표, 2026년 2분기말 열(잠정)"},
}
# Woori's 2026 H1 report gives only the total BIS ratio for the group; its CET1 appears only for 2025, in prose.
CET1_NOT_REPORTED = {"316140": {"rcept_no": "20260814003410", "quote": "보통주자본비율 12.89% 달성",
                                "note": "2026년 반기말 CET1은 반기보고서에 없음(그룹 표는 BIS자기자본비율만 제시). "
                                        "인용한 값은 2025년 말 수치로, 임원 보수 산정 설명에 있음"}}
# What the groups said about their own progress, quoted for the cross-check.
COMPANY_REPORTED = [
    {"code": "055550", "year": 2025, "rcept_no": "20260423800235",
     "quote": "ROE는 전년 대비 67bp 개선된 9.11%, 주주환원율은 Target 50%를 초과한 50.2%", "roe": 9.11, "payout": 50.2},
    {"code": "055550", "year": 2024, "rcept_no": "20250425800271",
     "quote": "2024년 재무실적 : ROE 8.4%, ROTCE 9.6%, CET1비율 13.06%, 주주환원율 40.2%", "roe": 8.4, "payout": 40.2},
]
DEADLINE_NOTE = {"086790": {"roe": ("20260814003800", "중장기적으로 12% 달성을 목표로 합니다")}}
NOT_QUOTABLE = {"316140": "우리금융지주의 기업가치 제고 계획 공시(2026-02-06 등)는 본문에 목차만 있고 수치 목표는 첨부 PDF에 "
                          "있습니다. DART OpenAPI 원문(document.xml)으로는 첨부를 받을 수 없어 목표를 인용하지 않았습니다."}


# ---------------------------------------------------------------------- documents and quotes
def document_text(zip_bytes: bytes) -> str:
    """Plain text of a DART original-document zip (document.xml): tags dropped, entities decoded, one cell per line."""
    texts = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            raw = zf.read(name)
            for enc in ("utf-8", "cp949", "euc-kr"):
                try:
                    s = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                continue
            s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
            s = re.sub(r"<br\s*/?>|</p>|</tr>|</td>|</title>|</span>", "\n", s, flags=re.I)
            s = unescape(re.sub(r"<[^>]+>", " ", s))
            texts.append(s)
    return "\n".join(texts)


def normalize(text: str) -> str:
    """Collapse whitespace and table-cell separators, so a quote matches however the document laid it out."""
    return re.sub(r"[\s| ]+", " ", text).strip()


def check_quotes(text: str, quotes: list[str]) -> list[str]:
    """Quotes that do not appear in the document text."""
    t = normalize(text)
    return [q for q in quotes if normalize(q) not in t]


def _iso(korean_date: str) -> str | None:
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(korean_date or ""))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


# ---------------------------------------------------------------------- build (network)
def build(client: DartClient, out: str | Path = VALUEUP_FILE, as_of: str = AS_OF, log=print) -> Path:
    """Fetch every source the scorecard needs from DART and write it, after checking each quote against its document."""
    docs: dict[str, list[str]] = {}
    for t in TARGETS:
        docs.setdefault(t.rcept_no, []).append(t.quote)
    for c in [*CET1.values(), *CET1_NOT_REPORTED.values(), *COMPANY_REPORTED]:
        docs.setdefault(c["rcept_no"], []).append(c["quote"])
    for notes in DEADLINE_NOTE.values():
        for rcept, quote in notes.values():
            docs.setdefault(rcept, []).append(quote)
    documents = {}
    for rcept, quotes in sorted(docs.items()):
        text = document_text(client._get("document.xml", rcept_no=rcept))
        missing = check_quotes(text, quotes)
        if missing:
            raise ValueupError(f"{rcept}: quote not found in the document: {missing[0]!r}")
        documents[rcept] = {"filed": filed_date(rcept), "sha256": hashlib.sha256(normalize(text).encode()).hexdigest(),
                            "quotes_checked": len(quotes)}
        log(f"  {rcept}: {len(quotes)} quote(s) found")
    end = as_of.replace("-", "")
    data = {"as_of": as_of, "documents": documents, "plans": {}, "history": {}, "dividends": {}, "buybacks": {}}
    for code in CODES:
        corp = client.resolve(code)["corp_code"]
        rows = client.filing_list(corp, "20240101", end, kind="I")  # exchange filings, where the plans are
        data["plans"][code] = [{"rcept_no": r["rcept_no"], "filed": filed_date(r["rcept_no"]),
                                "report": re.sub(r"\s+", " ", r["report_nm"]).strip()}
                               for r in rows if "기업가치" in r["report_nm"]]
        hist = {}
        for period in ("H1", "FY"):
            st = client.statement(corp, 2024, period, "CFS", profile="fhc")
            if st:
                hist[f"2024:{period}"] = {k: st[k] for k in ("rcept_no", "filed", "net_income_owners", "net_income",
                                                              "equity_owners", "hybrid_capital", "equity_total") if k in st}
        data["history"][code] = hist
        div = {}
        for year in (2024, 2025):
            for r in client._json("alotMatter.json", corp_code=corp, bsns_year=str(year), reprt_code="11011") or []:
                if str(r.get("se", "")).replace(" ", "") == "현금배당금총액(백만원)":
                    div[str(year)] = {"amount": parse_amount(r.get("thstrm")) * 1_000_000, "rcept_no": r["rcept_no"],
                                      "filed": filed_date(r["rcept_no"])}
        data["dividends"][code] = div
        buys = []
        for r in client._json("tsstkAqDecsn.json", corp_code=corp, bgn_de="20240101", end_de=end) or []:
            buys.append({"decided": _iso(r.get("aq_dd")), "kind": "직접 취득", "amount": parse_amount(r.get("aqpln_prc_ostk")),
                         "rcept_no": r["rcept_no"], "purpose": re.sub(r"\s+", " ", str(r.get("aq_pp", ""))).strip()})
        for r in client._json("tsstkAqTrctrCnsDecsn.json", corp_code=corp, bgn_de="20240101", end_de=end) or []:
            buys.append({"decided": _iso(r.get("bddd")), "kind": "신탁계약", "amount": parse_amount(r.get("ctr_prc")),
                         "rcept_no": r["rcept_no"], "purpose": re.sub(r"\s+", " ", str(r.get("ctr_pp", ""))).strip()})
        data["buybacks"][code] = sorted(buys, key=lambda b: (b["decided"] or "", b["rcept_no"]))
        log(f"  {NAMES[code]}: {len(data['plans'][code])} plan filings, {len(buys)} buyback decisions")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return out


# ---------------------------------------------------------------------- measures (offline)
def _fact(fid: str, label: str, value: float, unit: str, rcept_no: str, **meta) -> Fact:
    return Fact(fid, label, float(value), unit, {"source": "DART", "rcept_no": rcept_no, "filed": filed_date(rcept_no),
                                                 **meta})


def _hist_fact(vu: dict, code: str, key: str, account: str) -> Fact:
    h = vu["history"][code][key]
    year, period = key.split(":")
    return _fact(f"{code}.{year}{period}.CFS.{account}", f"{NAMES[code]} {year} {period} {account}", h[account], "KRW",
                 h["rcept_no"], company=code, year=int(year), period=period, basis="CFS", account=account)


def target_fact(t: Target) -> Fact:
    unit = "shares" if t.metric == "shares" else "%"
    return _fact(f"{t.code}.target.{t.metric}", f"{NAMES[t.code]} 기업가치 제고 계획 {METRIC_KO[t.metric]} 목표",
                 t.threshold or 0, unit, t.rcept_no)


def cet1_fact(code: str) -> Fact | None:
    c = CET1.get(code)
    if c is None:
        return None
    values = [float(x) for x in re.findall(r"(\d+\.\d+)%?", c["quote"])]
    return _fact(f"{code}.cet1.2026H1", f"{NAMES[code]} 보통주자본비율(2026-06-30)", values[c["index"]], "%", c["rcept_no"])


def _stmt(ds: Dataset, code: str, year: int, period: str) -> dict[str, Fact]:
    return {f.meta["account"]: f for f in statement_facts(ds.companies[code], year, period, "CFS")}


def measures(ds: Dataset, vu: dict) -> dict:
    """Per group: each measure as (value, unit, facts), now and a year earlier."""
    out = {}
    for code in CODES:
        h26, h25, fy25 = _stmt(ds, code, 2026, "H1"), _stmt(ds, code, 2025, "H1"), _stmt(ds, code, 2025, "FY")
        ni, eq = "net_income_owners", "equity_owners"
        fy24, h24 = _hist_fact(vu, code, "2024:FY", ni), _hist_fact(vu, code, "2024:H1", ni)
        eq24 = _hist_fact(vu, code, "2024:H1", eq)
        ttm_now = [fy25[ni], h25[ni], h26[ni]]
        ttm_prev = [fy24, h24, h25[ni]]
        roe = lambda ttm, e0, e1: (ttm[0].value - ttm[1].value + ttm[2].value) / ((e0.value + e1.value) / 2) * 100
        m = {"roe": ((roe(ttm_now, h25[eq], h26[eq]), "%", [*ttm_now, h25[eq], h26[eq]]),
                     (roe(ttm_prev, eq24, h25[eq]), "%", [*ttm_prev, eq24, h25[eq]]))}
        pay = []
        for year, ni_fact in ((2025, fy25[ni]), (2024, fy24)):
            d = vu["dividends"][code][str(year)]
            div = _fact(f"{code}.dividends.FY{year}", f"{NAMES[code]} {year} 사업연도 현금배당금 총액", d["amount"], "KRW",
                        d["rcept_no"])
            buys = [_fact(f"{code}.buyback.{b['rcept_no']}", f"{NAMES[code]} 자기주식 {b['kind']} 결정({b['decided']})",
                          b["amount"], "KRW", b["rcept_no"], short=f"{b['kind']}({b['decided']})")
                    for b in vu["buybacks"][code] if (b["decided"] or "").startswith(str(year))]
            total = div.value + sum(b.value for b in buys)
            pay.append((total / ni_fact.value * 100, "%", [div, *buys, ni_fact], {"dividends": div, "buybacks": buys,
                                                                                  "net_income": ni_fact}))
        m["payout"] = tuple(pay)
        eq24fy = _hist_fact(vu, code, "2024:FY", eq)  # fiscal-year ROE on year-start and year-end equity, for the cross-check
        m["roe_fy2025"] = (fy25[ni].value / ((eq24fy.value + fy25[eq].value) / 2) * 100, "%", [fy25[ni], eq24fy, fy25[eq]])
        shares = {y: next((f for f in share_facts(ds.companies[code], y, "H1") if f.id.endswith("shares_common")), None)
                  for y in (2026, 2025)}  # KB left the 2025 H1 share table empty
        m["shares"] = tuple((f.value, "shares", [f]) if f else None for f in (shares[2026], shares[2025]))
        c = cet1_fact(code)
        m["cet1"] = ((c.value, "%", [c]), None) if c else (None, None)
        out[code] = m
    return out


def judge(t: Target, now: float | None, prev: float | None, as_of: str = "2026-06-30") -> tuple[str, float | None, float | None]:
    """(status, gap still to go, yearly pace the deadline requires); gap and pace are None where they do not apply."""
    if t.direction == "policy" or now is None:
        return ("정량 목표 없음" if t.direction == "policy" else "측정 불가"), None, None
    gap = (t.threshold - now) if t.direction == ">=" else (now - t.threshold)
    if gap <= 0:
        return "달성", 0.0, None
    if t.deadline is None:
        return "미달(기한 미명시)", gap, None
    years = (dt.date.fromisoformat(t.deadline) - dt.date.fromisoformat(as_of)).days / 365.25
    if years <= 0:
        return "미달(기한 경과)", gap, None
    need = gap / years
    if prev is None:
        return "판단 불가", gap, need
    pace = (now - prev) if t.direction == ">=" else (prev - now)
    return ("순항" if pace >= need else "지연"), gap, need


def load(path: str | Path = VALUEUP_FILE) -> dict:
    p = Path(path)
    if not p.exists():
        raise ValueupError(f"{p} not found; build it with `python -m agentworld_lite.finance valueup --build` "
                           "(needs DART_API_KEY)")
    return json.loads(p.read_text(encoding="utf-8"))


def scorecard_rows(data_dir: str | Path | None = None, path: str | Path | None = None) -> list[dict]:
    """One row per target, with the measure now and a year earlier and the verdict. Used by the demo and the tests."""
    m = measures(Dataset(Path(data_dir) if data_dir else DATA_DIR), load(path or VALUEUP_FILE))
    rows = []
    for t in TARGETS:
        now, prev = m[t.code].get(t.metric, (None, None)) if t.metric != "policy" else (m[t.code]["payout"][0], None)
        status, gap, need = judge(t, now[0] if now else None, prev[0] if prev else None)
        rows.append({"target": t, "now": now, "prev": prev, "status": status, "gap": gap, "need": need})
    return rows


# ---------------------------------------------------------------------- the scorecard (Korean Markdown)
def _cite_only(fact: Fact) -> str:
    return cite_facts_ko([fact])


def _gap_unit(t: Target) -> str:
    return "shares" if t.metric == "shares" else "pp"


def render_scorecard(data_dir: str | Path | None = None, path: str | Path | None = None, as_of: str = AS_OF) -> str:
    rows = scorecard_rows(data_dir, path)
    vu = load(path or VALUEUP_FILE)
    m = measures(Dataset(Path(data_dir) if data_dir else DATA_DIR), vu)
    out = [
        "# 밸류업 약속 점검: 4대 금융지주", "",
        f"> **{NOTICE}.** 공시만으로 고정된 규칙에 따라 만든 점검표이며, 언어 모델이 쓰지 않았습니다. 목표는 각 그룹이 DART에 "
        "낸 기업가치 제고 계획의 문구를 그대로 인용했고, 추론한 목표는 없습니다.", "",
        f"- **기준일:** {as_of}. 진척도는 2026년 6월 말(반기) 기준이고, 1년 전 값은 2025년 6월 말(주주환원율은 2024 사업연도)입니다.",
        "- **인용:** 모든 수치 뒤에 사실 ID와 DART 접수번호를 붙였습니다. 목표 문구는 따옴표 안에 원문 그대로 적고 접수번호를 "
        "붙였습니다. 금액은 억원 미만에서 반올림했습니다.", "",
        "## 요약", "",
        "| 금융지주 | 지표 | 목표 | 기한 | 현재 | 1년 전 | 남은 격차 | 판정 |", "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        t = r["target"]
        tf = target_fact(t)
        target_cell = (f"정량 목표 없음(공식) {_cite_only(tf)}" if t.direction == "policy" else
                       f"{cited_ko(tf.value, tf.unit, [tf])} {'이하' if t.direction == '<=' else '이상'}")
        now = cited_ko(*r["now"][:2], r["now"][2]) if r["now"] else "-"
        prev = cited_ko(*r["prev"][:2], r["prev"][2]) if r["prev"] else "-"
        if t.metric == "policy":
            now = f"주주환원율 {now}"
            prev = "-"
        gap = "-" if not r["gap"] else cited_ko(r["gap"], _gap_unit(t), [tf, *(r["now"][2])])
        deadline = t.deadline[:4] + "년" if t.deadline else "명시 없음"
        out.append(f"| {NAMES[t.code]} | {METRIC_KO[t.metric]} | {target_cell} | {deadline} | {now} | {prev} | {gap} | "
                   f"**{r['status']}** |")
    out += ["", f"우리금융지주: {NOT_QUOTABLE['316140']}", ""]

    out += ["## 그룹별 목표 원문과 진척", ""]
    for code in CODES:
        out += [f"### {NAMES[code]}({code})", ""]
        mine = [r for r in rows if r["target"].code == code]
        if not mine:
            out.append(f"- 목표: 인용할 수 있는 수치 목표 없음. {NOT_QUOTABLE.get(code, '')}")
        for r in mine:
            t = r["target"]
            tf = target_fact(t)
            out.append(f"- **{METRIC_KO[t.metric]}.** “{t.quote}” {_cite_only(tf)}")
            if t.code in DEADLINE_NOTE and t.metric in DEADLINE_NOTE[t.code]:
                rc, quote = DEADLINE_NOTE[t.code][t.metric]
                note = _fact(f"{t.code}.target.{t.metric}.horizon", "기한 관련 문구", 0, "", rc)
                out.append(f"  - 기한 관련 문구: “{quote}” {_cite_only(note)}")
            if r["now"]:
                line = f"  - 현재 {cited_ko(*r['now'][:2], r['now'][2])}"
                if r["prev"]:
                    line += f", 1년 전 {cited_ko(*r['prev'][:2], r['prev'][2])}"
                out.append(line + f" → **{r['status']}**.")
            if r["need"] is not None and r["prev"]:
                pace = (r["now"][0] - r["prev"][0]) if t.direction == ">=" else (r["prev"][0] - r["now"][0])
                facts = [tf, *r["now"][2], *r["prev"][2]]
                out.append(f"  - 기한까지 남은 격차를 메우려면 연간 {cited_ko(r['need'], _gap_unit(t), facts)}씩 나아져야 하고, "
                           f"최근 1년 동안은 {cited_ko(pace, _gap_unit(t), facts)} 나아졌습니다.")
        if code not in CET1 and code in CET1_NOT_REPORTED:
            c = CET1_NOT_REPORTED[code]
            ref = _fact(f"{code}.cet1.2025FY", f"{NAMES[code]} 보통주자본비율(2025년 말)", 12.89, "%", c["rcept_no"])
            out.append(f"- **CET1 비율(참고).** {c['note']}: “{c['quote']}” {_cite_only(ref)}")
        pay = m[code]["payout"][0]
        div, buys, ni = pay[3]["dividends"], pay[3]["buybacks"], pay[3]["net_income"]
        detail = ", ".join(f"{b.meta['short']} {cited_ko(b.value, 'KRW', [b])}" for b in buys) or "없음"
        out.append(f"- **2025년 주주환원 구성(참고).** 현금배당 {cited_ko(div.value, 'KRW', [div])}, 자기주식 결정 {detail}, "
                   f"지배주주순이익 {cited_ko(ni.value, 'KRW', [ni])} → 주주환원율 {cited_ko(pay[0], '%', pay[2])}.")
        out.append("")

    out += ["## 회사가 밝힌 수치와 대조", ""]
    for c in COMPANY_REPORTED:
        code, year = c["code"], c["year"]
        rep = _fact(f"{code}.reported.{year}", f"{NAMES[code]} {year}년 자체 발표", c["roe"], "%", c["rcept_no"])
        idx = 0 if year == 2025 else 1
        ours_pay = m[code]["payout"][idx]
        line = f"- {NAMES[code]} {year}년: “{c['quote']}” {_cite_only(rep)}. 이 점검표의 {year}년 주주환원율은 " \
               f"{cited_ko(ours_pay[0], '%', ours_pay[2])}"
        if year == 2025:
            line += f", 같은 사업연도 ROE(연초·연말 지배주주지분 평균)는 {cited_ko(*m[code]['roe_fy2025'])}"
        out.append(line + "입니다.")
    out += [
        "- 차이가 나는 이유: 이 점검표는 자기주식을 **이사회가 결정한 금액**으로, **결의일이 속한 해**에 넣습니다. 회사는 실제 매입액을 "
        "쓰거나, 다음 해 초에 결정한 매입을 직전 사업연도 환원으로 셉니다. ROE도 회사마다 평균 자본 산정 방식(분기 평균 등)이 다릅니다.",
        "",
        "## 측정 방법", "",
        "- **ROE** = TTM 지배주주순이익 ÷ 평균 지배주주지분. TTM = 전년 연간 − 전년 상반기 + 올해 상반기, 평균 = 12개월의 시작"
        "(전년 6월 말)과 끝(올해 6월 말) 잔액의 평균. 연결 재무제표, DART 전체 재무제표 API.",
        "- **CET1 비율** = 각 그룹이 반기보고서 본문에 적은 그룹 보통주자본비율(잠정치 포함). DART 재무제표 API에는 자본비율이 "
        "없어서 반기보고서 원문의 해당 표를 인용했습니다.",
        "- **주주환원율** = (그 사업연도 현금배당금 총액 + 그 해에 이사회가 결정한 자기주식 취득·신탁계약 금액) ÷ 그 해 "
        "지배주주순이익. 배당은 배당 API(alotMatter), 자기주식은 주요사항보고서 API(자기주식 취득 결정, 자기주식취득 신탁계약 "
        "체결 결정)에서 읽었습니다. 결정 금액은 실제 매입액과 다를 수 있습니다.",
        "- **판정:** 현재 값이 목표를 충족하면 **달성**. 아직이면, 기한이 명시된 경우 남은 격차를 남은 기간으로 나눈 연간 필요 "
        "개선폭과 최근 1년 개선폭을 비교해 같거나 크면 **순항**, 작으면 **지연**. 기한이 명시되지 않았으면 **미달(기한 미명시)**.",
        "",
        "## 고지", "",
        "이 문서는 공시만으로 약속과 진척을 대조하는 작업 방식을 보여 주는 교육용 예시입니다. 투자 권유가 아니고, 어떤 증권의 "
        "거래도 권하지 않으며, 투자의견과 목표주가를 제시하지 않습니다. 이용하기 전에 인용한 공시를 확인하십시오.", "",
        "## 출처", "", "| 접수번호 | 접수일 | 내용 |", "|---|---|---|",
    ]
    for rcept, d in sorted(vu["documents"].items()):
        out.append(f"| {dart_link(rcept)} | {d['filed']} | 원문 대조(문구 {d['quotes_checked']}개 확인) |")
    out.append("")
    md = "\n".join(out).rstrip() + "\n"
    bad = uncited_figures_ko(md)
    if bad:
        raise ValueupError(f"uncited figures in the scorecard: {', '.join(bad[:5])}")
    return md
