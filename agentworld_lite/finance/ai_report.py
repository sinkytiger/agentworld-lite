"""AI 리포트와 팩트체크: 같은 질문을 언어 모델에 두 번 묻고(자료 없이, DART 데이터 팩과 함께), 답의 숫자를 모두 공시와 대조한다.

    python -m agentworld_lite.finance ai-report --prompts                 # 프롬프트만 저장(모델 호출 없음)
    python -m agentworld_lite.finance ai-report --run --max-cost 1.0      # 유료: 모델 호출 2회(ANTHROPIC_API_KEY)
    python -m agentworld_lite.finance ai-report --check                   # 저장한 답을 다시 검사(오프라인)

Both prompts ask the same question about the four groups' Q2 2026 results and value-up progress, with the same
definitions. The data prompt adds a data pack (statement facts, share counts, CET1 ratios, value-up targets, dividends
and buyback decisions, each with its DART receipt number) and asks for a citation after every figure: [사실 `id`] for a
pack value, [계산: expression] for a computed one. Everything lands in data/finance/ai_report: the system prompt, both
prompts, both answers, the call record (model, usage, cost) and the fact-check.

Every figure in an answer (amounts in 원·억·조, 주, %, %p, bp, 배; bare numbers in a table whose header gives the unit)
is checked twice:

  citation  a figure followed by [사실 `id`] or [계산: ...] is recomputed from the data pack and must equal the figure to
            its written precision (rounding or truncation): 인용 일치, 인용 불일치, 인용 오류 (unknown id, bad formula).
  context   the group named nearest before the figure (or in its table row), the metric, period and year words before it
            in its line (or the table's column header), and a nearby 목표 pick candidates from a reference set built from
            the filings: raw statement figures, standalone quarters, growth rates, CIR, common equity, ROE under the usual
            definitions, CET1, payout, share counts, value-up targets, dividends, buybacks, Shinhan's own reported
            figures, gaps and changes in percentage points, and sums over the four groups. Verdicts: 일치, 기준 다름 (the
            value belongs to another period or year), 불일치 (the nearest candidate is shown), 지표 불명 (no metric word;
            the value was looked up in the group's figures), 확인 불가 (nothing to compare with).

No language model judges anything here; the model only writes the two answers.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import valueup as vu
from .dataset import Dataset, Fact, share_facts, statement_facts
from .env import REPO_ROOT
from .note_ko import fmt_krw

OUT_DIR = REPO_ROOT / "data" / "finance" / "ai_report"
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 8000
CODES, NAMES = vu.CODES, vu.NAMES
FLOWS = ("net_interest_income", "net_fee_income", "general_admin_expense", "credit_loss_provision", "operating_income",
         "net_income", "net_income_owners")
ACCOUNT_KO = {"net_interest_income": "순이자이익", "net_fee_income": "순수수료이익", "general_admin_expense": "일반관리비",
              "credit_loss_provision": "신용손실충당금 전입액", "operating_income": "영업이익",
              "net_income": "당기순이익(비지배지분 포함)", "net_income_owners": "지배주주순이익",
              "equity_owners": "지배주주지분", "hybrid_capital": "신종자본증권", "equity_total": "자본총계",
              "total_assets": "자산총계", "common_equity": "보통주자본"}
PERIOD_KO = {"Q1": "1분기", "Q2": "2분기", "H1": "상반기", "Q3": "3분기", "9M": "3분기 누적", "Q4": "4분기", "FY": "연간"}
PERIOD_END = {"Q1": "3월 말", "H1": "6월 말", "Q3": "9월 말", "FY": "12월 말"}
VARIANTS = ("nodata", "data")


class AIReportError(Exception):
    pass


# ---------------------------------------------------------------------- prompts
SYSTEM = ("당신은 한국 은행·금융지주를 담당하는 리서치 애널리스트입니다. 수치의 정확성과 근거가 가장 중요합니다. "
          "확인할 수 없는 수치는 쓰지 않습니다.")
QUESTION = """기준일 2026-10-15. 4대 금융지주(KB금융 105560, 신한지주 055550, 하나금융지주 086790, 우리금융지주 316140)의 2026년 2분기 실적과 기업가치 제고(밸류업) 계획의 진척을 정리한 한국어 리포트를 마크다운으로 써 주세요. 분량은 A4 2쪽 이내입니다.

다룰 내용
1. 그룹별 2026년 2분기(4~6월) 지배주주순이익과 전년 같은 분기 대비 증감률, 4개 그룹 합산
2. 비용 효율: 2026년 상반기 영업이익경비율(CIR)
3. 자본: 2026년 6월 말 보통주자본비율(CET1 비율)
4. 밸류업 목표(ROE, CET1 비율, 주주환원율 등)와 현재 수준: 그룹별로 달성, 순항, 지연 중 어디에 해당하는지
5. 종합

지켜 주세요
- 투자의견, 목표주가, 주가는 쓰지 마세요.
- 모르는 수치는 추측하지 말고 "확인 필요"라고 적으세요.
- 정의: 2분기 단독 = 상반기 누적 − 1분기 누적. CIR = 일반관리비 ÷ (영업이익 + 일반관리비 + 신용손실충당금 전입액) × 100. ROE = 최근 12개월 지배주주순이익 ÷ 평균 지배주주지분 × 100. 주주환원율 = (현금배당 + 자기주식 매입) ÷ 지배주주순이익 × 100."""
NODATA_TAIL = "자료는 따로 주지 않습니다. 알고 있는 정보로 쓰고, 수치마다 근거(어떤 보고서나 발표인지)를 괄호 안에 밝혀 주세요."
DATA_TAIL = """아래 데이터 팩은 각 그룹이 DART에 낸 공시에서 읽은 사실입니다. 이 데이터만 근거로 쓰세요.
수치마다 바로 뒤에 근거를 붙이세요. 데이터 팩의 값을 그대로 쓰면 [사실 `id`], 계산한 값이면 [계산: 식]입니다. 식에는 백틱으로 감싼 사실 id, 숫자, + - * / 괄호만 씁니다. 예: 1조 9,922억원 [계산: `105560.2026H1.CFS.net_income_owners` - `105560.2026Q1.CFS.net_income_owners`]. 식 안에서 금액 사실의 값은 원 단위이고, 비율 사실은 % 숫자(예: 13.74)입니다. 손익은 1월부터의 누적입니다."""


def data_pack(data_dir: str | Path | None = None, vu_path: str | Path | None = None) -> list[tuple[Fact, str]]:
    """(fact, note) rows for the data prompt; the note carries a target's quoted wording and deadline."""
    ds = Dataset(Path(data_dir) if data_dir else vu.DATA_DIR)
    data = vu.load(vu_path or vu.VALUEUP_FILE)
    rows: list[tuple[Fact, str]] = []
    for code in CODES:
        c = ds.companies[code]
        for y, p in ((2025, "Q1"), (2025, "H1"), (2025, "FY"), (2026, "Q1"), (2026, "H1")):
            rows += [(f, "") for f in statement_facts(c, y, p, "CFS")]
        rows += [(vu._hist_fact(data, code, k, a), "") for k in ("2024:H1", "2024:FY")
                 for a in ("net_income_owners", "equity_owners")]
        for y in (2025, 2026):
            rows += [(f, "") for f in share_facts(c, y, "H1") if f.id.endswith(("shares_common", "shares_treasury_common"))]
        for cet1 in (vu.cet1_fact(code), vu.cet1_reference_fact(code)):
            if cet1:
                rows.append((cet1, "그룹이 반기보고서 본문에 적은 값" if cet1.id.endswith("2026H1") else "2025년 말 값(참고)"))
        for t in vu.TARGETS:
            if t.code == code and t.threshold is not None:
                rows.append((vu.target_fact(t), f"원문: “{t.quote}”, 기한: {t.deadline[:4] + '년' if t.deadline else '명시 없음'}"))
        for year in (2024, 2025):
            rows.append((vu.dividend_fact(data, code, year), ""))
            rows += [(f, "이사회 결정 금액") for f in vu.buyback_facts(data, code, year)]
    return rows


def _pack_value(f: Fact) -> str:
    if f.unit == "KRW":
        return f"{fmt_krw(f.value)} ({f.value / 1e6:,.0f}백만원)"
    if f.unit == "shares":
        return f"{f.value:,.0f}주"
    return f"{f.value:g}%"


def pack_markdown(rows: list[tuple[Fact, str]]) -> str:
    out = ["| 사실 id | 내용 | 값 | 접수번호 | 비고 |", "|---|---|---|---|---|"]
    out += [f"| `{f.id}` | {f.label} | {_pack_value(f)} | {f.meta.get('rcept_no', '')} | {note} |" for f, note in rows]
    return "\n".join(out)


def prompts(data_dir=None, vu_path=None) -> dict[str, str]:
    pack = pack_markdown(data_pack(data_dir, vu_path))
    return {"nodata": f"{QUESTION}\n\n{NODATA_TAIL}\n",
            "data": f"{QUESTION}\n\n{DATA_TAIL}\n\n## 데이터 팩\n\n{pack}\n"}


# ---------------------------------------------------------------------- the model calls (paid)
def worst_case_cost(prompt: str, model: str, max_tokens: int = MAX_TOKENS) -> float:
    """Upper bound in USD: one token per character of system and prompt (Korean runs below that) and a full output."""
    from ..llm import PRICING
    p_in, p_out, _ = PRICING[model]
    return ((len(SYSTEM) + len(prompt)) * p_in + max_tokens * p_out) / 1e6


def run(client, out_dir: str | Path = OUT_DIR, max_cost: float = 1.0, variants=VARIANTS, force: bool = False,
        max_tokens: int = MAX_TOKENS, thinking: dict | None = None, log=print) -> dict:
    """Ask each variant once. Refuses before any call when the worst case of the calls still to make exceeds max_cost.
    `thinking` is passed through as is, e.g. {"type": "between_tools"} to keep Claude Sonnet 5.5 from spending the
    output budget on thinking (its adaptive thinking is on by default and {"type": "disabled"} is rejected)."""
    out = Path(out_dir)
    write_prompts(out)
    record_path = out / "calls.json"
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {"calls": {}}
    todo = [v for v in variants if force or not (out / f"answer_{v}.md").exists()]
    texts = {v: (out / f"prompt_{v}.md").read_text(encoding="utf-8") for v in todo}
    bound = sum(worst_case_cost(texts[v], client.model, max_tokens) for v in todo)
    if bound > max_cost:
        raise AIReportError(f"worst case ${bound:.2f} for {todo} exceeds --max-cost ${max_cost:.2f}")
    log(f"calls to make: {todo or 'none'} (worst case ${bound:.2f}, cap ${max_cost:.2f})")
    for v in todo:
        started = dt.datetime.now(dt.timezone.utc)
        extra = {"thinking": thinking} if thinking else {}
        resp, usage = client.create(system=SYSTEM, max_tokens=max_tokens,
                                    messages=[{"role": "user", "content": texts[v]}], **extra)
        text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        blocks = [getattr(b, "type", "?") for b in resp.content]
        (out / f"answer_{v}.md").write_text(text + "\n", encoding="utf-8", newline="\n")
        if v in record["calls"]:  # an earlier answer is replaced; keep what it cost
            record.setdefault("replaced", []).append(record["calls"][v])
        record["calls"][v] = {"requested_model": client.model, "response_model": getattr(resp, "model", None),
                              "stop_reason": resp.stop_reason, "max_tokens": max_tokens,
                              **({"thinking": thinking} if thinking else {}),
                              **({"effort": client.effort} if getattr(client, "effort", None) else {}),
                              "content_blocks": {t: blocks.count(t) for t in dict.fromkeys(blocks)},
                              "answer_chars": len(text),
                              "started_utc": started.isoformat(timespec="seconds"), "usage": usage,
                              "prompt_file": f"prompt_{v}.md", "answer_file": f"answer_{v}.md"}
        log(f"{v}: {usage['input_tokens']} in, {usage['output_tokens']} out, ${usage['cost_usd']:.4f}, "
            f"stop {resp.stop_reason}")
    record["total_cost_usd"] = round(sum(c["usage"]["cost_usd"]
                                         for c in [*record["calls"].values(), *record.get("replaced", [])]), 6)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record


def write_prompts(out_dir: str | Path = OUT_DIR) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "system.txt").write_text(SYSTEM + "\n", encoding="utf-8", newline="\n")
    for v, text in prompts().items():
        (out / f"prompt_{v}.md").write_text(text, encoding="utf-8", newline="\n")
    return out


# ---------------------------------------------------------------------- figures in a Korean answer
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_FIG = re.compile(
    rf"(?<![\d.,A-Za-z_제])(?P<sign>[-−△+])?(?:"
    rf"(?P<jo>{_NUM})\s?조(?:\s?(?P<jo_eok>{_NUM})\s?억)?(?:\s?(?P<jo_man>{_NUM})\s?만)?\s?(?P<jo_tail>원)?"
    rf"|(?P<eok>{_NUM})\s?억(?:\s?(?P<eok_man>{_NUM})\s?만)?\s?(?P<eok_tail>원|주)?"
    rf"|(?P<man>{_NUM})\s?만\s?(?P<man_tail>원|주)"
    rf"|(?P<plain>{_NUM})\s?(?P<punit>백만\s?원|백만\s?주|원|주|%p|%포인트|pp|bp|%|배)"
    rf"|(?P<bare>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+\.\d+)(?![\d.,]\d))")
_RANGE = re.compile(r"\s?(?:(?:초반|중반|후반)대|대(?![비출표상응규형한체량]))")  # "30% 중반대", "40%대로", not "대비"
_MASK_CITES = r"\[사실 [^\]]*\]|\[계산:[^\]]*\]|`[^`]*`"
_MASK = re.compile(_MASK_CITES + r"|\d{4}[.\-/]\d{1,2}(?:[.\-/]\d{1,2})?|\d{14}")
_CITE = re.compile(r"^[\s*)）]*(?P<cite>\[(?:사실 (?P<ids>[^\]]*)|계산:\s*(?P<expr>[^\]]+))\])")
_HEADER_UNIT = re.compile(r"\((?P<u>조원|억원|백만원|원|%p|%|주|배|bp)\)|\[(?P<u2>조원|억원|백만원|%p|%)\]|단위\s*:\s*(?P<u3>조원|억원|백만원|%)")


@dataclass
class Figure:
    text: str
    value: float          # 원, %, %p, 주 or 배
    unit: str             # KRW, %, pp, shares, x
    precision: float      # one unit of the last written digit
    signed: bool          # an explicit sign was written
    start: int
    end: int


def _num(s: str) -> tuple[float, float]:
    s = s.replace(",", "")
    dec = len(s.split(".")[1]) if "." in s else 0
    return float(s), 10.0 ** -dec


def _scaled(value: float, precision: float, scale: float) -> tuple[float, float]:
    return value * scale, precision * scale


def parse_figures(text: str, header_unit: str | None = None) -> list[Figure]:
    """Figures in one line. Masked: citations, code spans, dates and 14-digit receipt numbers. Skipped: ranges such
    as "30% 중반대" or "40%대", which state no single value."""
    masked = _MASK.sub(lambda m: " " * len(m.group(0)), text)
    out = []
    for m in _FIG.finditer(masked):
        if _RANGE.match(masked, m.end()):
            continue
        g = m.groupdict()
        unit, value, prec = "KRW", 0.0, 1.0
        if g["jo"]:
            value, prec = _scaled(*_num(g["jo"]), 1e12)
            for part, scale in (("jo_eok", 1e8), ("jo_man", 1e4)):
                if g[part]:
                    v, p = _scaled(*_num(g[part]), scale)
                    value, prec = value + v, p
        elif g["eok"]:
            value, prec = _scaled(*_num(g["eok"]), 1e8)
            if g["eok_man"]:
                v, prec = _scaled(*_num(g["eok_man"]), 1e4)
                value += v
            unit = "shares" if g["eok_tail"] == "주" else "KRW"
        elif g["man"]:
            value, prec = _scaled(*_num(g["man"]), 1e4)
            unit = "shares" if g["man_tail"] == "주" else "KRW"
        elif g["plain"]:
            value, prec = _num(g["plain"])
            u = g["punit"].replace(" ", "")
            unit, scale = {"백만원": ("KRW", 1e6), "백만주": ("shares", 1e6), "원": ("KRW", 1), "주": ("shares", 1),
                           "%p": ("pp", 1), "%포인트": ("pp", 1), "pp": ("pp", 1), "bp": ("pp", 0.01), "%": ("%", 1),
                           "배": ("x", 1)}[u]
            value, prec = _scaled(value, prec, scale)
        else:
            if not header_unit:
                continue
            value, prec = _num(g["bare"])
            unit, scale = {"조원": ("KRW", 1e12), "억원": ("KRW", 1e8), "백만원": ("KRW", 1e6), "원": ("KRW", 1),
                           "%": ("%", 1), "%p": ("pp", 1), "bp": ("pp", 0.01), "주": ("shares", 1),
                           "배": ("x", 1)}[header_unit]
            value, prec = _scaled(value, prec, scale)
        sign = g["sign"]
        if sign in ("-", "−", "△"):
            value = -value
        out.append(Figure(text[m.start():m.end()].strip(), value, unit, prec, sign is not None, m.start(), m.end()))
    return out


# ---------------------------------------------------------------------- the reference set
@dataclass(frozen=True)
class Ref:
    group: str            # stock code or "sector"
    family: str           # account, common_equity, cir, roe, cet1, payout, div_payout, shares, dividends, buyback
    period: str           # Q1 Q2 H1 Q3 9M Q4 FY, or "" when not tied to one
    year: int | None
    value: float
    unit: str             # KRW, %, pp, shares
    label: str
    sources: tuple[str, ...]
    kind: str = "value"   # value | target | reported
    basis: str = ""       # "qoq" for a change on the previous quarter; year-on-year otherwise


def _src(*facts: Fact) -> tuple[str, ...]:
    return tuple(dict.fromkeys(f.meta["rcept_no"] for f in facts if f.meta.get("rcept_no")))


def references(data_dir=None, vu_path=None) -> list[Ref]:
    ds = Dataset(Path(data_dir) if data_dir else vu.DATA_DIR)
    data = vu.load(vu_path or vu.VALUEUP_FILE)
    m = vu.measures(ds, data)
    refs: list[Ref] = []
    sector: dict[tuple, list] = {}

    def add(code, family, period, year, value, unit, label, sources, kind="value", basis=""):
        refs.append(Ref(code, family, period, year, float(value), unit, f"{NAMES.get(code, '4개 그룹')} {label}",
                        tuple(sources), kind, basis))

    for code in CODES:
        c = ds.companies[code]
        st = {(y, p): {f.meta["account"]: f for f in statement_facts(c, y, p, "CFS")}
              for y, p in ((2025, "Q1"), (2025, "H1"), (2025, "Q3"), (2025, "FY"), (2026, "Q1"), (2026, "H1"),
                           (2026, "Q3"), (2026, "FY"))}
        st = {k: v for k, v in st.items() if v}  # later 2026 reports count once watch-filings has added them
        flow: dict[tuple[int, str], dict[str, tuple[float, tuple]]] = {}
        for (y, p), fs in st.items():
            tag = "9M" if p == "Q3" else p
            for a, f in fs.items():
                if a in FLOWS:
                    flow.setdefault((y, tag), {})[a] = (f.value, _src(f))
                else:
                    add(code, a, p, y, f.value, "KRW", f"{y}년 {PERIOD_END[p]} {ACCOUNT_KO[a]}", _src(f))
            ce = fs["equity_owners"].value - fs["hybrid_capital"].value
            add(code, "common_equity", p, y, ce, "KRW", f"{y}년 {PERIOD_END[p]} 보통주자본", _src(fs["equity_owners"]))
        sub = lambda a, b: {k: (a[k][0] - b[k][0], a[k][1] + b[k][1]) for k in FLOWS}
        for y in (2025, 2026):
            for q, (cum, prev) in (("Q2", ("H1", "Q1")), ("Q3", ("9M", "H1")), ("Q4", ("FY", "9M"))):
                if (y, cum) in flow and (y, prev) in flow:
                    flow[(y, q)] = sub(flow[(y, cum)], flow[(y, prev)])
        for (y, tag), accts in flow.items():
            for a, (v, s) in accts.items():
                add(code, a, tag, y, v, "KRW", f"{y}년 {PERIOD_KO[tag]} {ACCOUNT_KO[a]}", s)
                if a == "net_income_owners" and tag in ("Q2", "H1", "Q3", "9M"):
                    sector.setdefault(("ni", y, tag), []).append((v, s))
        new = [t for t in ("Q3", "9M") if (2026, t) in flow]  # tags of the latest reports, once filed
        for a in FLOWS:
            for tag in ("Q1", "Q2", "H1", *new):
                (v1, s1), (v0, s0) = flow[(2026, tag)][a], flow[(2025, tag)][a]
                add(code, a, tag, 2026, (v1 / v0 - 1) * 100, "%", f"2026년 {PERIOD_KO[tag]} {ACCOUNT_KO[a]} 전년 대비 증감률",
                    s1 + s0)
            for q, p in (("Q2", "Q1"), ("Q3", "Q2")):
                if (2026, q) in flow:
                    (v1, s1), (v0, s0) = flow[(2026, q)][a], flow[(2026, p)][a]
                    add(code, a, q, 2026, (v1 / v0 - 1) * 100, "%", f"2026년 {PERIOD_KO[q]} {ACCOUNT_KO[a]} 전분기 대비 "
                        "증감률", s1 + s0, basis="qoq")
        cir = {}
        for (y, tag) in ((2025, "Q1"), (2025, "Q2"), (2025, "H1"), (2025, "FY"), (2026, "Q1"), (2026, "Q2"),
                         (2026, "H1"), *[(y, t) for t in new for y in (2025, 2026)]):
            f = flow[(y, tag)]
            g, rev = f["general_admin_expense"][0], sum(f[k][0] for k in ("general_admin_expense", "operating_income",
                                                                         "credit_loss_provision"))
            cir[(y, tag)] = g / rev * 100
            add(code, "cir", tag, y, cir[(y, tag)], "%", f"{y}년 {PERIOD_KO[tag]} CIR", f["general_admin_expense"][1])
            if y == 2026 and tag in ("H1", "Q2", *new):
                sector.setdefault(("cir", y, tag), []).append((g, rev, f["general_admin_expense"][1]))
        for tag in ("Q2", "H1", *new):
            add(code, "cir", tag, 2026, cir[(2026, tag)] - cir[(2025, tag)], "pp",
                f"2026년 {PERIOD_KO[tag]} CIR 전년 대비 변화", flow[(2026, tag)]["general_admin_expense"][1])
        mm = m[code]
        (roe_now, _, f_now), (roe_prev, _, f_prev) = mm["roe"]
        add(code, "roe", "", 2026, roe_now, "%", "TTM ROE(2026년 6월 말, 평균 지배주주지분)", _src(*f_now))
        add(code, "roe", "", 2025, roe_prev, "%", "TTM ROE(2025년 6월 말, 평균 지배주주지분)", _src(*f_prev))
        add(code, "roe", "", 2026, roe_now - roe_prev, "pp", "TTM ROE 1년 변화", _src(*f_now, *f_prev))
        add(code, "roe", "FY", 2025, mm["roe_fy2025"][0], "%", "2025 사업연도 ROE(연초·연말 평균)", _src(*mm["roe_fy2025"][2]))
        h26, fy25 = st[(2026, "H1")], st[(2025, "FY")]
        ann = 2 * h26["net_income_owners"].value / ((fy25["equity_owners"].value + h26["equity_owners"].value) / 2) * 100
        add(code, "roe", "H1", 2026, ann, "%", "2026년 상반기 연환산 ROE(연초·6월 말 평균)",
            _src(h26["net_income_owners"], fy25["equity_owners"]))
        ce = lambda fs: fs["equity_owners"].value - fs["hybrid_capital"].value
        ttm = f_now[0].value - f_now[1].value + f_now[2].value
        add(code, "roe", "", 2026, ttm / ((ce(st[(2025, "H1")]) + ce(h26)) / 2) * 100, "%",
            "TTM ROE(평균 보통주자본)", _src(*f_now))
        cet1 = vu.cet1_fact(code) or vu.cet1_reference_fact(code)
        add(code, "cet1", "H1" if cet1.id.endswith("2026H1") else "FY", 2026 if cet1.id.endswith("2026H1") else 2025,
            cet1.value, "%", cet1.label.split(" ", 1)[1], _src(cet1))
        if cet1.id.endswith("2026H1"):
            add(code, "cet1", "H1", 2026, cet1.value - 13.0, "pp", "CET1 비율의 13% 초과분", _src(cet1))
        for i, year in enumerate((2025, 2024)):
            value, _, facts, parts = mm["payout"][i]
            add(code, "payout", "", year, value, "%", f"{year}년 주주환원율(배당 + 그해 결정한 자기주식)", _src(*facts))
            add(code, "div_payout", "", year, parts["dividends"].value / parts["net_income"].value * 100, "%",
                f"{year}년 배당성향", _src(parts["dividends"], parts["net_income"]))
            add(code, "dividends", "FY", year, parts["dividends"].value, "KRW", f"{year} 사업연도 현금배당금 총액",
                _src(parts["dividends"]))
            add(code, "buyback", "", year, sum(b.value for b in parts["buybacks"]), "KRW",
                f"{year}년에 결정한 자기주식 취득 합계", _src(*parts["buybacks"]))
            for b in parts["buybacks"]:
                add(code, "buyback", "", year, b.value, "KRW", b.label, _src(b))
        for i, year in enumerate((2026, 2025)):
            if mm["shares"][i]:
                add(code, "shares", "H1", year, mm["shares"][i][0], "shares", f"{year}년 6월 말 발행 보통주식 수",
                    _src(*mm["shares"][i][2]))
        if all(mm["shares"]):
            add(code, "shares", "", 2026, mm["shares"][1][0] - mm["shares"][0][0], "shares",
                "1년간 줄어든 발행 보통주식 수", _src(*mm["shares"][0][2], *mm["shares"][1][2]))
        for t in vu.TARGETS:
            if t.code == code and t.metric == "shares" and mm["shares"][0]:
                treasury = next((f for f in share_facts(c, 2026, "H1") if f.id.endswith("shares_treasury_common")), None)
                add(code, "shares", "", 2026, mm["shares"][0][0] - t.threshold, "shares", "발행주식 수 목표까지 남은 주식 수",
                    (*_src(*mm["shares"][0][2]), t.rcept_no))
                if treasury:
                    add(code, "shares", "", 2026, mm["shares"][0][0] - treasury.value - t.threshold, "shares",
                        "자기주식을 모두 소각해도 목표까지 남는 주식 수", (*_src(*mm["shares"][0][2]), t.rcept_no))
        if code == "055550":  # the 2026 plan restates the share target as a reduction (quoted in valueup.TARGETS)
            add(code, "shares", "", None, 50_000_000, "shares", "기업가치 제고 계획 발행주식수 감축 목표(50백만주)",
                ("20260423800235",), kind="target")
        for t in vu.TARGETS:
            if t.code == code and t.threshold is not None:
                add(code, t.metric, "", None, t.threshold, "shares" if t.metric == "shares" else "%",
                    f"기업가치 제고 계획 {vu.METRIC_KO[t.metric]} 목표", (t.rcept_no,), kind="target")
                now = {"roe": roe_now, "cet1": cet1.value, "payout": mm["payout"][0][0]}.get(t.metric)
                if now is not None:
                    add(code, t.metric, "", 2026, t.threshold - now, "pp", f"{vu.METRIC_KO[t.metric]} 목표와 현재의 차",
                        (t.rcept_no,))
    for c in vu.COMPANY_REPORTED:
        add(c["code"], "roe", "FY", c["year"], c["roe"], "%", f"{c['year']}년 ROE(회사 발표)", (c["rcept_no"],), "reported")
        add(c["code"], "payout", "", c["year"], c["payout"], "%", f"{c['year']}년 주주환원율(회사 발표)", (c["rcept_no"],),
            "reported")
    tags = ("Q2", "H1", *[t for t in ("Q3", "9M") if len(sector.get(("ni", 2026, t), [])) == len(CODES)])
    for y, tag in [(y, t) for t in tags for y in (2025, 2026)]:
        vals = sector[("ni", y, tag)]
        add("sector", "net_income_owners", tag, y, sum(v for v, _ in vals), "KRW", f"{y}년 {PERIOD_KO[tag]} 지배주주순이익 합계",
            tuple(s for _, ss in vals for s in ss))
    for tag in tags:
        now, before = sector[("ni", 2026, tag)], sector[("ni", 2025, tag)]
        add("sector", "net_income_owners", tag, 2026, (sum(v for v, _ in now) / sum(v for v, _ in before) - 1) * 100,
            "%", f"2026년 {PERIOD_KO[tag]} 합산 지배주주순이익 전년 대비 증감률", ())
        g = sector[("cir", 2026, tag)]
        add("sector", "cir", tag, 2026, sum(x[0] for x in g) / sum(x[1] for x in g) * 100, "%",
            f"2026년 {PERIOD_KO[tag]} 합산 CIR", ())
        rs = [r.value for r in refs if r.family == "cir" and r.period == tag and r.year == 2026 and r.unit == "%"
              and r.group != "sector"]
        add("sector", "cir", tag, 2026, max(rs) - min(rs), "pp", f"2026년 {PERIOD_KO[tag]} 그룹별 CIR 최고-최저 차", ())
    ces = [r.value for r in refs if r.family == "common_equity" and r.period == "H1" and r.year == 2026]
    add("sector", "common_equity", "H1", 2026, sum(ces) / len(ces), "KRW", "2026년 6월 말 보통주자본 평균", ())
    add("sector", "common_equity", "H1", 2026, max(ces) - min(ces), "KRW", "2026년 6월 말 보통주자본 최대-최소 차", ())
    return refs


# ---------------------------------------------------------------------- context
GROUP_RE = re.compile(r"(?P<g105560>KB|국민)|(?P<g055550>신한)|(?P<g086790>하나(?:금융|은행)|하나(?=[은는이와과도로]|[^가-힣]|$))"
                      r"|(?P<g316140>우리(?:금융|은행)|우리(?=[은는이가의와과도로]|[^가-힣]|$))"
                      r"|(?P<sector>4대|네 그룹|\d개 그룹|4개사|4사|합산|합계|섹터|업종 전체)")
FAMILY_RE = re.compile(
    r"(?P<cet1>보통주자본비율|CET\s?-?1)|(?P<payout>주주환원율|총주주환원|환원율)|(?P<div_payout>배당성향)"
    r"|(?P<roe>ROE|자기자본이익률)|(?P<cir>영업이익경비율|경비율|CIR|비용효율|Cost[- ]?to[- ]?income)"
    r"|(?P<common_equity>보통주자본)|(?P<hybrid_capital>신종자본)|(?P<equity>지배주주지분|자기자본|자본총계|총자본)"
    r"|(?P<net_income_owners>지배주주\s?순이익|지배기업.{0,6}순이익)|(?P<net_interest_income>순이자이익|이자이익|NII)"
    r"|(?P<operating_income>영업이익)|(?P<net_income>당기순이익|순이익)|(?P<net_fee_income>수수료)"
    r"|(?P<general_admin_expense>판관비|판매비와\s?관리비|일반관리비|관리비)"
    r"|(?P<credit_loss_provision>충당금|대손|신용손실|크레딧\s?코스트)|(?P<total_assets>총자산|자산총계)"
    r"|(?P<shares>발행주식|주식\s?수|주식수|유통주식)|(?P<dividends>배당)|(?P<buyback>자사주|자기주식|소각)", re.I)
FAMILIES = {"equity": {"equity_owners", "equity_total"}, "net_income": {"net_income", "net_income_owners"}}
PERIOD_RE = re.compile(r"(?P<M9>3분기\s?누적|1~9월|9M)"  # nine months to September (Q3 report figures)
                       r"|(?P<Q2>2분기|2Q|4~6월|4-6월|2/4분기)|(?P<H1>상반기|1H|반기|6월\s?말)|(?P<Q1>1분기|1Q|1~3월|3월\s?말)"
                       r"|(?P<Q3>3분기|3Q)|(?P<Q4>4분기|4Q)|(?P<FY>연간|사업연도|연말|12월\s?말|20\d\d년\s?말|FY)|(?P<none>TTM|최근\s?12개월|LTM)")
YEAR_RE = re.compile(r"(?P<y4>20(?:2[4-7]))\s?년?|'(?P<y2a>2[4-7])|[1-4]Q(?P<y2b>2[4-7])|[12]H(?P<y2c>2[4-7])"
                     r"|(?P<y2d>2[4-7])년|(?P<prev>전년|작년|지난해|1년 전)")
TARGET_RE = re.compile(r"목표|Target|타깃")
CURRENT_RE = re.compile(r"현재|현 수준|실적|기록|TTM|최근|기준일|6월\s?말|상반기|2분기|남은|차이|격차|부족")
GROWTH_RE = re.compile(r"YoY|QoQ|증감|증가율|성장률|전년\s?대비|전분기\s?대비", re.I)
NEG_RE = re.compile(r"^[^,.;:|]{0,8}?(감소|하락|줄|축소|악화|역성장)")


def _last(regex: re.Pattern, text: str):
    hits = list(regex.finditer(text))
    return hits[-1] if hits else None


def _kind(m) -> str | None:
    return next((k for k, v in m.groupdict().items() if v), None) if m else None


@dataclass
class Context:
    group: str | None = None
    families: set[str] = field(default_factory=set)
    period: str | None = None
    years: set[int] = field(default_factory=set)
    target: bool = False
    compare: str | None = None    # "qoq" when the nearest comparison word is 전분기/QoQ


SENTENCE_END = re.compile(r"(?<=[다요음함])\.\s*|[!?]\s+")
CLAUSE_END = re.compile(r"(?<!\d)[,.;](?!\d)|\s-\s")
COMPARE_RE = re.compile(r"(?P<qoq>전분기|직전\s?분기|QoQ)|(?P<yoy>전년|작년|지난해|YoY)", re.I)


def _context(before: str, after: str, fallback: Context, column_target: bool = False, left_cells: str = "") -> Context:
    """Group from the line; metric from the sentence, then the line; period and years from the sentence; 목표 and the
    comparison basis from the clause. Missing pieces come from the heading (or a line naming one group)."""
    ctx = Context()
    before = re.sub(_MASK_CITES, " ", before)
    sent = SENTENCE_END.split(before)[-1]
    clause = CLAUSE_END.split(sent)[-1]
    g = _kind(_last(GROUP_RE, sent) or _last(GROUP_RE, before))
    ctx.group = (g[1:] if g and g.startswith("g") else g) or fallback.group
    fam = _kind(_last(FAMILY_RE, sent) or FAMILY_RE.search(after[:20])  # a table's metric may sit in a cell to the left
                or _last(FAMILY_RE, re.sub(_MASK_CITES, " ", left_cells)) or _last(FAMILY_RE, before))
    ctx.families = FAMILIES.get(fam, {fam}) if fam else set(fallback.families)
    per = {"M9": "9M"}.get(k := _kind(_last(PERIOD_RE, sent)), k)
    ctx.period = (None if per == "none" else per) if per else fallback.period
    years = set()
    for y in YEAR_RE.finditer(sent):
        k = _kind(y)
        years |= {2025, 2026} if k == "prev" else {2000 + int(y.group(k)[-2:])}
    ctx.years = years or set(fallback.years)
    t = _last(TARGET_RE, sent)  # 목표 earlier in the sentence, unless the sentence moves on to the current level
    ctx.target = bool(column_target or (t and not CURRENT_RE.search(sent[t.end():]))
                      or re.match(r"\s*(?:를|을)\s*목표", after))
    ctx.compare = _kind(_last(COMPARE_RE, sent))
    return ctx


# ---------------------------------------------------------------------- checking
def _close(fig: Figure, true: float, negative_word: bool = False) -> bool:
    w, t = fig.value, true
    if not fig.signed:
        if negative_word:
            w = -abs(w)
        elif fig.unit in ("%", "pp"):
            w, t = abs(w), abs(t)
    if abs(w - t) <= fig.precision / 2 * 1.0001 + 1e-9:
        return True
    same_sign = (w >= 0) == (t >= 0)
    return same_sign and 0 <= abs(t) - abs(w) < fig.precision  # truncated, not rounded


def _fmt(value: float, unit: str) -> str:
    if unit == "KRW":
        return fmt_krw(value)
    if unit == "shares":
        return f"{value:,.0f}주"
    return f"{value:,.2f}{'%p' if unit == 'pp' else '%'}"


def _ref_dict(r: Ref) -> dict:
    return {"label": r.label, "value": round(r.value, 6), "shown": _fmt(r.value, r.unit), "sources": list(r.sources),
            "kind": r.kind}


def match(fig: Figure, ctx: Context, refs: list[Ref], negative_word: bool = False) -> tuple[str, dict | None]:
    pool = [r for r in refs if r.unit == fig.unit]
    if ctx.target and fig.unit in ("%", "shares"):
        # a stated target is compared with the quoted targets only; a group without one cannot be checked
        targets = [r for r in pool if r.kind == "target" and ctx.group in (None, "sector", r.group)
                   and (not ctx.families or r.family in ctx.families)]
        hit = next((r for r in targets if _close(fig, r.value)), None)
        if hit or not targets:
            return ("일치", _ref_dict(hit)) if hit else ("확인 불가", None)
        return "불일치", _ref_dict(min(targets, key=lambda r: abs(r.value - fig.value)))
    if ctx.group is None:
        return "확인 불가", None  # no group named in the line, its heading or its table row
    pool = [r for r in pool if r.group == ctx.group and r.kind != "target"]
    if not ctx.families:
        hits = [r for r in pool if _close(fig, r.value, negative_word)]
        best = min(hits, key=lambda r: abs(r.value - fig.value), default=None)
        return ("지표 불명", _ref_dict(best)) if best else ("확인 불가", None)
    fam = [r for r in pool if r.family in ctx.families]
    if not fam:
        return "확인 불가", None
    exact = [r for r in fam if (r.basis == "qoq") == (ctx.compare == "qoq")
             and (not ctx.period or not r.period or r.period == ctx.period)
             and (not ctx.years or r.year is None or r.year in ctx.years)]
    gap = lambda r: abs(abs(r.value) - abs(fig.value)) / (abs(r.value) or 1)
    hits = [r for r in exact if _close(fig, r.value, negative_word)]
    if hits:
        return "일치", _ref_dict(min(hits, key=gap))
    others = [r for r in fam if _close(fig, r.value, negative_word)]
    if others:
        return "기준 다름", _ref_dict(min(others, key=gap))
    pool = exact or fam
    if not ctx.years:  # no year written: show the latest year's value, not the closest of any year
        latest = max((r.year for r in pool if r.year), default=None)
        pool = [r for r in pool if r.year in (latest, None)] or pool
    return "불일치", _ref_dict(min(pool, key=gap))


_ID = re.compile(r"`([^`]+)`")


def _eval(expr: str, facts: dict[str, Fact]) -> float:
    names = {}

    def sub(m):
        if m.group(1) not in facts:
            raise KeyError(m.group(1))
        key = f"v{len(names)}"
        names[key] = facts[m.group(1)].value
        return key
    tree = ast.parse(_ID.sub(sub, expr.replace("×", "*").replace("÷", "/").replace("−", "-")), mode="eval")
    ops = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
           ast.Div: lambda a, b: a / b}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name) and n.id in names:
            return names[n.id]
        raise ValueError("unsupported expression")
    return ev(tree)


def check_citation(fig: Figure, cite: re.Match, facts: dict[str, Fact]) -> tuple[str, str]:
    """A ratio computed as a fraction is accepted when the figure shows it in percent (x 100)."""
    try:
        if cite.group("expr"):
            v = _eval(cite.group("expr"), facts)
            values = [v, v * 100] if fig.unit in ("%", "pp") else [v]
        else:
            ids = _ID.findall(cite.group("ids"))
            if not ids:
                raise ValueError("no fact id")
            values = [facts[i].value for i in ids]
    except (KeyError, ValueError, SyntaxError, ZeroDivisionError, TypeError) as exc:
        return "인용 오류", f"{type(exc).__name__}: {exc}"
    best = min(values, key=lambda x: abs(abs(x) - abs(fig.value)))
    return ("인용 일치" if any(_close(fig, x) for x in values) else "인용 불일치"), _fmt(best, fig.unit)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def check_answer(text: str, refs: list[Ref], facts: dict[str, Fact] | None = None) -> list[dict]:
    """One record per figure, in reading order."""
    facts = facts or {}
    out = []
    heading = Context()            # from the last heading, or a line without figures (a bold label, a lead-in)
    header: list[str] | None = None
    parents: list[tuple[int, str | None]] = []  # (indent, group) of the enclosing bullets
    for no, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            heading = _context(s, "", Context())
            header, parents = None, []
            continue
        if s.startswith("|"):
            if re.fullmatch(r"\|[\s:|-]+\|?", s):
                continue
            if header is None:
                header = _cells(s)
                continue
            cells = _cells(s)
            row_label = cells[0] if cells else ""
            for j, cell in enumerate(cells):
                col = own = header[j] if j < len(header) else ""
                if GROWTH_RE.search(col) and not FAMILY_RE.search(col):  # "YoY(%)" next to "순이익(억원)"
                    col = next((h for h in reversed(header[:j]) if FAMILY_RE.search(h)), "") + " " + col
                u = _HEADER_UNIT.search(own) or _HEADER_UNIT.search(row_label)
                hu = next((x for x in u.groups() if x), None) if u else None
                for fig in parse_figures(cell, hu):
                    before = f"{col} | {row_label} | {cell[:fig.start]}"
                    out.append(_record(no, fig, before, cell[fig.end:], heading, refs, facts,
                                       bool(TARGET_RE.search(own)), " | ".join(cells[1:j])))
            continue
        header = None
        figs = parse_figures(line)
        plain = _MASK.sub(" ", line)
        groups = [_group(m) for m in GROUP_RE.finditer(plain)]
        if not figs and s:
            fam = _kind(_last(FAMILY_RE, plain))
            heading = Context(groups[0] if len(set(groups)) == 1 else heading.group,
                              FAMILIES.get(fam, {fam}) if fam else heading.families, heading.period, heading.years)
        indent = len(line) - len(line.lstrip())
        parents = [p for p in parents if p[0] < indent]
        fallback = heading
        if parents and parents[-1][1]:  # a sub-bullet inherits the group of its parent bullet
            fallback = Context(parents[-1][1], heading.families, heading.period, heading.years)
        if s.startswith(("-", "*")) or s[:2].rstrip(".").isdigit():
            parents.append((indent, groups[-1] if groups else fallback.group))
        for fig in figs:
            quoted = (line[:fig.start].count('"') % 2 == 1) or (line[:fig.start].count("“") > line[:fig.start].count("”"))
            out.append(_record(no, fig, line[:fig.start], line[fig.end:], fallback, refs, facts, quoted))
    return out


def _group(m: re.Match) -> str:
    g = _kind(m)
    return g[1:] if g.startswith("g") else g


def _record(no, fig, before, after, heading, refs, facts, column_target=False, left_cells="") -> dict:
    ctx = _context(before, after, heading, column_target, left_cells)
    neg = bool(NEG_RE.match(after)) and any(f in FLOWS for f in ctx.families)
    verdict, ref = match(fig, ctx, refs, neg)
    cite = _CITE.match(after)
    cverdict, cvalue = check_citation(fig, cite, facts) if cite else (None, None)
    return {"line": no, "figure": fig.text, "value": fig.value, "unit": fig.unit,
            "context": {"group": NAMES.get(ctx.group, ctx.group), "families": sorted(ctx.families),
                        "period": ctx.period, "years": sorted(ctx.years), "target": ctx.target},
            "verdict": verdict, "ref": ref, "citation": cite.group("cite") if cite else None,
            "citation_verdict": cverdict, "citation_value": cvalue}


VERDICTS = ("일치", "기준 다름", "불일치", "지표 불명", "확인 불가")
CITATION_VERDICTS = ("인용 일치", "인용 불일치", "인용 오류")


def summarize(records: list[dict], text: str) -> dict:
    s = {"figures": len(records), **{v: sum(r["verdict"] == v for r in records) for v in VERDICTS},
         "cited": sum(r["citation"] is not None for r in records),
         **{v: sum(r["citation_verdict"] == v for r in records) for v in CITATION_VERDICTS},
         "확인 필요 표시": text.count("확인 필요")}
    return s


def check(out_dir: str | Path = OUT_DIR, data_dir=None, vu_path=None) -> dict:
    out = Path(out_dir)
    refs = references(data_dir, vu_path)
    facts = {f.id: f for f, _ in data_pack(data_dir, vu_path)}
    result = {}
    for v in VARIANTS:
        p = out / f"answer_{v}.md"
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        recs = check_answer(text, refs, facts)
        result[v] = {"summary": summarize(recs, text), "figures": recs}
    if not result:
        raise AIReportError(f"no answers in {out}; run with --run first")
    (out / "factcheck.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                                        newline="\n")
    (out / "factcheck.md").write_text(render(result, out), encoding="utf-8", newline="\n")
    return result


# ---------------------------------------------------------------------- the fact-check report (Korean Markdown)
TITLE = {"nodata": "자료 없이 쓴 답", "data": "데이터 팩을 주고 쓴 답"}


def render(result: dict, out: Path) -> str:
    calls = json.loads((out / "calls.json").read_text(encoding="utf-8")) if (out / "calls.json").exists() else {}
    lines = ["# AI 리포트 팩트체크", "",
             "같은 질문을 언어 모델에 두 번 물었습니다. 한 번은 자료 없이, 한 번은 DART 공시에서 읽은 데이터 팩과 함께입니다. "
             "답에 나온 숫자를 모두 공시 기반 기준값과 규칙으로 대조했습니다(판정에 언어 모델을 쓰지 않음).", ""]
    if calls:
        lines += ["| 답 | 모델 | 입력 토큰 | 출력 토큰 | 비용(USD) | 종료 |", "|---|---|---|---|---|---|"]
        for v, c in calls["calls"].items():
            u = c["usage"]
            lines.append(f"| {TITLE[v]} | {c['response_model'] or c['requested_model']} | {u['input_tokens']:,} | "
                         f"{u['output_tokens']:,} | {u['cost_usd']:.4f} | {c['stop_reason']} |")
        lines += ["", f"합계 {calls.get('total_cost_usd', 0):.4f}달러"
                  + (f"(다시 물어 교체한 답 {len(calls['replaced'])}개 포함)" if calls.get("replaced") else "") + "."]
        for c in calls.get("replaced", []):
            u = c["usage"]
            lines.append(f"- 교체한 답: {TITLE[c['prompt_file'][7:-3]]}의 이전 답(출력 한도 {c['max_tokens']:,}토큰, 출력 "
                         f"{u['output_tokens']:,}토큰, {u['cost_usd']:.4f}달러, 종료 {c['stop_reason']}). 이전 답은 저장소 "
                         "기록(git)에 남아 있습니다.")
        for v, c in calls["calls"].items():
            if c["stop_reason"] == "max_tokens":
                lines.append(f"- {TITLE[v]}은 출력 한도({c['max_tokens']:,}토큰)에 걸려 끝까지 쓰지 못했습니다. 아래 "
                             "숫자는 끊기기 전까지 쓴 부분입니다.")
        lines.append("")
    lines += ["## 요약", "", "| 판정 | " + " | ".join(TITLE[v] for v in result) + " |",
              "|---|" + "---|" * len(result)]
    keys = ("figures", *VERDICTS, "cited", *CITATION_VERDICTS, "확인 필요 표시")
    names = {"figures": "숫자 수", "cited": "근거가 붙은 숫자"}
    for k in keys:
        lines.append(f"| {names.get(k, k)} | " + " | ".join(str(result[v]["summary"][k]) for v in result) + " |")
    lines += ["", "- **일치**: 문맥(그룹·지표·기간)에 맞는 공시 값과 쓴 자릿수까지 같음(반올림 또는 버림).",
              "- **기준 다름**: 같은 그룹·지표의 다른 기간 또는 연도 값과 같음.",
              "- **불일치**: 같은 그룹·지표 값이 있지만 어느 것과도 다름. 가장 가까운 값을 적었습니다.",
              "- **지표 불명**: 지표 단어가 없어 그룹의 모든 값에서 찾음. **확인 불가**: 비교할 공시 값이 없음.",
              "- **인용 일치/불일치/오류**: 숫자 뒤의 [사실 `id`]·[계산: 식]을 데이터 팩으로 다시 계산한 결과.", ""]
    for v, res in result.items():
        lines += [f"## {TITLE[v]}", "", "| 줄 | 숫자 | 문맥 | 판정 | 공시 기준값 | 근거 판정 |", "|---|---|---|---|---|---|"]
        for r in res["figures"]:
            c = r["context"]
            ctx = " · ".join(x for x in (c["group"] or "", "/".join(c["families"]), c["period"] or "",
                                         "/".join(map(str, c["years"])), "목표" if c["target"] else "") if x)
            ref = r["ref"]
            refs = f"{ref['shown']} ({ref['label']}; {', '.join(ref['sources']) or '계산'})" if ref else ""
            cv = f"{r['citation_verdict']} ({r['citation_value']})" if r["citation_verdict"] else ""
            lines.append(f"| {r['line']} | {r['figure']} | {ctx} | {r['verdict']} | {refs} | {cv} |")
        lines.append("")
    vc = out / "verify_claims.json"
    if vc.exists():
        lines += _render_verify_claims(json.loads(vc.read_text(encoding="utf-8")))
    return "\n".join(lines).rstrip() + "\n"


def _render_verify_claims(data: dict) -> list[str]:
    lines = ["## 독립 검증: verify_claims", "", data.get("note", ""), "",
             "| 답 | 주장 | 판정 | 공시 값 | 접수번호 |", "|---|---|---|---|---|"]
    for row in data.get("claims", []):
        lines.append(f"| {TITLE.get(row['variant'], row['variant'])} | {row['quote']} | {row['verdict']} | "
                     f"{row.get('filed', '')} | {row.get('rcept_no', '')} |")
    return lines + [""]
