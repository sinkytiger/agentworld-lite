"""DART 실적 리뷰(Earnings Reviewer): 금융지주가 새 정기보고서를 내면 분기 실적을 변동표로 정리하고, 검사(QC)를 거쳐 숫자마다
접수번호가 붙은 한국어 실적 노트 초안을 쓴다. 기본은 언어 모델 없이 규칙으로 쓰고, --draft는 모델 초안을 받아 팩트체크한다.

    python -m agentworld_lite.finance earnings-review --group all                       # 오프라인, 최신 보고서
    python -m agentworld_lite.finance earnings-review --group 055550 --draft --max-cost 1.0   # 유료: 모델 초안 1회

Modelled on the Earnings Reviewer agent of anthropics/financial-services (Apache-2.0): pull the print, update the
coverage model, run model QC, draft the note, stage it for review, cite every number, never publish. Here the print
is the desk's DART dataset (`watch-filings --apply` adds a new report), there is no call transcript, and DART has no
consensus, so the variance table compares the quarter with the same quarter a year earlier and with the previous
quarter, plus the group's value-up targets. Outputs go to reports/earnings (outside git), marked 검토용 초안; nothing
is posted, sent or pushed.

A model draft (--draft) gets only figures from the dataset as a data pack (no filing text, so nothing in a filing can
instruct it), must cite every figure as [사실 `id`] or [계산: 식], and is checked with ai_report's rules. It is kept as
`검토 가능` only when no figure contradicts the filings and every citation recomputes; otherwise `수정 필요`.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import ai_report as ar
from . import valueup as vu
from .catalog import FHC
from .dataset import Dataset, Fact, statement_facts
from .env import REPO_ROOT
from .note_ko import cited_ko, dart_link, uncited_figures_ko

DATA_DIR = REPO_ROOT / "data" / "finance" / "dart_fhc"
OUT_DIR = REPO_ROOT / "reports" / "earnings"
ORDER = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}
QUARTER = {"Q1": ("1분기", None), "H1": ("2분기", "Q1"), "Q3": ("3분기", "H1"), "FY": ("4분기", "Q3")}
PREV_Q = {"Q1": ("FY", -1), "H1": ("Q1", 0), "Q3": ("H1", 0), "FY": ("Q3", 0)}  # report of the previous quarter
CUM_KO = {"H1": "상반기 누적", "Q3": "3분기 누적(1~9월)", "FY": "연간"}
LINES = ("net_interest_income", "net_fee_income", "general_admin_expense", "credit_loss_provision", "operating_income",
         "net_income_owners")
BANNER = "검토용 초안 · 교육용 예시·투자 권유 아님 · 외부 배포 전 사람이 검토"
SWING = 40.0  # percent: a larger year-on-year move in net interest or fee income is flagged for review


class ReviewError(Exception):
    pass


@dataclass
class Quarter:
    year: int
    period: str                                  # the report: Q1, H1, Q3 or FY
    values: dict[str, tuple[float, list[Fact]]]  # standalone quarter per account, with the facts behind it
    cum: dict[str, Fact]                         # the report's own (cumulative) facts


@dataclass
class Review:
    code: str
    name: str
    year: int
    period: str
    now: Quarter
    year_ago: Quarter | None
    prev: Quarter | None
    ttm_roe: tuple[float, list[Fact]] | None
    targets: list[vu.Target]
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.year}년 {QUARTER[self.period][0]}"


def latest_report(ds: Dataset, code: str) -> tuple[int, str]:
    keys = [k.split(":") for k in ds.companies[code].data["statements"] if k.endswith(":CFS")]
    year, period, _ = max(keys, key=lambda k: (int(k[0]), ORDER[k[1]]))
    return int(year), period


def quarter(ds: Dataset, code: str, year: int, period: str) -> Quarter | None:
    """The standalone quarter a report covers: Q2 = H1 - Q1, Q3 = 9M - H1, Q4 = FY - 9M."""
    c = ds.companies[code]
    cum = {f.meta["account"]: f for f in statement_facts(c, year, period, "CFS")}
    if not cum:
        return None
    before = QUARTER[period][1]
    prior = {f.meta["account"]: f for f in statement_facts(c, year, before, "CFS")} if before else {}
    if before and not prior:
        return None
    values = {}
    for a in LINES:
        if a in cum and (not before or a in prior):
            values[a] = ((cum[a].value - prior[a].value, [cum[a], prior[a]]) if before else (cum[a].value, [cum[a]]))
    return Quarter(year, period, values, cum)


def _ttm_roe(ds: Dataset, code: str, year: int, period: str) -> tuple[float, list[Fact]] | None:
    """TTM net income to owners / average owners' equity at the start and end of the twelve months."""
    c = ds.companies[code]
    get = lambda y, p: {f.meta["account"]: f for f in statement_facts(c, y, p, "CFS")}
    now, then, fy = get(year, period), get(year - 1, period), get(year - 1, "FY")
    if period == "FY":
        if not (now and then):
            return None
        ni, facts = now["net_income_owners"].value, [now["net_income_owners"]]
    else:
        if not (now and then and fy):
            return None
        ni = fy["net_income_owners"].value - then["net_income_owners"].value + now["net_income_owners"].value
        facts = [fy["net_income_owners"], then["net_income_owners"], now["net_income_owners"]]
    e0, e1 = then["equity_owners"], now["equity_owners"]
    return ni / ((e0.value + e1.value) / 2) * 100, [*facts, e0, e1]


def _cir(q: Quarter) -> tuple[float, list[Fact]] | None:
    v = q.values
    if not all(a in v for a in ("general_admin_expense", "operating_income", "credit_loss_provision")):
        return None
    g, o, p = (v[a][0] for a in ("general_admin_expense", "operating_income", "credit_loss_provision"))
    return g / (o + g + p) * 100, [f for a in ("general_admin_expense", "operating_income", "credit_loss_provision")
                                    for f in v[a][1]]


def review(code: str, year: int | None = None, period: str | None = None, data_dir: str | Path = DATA_DIR,
           today: str | None = None) -> Review:
    ds = Dataset(Path(data_dir))
    if code not in ds.companies:
        raise ReviewError(f"{code} is not one of the four groups in {data_dir}")
    if year is None or period is None:
        year, period = latest_report(ds, code)
    now = quarter(ds, code, year, period)
    if now is None:
        raise ReviewError(f"{code}: no {year} {period} report (or the report before it) in the dataset")
    py, (pp, dy) = quarter(ds, code, year - 1, period), PREV_Q[period]
    prev = quarter(ds, code, year + dy, pp)
    rv = Review(code, ds.companies[code].name, year, period, now, py, prev, _ttm_roe(ds, code, year, period),
                [t for t in vu.TARGETS if t.code == code and t.metric in ("roe", "cet1")])
    rv.checks = qc(rv, today or dt.date.today().isoformat())
    return rv


def qc(rv: Review, today: str) -> list[tuple[str, bool, str]]:
    """Model QC: the checks a reviewer would run before reading the numbers."""
    st, out = rv.now.cum, []
    flows_ok = [a for a in ("net_interest_income", "net_fee_income", "general_admin_expense", "operating_income")
                if a in rv.now.values and rv.now.values[a][0] > 0]
    out.append(("분기 단독 값(누적 차감)이 양수", len(flows_ok) == 4,
                "순이자이익·순수수료이익·일반관리비·영업이익" + ("" if len(flows_ok) == 4 else " 중 음수 또는 누락 있음")))
    cir = _cir(rv.now)
    out.append(("분기 CIR이 20~70 범위(단위 퍼센트)", bool(cir) and 20 <= cir[0] <= 70,
                "범위 밖이면 일회성 비용·계정 매핑 확인"))
    eq = all(k in st for k in ("equity_total", "equity_owners", "hybrid_capital")) and \
        st["equity_total"].value >= st["equity_owners"].value > st["hybrid_capital"].value > 0
    out.append(("자본총계 ≥ 지배주주지분 > 신종자본증권 > 0", eq, "자본 계정 매핑"))
    out.append(("비교 기간 보고서가 데이터셋에 있음", rv.year_ago is not None and rv.prev is not None,
                "전년 같은 분기와 직전 분기"))
    swings = [ar.ACCOUNT_KO[a] for a in ("net_interest_income", "net_fee_income")
              if rv.year_ago and a in rv.now.values and a in rv.year_ago.values and rv.year_ago.values[a][0] > 0
              and abs(_pct(rv.now.values[a][0], rv.year_ago.values[a][0])) > SWING]
    same = all(len({f.meta.get("concept") for q in (rv.now, rv.year_ago) for f in q.values[a][1]}) == 1
               for a in ("net_interest_income", "net_fee_income") if ar.ACCOUNT_KO[a] in swings)
    why = ("XBRL 계정은 두 해 같음: 실제 변동인지 회사의 재분류인지 확인" if same else "XBRL 계정이 달라짐: 계정 매핑 확인")
    out.append(("핵심 이익의 전년 대비 변동이 크지 않음", not swings,
                (f"{', '.join(swings)}: {why}" if swings else "순이자이익·순수수료이익")
                + "(기준: 전년 같은 분기 대비 40 퍼센트)"))
    meta = st["net_income_owners"].meta
    filed = meta.get("filed") or ""
    out.append(("보고서 접수일이 오늘 이전", bool(filed) and filed <= today, f"접수일 {filed or '없음'}"))
    form = meta.get("form") or ""
    out.append(("정정 보고서 여부 확인", True, f"{form}(정정본, 최초 접수와 숫자 비교 필요)" if "정정" in form else "원본 보고서"))
    return out


# ---------------------------------------------------------------------- the note (Korean Markdown)
def _pct(now: float, before: float) -> float:
    return (now / before - 1) * 100


def render(rv: Review) -> str:
    st = rv.now.cum["net_income_owners"].meta
    out = [f"# {rv.name} {rv.label} 실적 리뷰", "",
           f"> **{BANNER}.** 이 데스크의 DART 데이터셋만으로 규칙에 따라 쓴 초안이며 언어 모델이 쓰지 않았습니다. 모든 수치 뒤에 "
           "사실 ID와 접수번호를 붙였습니다. 금액은 억원 미만에서 반올림했습니다. DART에는 컨센서스가 없어 전년 같은 분기와 직전 "
           "분기, 밸류업 목표와 비교합니다. 실적발표 콘퍼런스콜은 다루지 않았습니다.", "",
           f"- **보고서:** {st.get('form') or '정기보고서'} {dart_link(st['rcept_no'])}, 접수일 {st.get('filed')}.",
           f"- **분기 단독:** {QUARTER[rv.period][0]} = " + (f"{CUM_KO[rv.period]} − 직전 누적 보고서" if QUARTER[rv.period][1]
                                                         else "1분기 보고서 그대로") + ".", ""]
    out += ["## 변동표", "", f"| 항목 | {rv.label} | 전년 같은 분기 | 전년 대비 | 직전 분기 | 직전 분기 대비 |",
            "|---|---|---|---|---|---|"]
    for a in LINES:
        if a not in rv.now.values:
            continue
        v, f = rv.now.values[a]
        cells = [ar.ACCOUNT_KO[a], cited_ko(v, "KRW", f)]
        for other in (rv.year_ago, rv.prev):
            if other and a in other.values:
                v0, f0 = other.values[a]
                cells += [cited_ko(v0, "KRW", f0), cited_ko(_pct(v, v0), "%", [*f, *f0]) if v0 > 0 else "-"]
            else:
                cells += ["-", "-"]
        out.append("| " + " | ".join(cells) + " |")
    cir_now = _cir(rv.now)
    if cir_now:
        cells = ["분기 CIR", cited_ko(cir_now[0], "%", cir_now[1])]
        for other in (rv.year_ago, rv.prev):
            c0 = _cir(other) if other else None
            cells += [cited_ko(c0[0], "%", c0[1]), cited_ko(cir_now[0] - c0[0], "pp", [*cir_now[1], *c0[1]])] if c0 \
                else ["-", "-"]
        out.append("| " + " | ".join(cells) + " |")
    out += ["", "## 자본과 밸류업 목표", ""]
    eq, hy = rv.now.cum["equity_owners"], rv.now.cum["hybrid_capital"]
    out.append(f"- 보통주자본(지배주주지분 − 신종자본증권): {cited_ko(eq.value - hy.value, 'KRW', [eq, hy])}.")
    if rv.ttm_roe:
        out.append(f"- TTM ROE(최근 12개월 지배주주순이익 ÷ 12개월 전과 지금의 지배주주지분 평균): "
                   f"{cited_ko(rv.ttm_roe[0], '%', rv.ttm_roe[1])}.")
    for t in rv.targets:
        tf = vu.target_fact(t)
        when = f"{t.deadline[:4]}년까지" if t.deadline else "기한 명시 없음"
        line = f"- 목표 {vu.METRIC_KO[t.metric]} {cited_ko(tf.value, '%', [tf])} 이상({when})"
        if t.metric == "roe" and rv.ttm_roe:
            gap = t.threshold - rv.ttm_roe[0]
            line += ": " + ("달성." if gap <= 0 else f"남은 격차 {cited_ko(gap, 'pp', [tf, *rv.ttm_roe[1]])}.")
        elif t.metric == "cet1":
            c = vu.cet1_fact(rv.code)
            same = c and c.id.endswith(f"{rv.year}{rv.period}")
            line += (f": 이번 보고서 기준 {cited_ko(c.value, '%', [c])}." if same else
                     ": 이번 보고서의 CET1 비율은 데이터셋에 없음(확인 필요).")
        out.append(line)
    if not rv.targets:
        out.append(f"- 밸류업 목표: 인용할 수 있는 수치 목표 없음. {vu.NOT_QUOTABLE.get(rv.code, '')}".rstrip())
    out += ["", "## 검사(QC)", "", "| 검사 | 결과 | 비고 |", "|---|---|---|"]
    out += [f"| {name} | {'통과' if ok else '확인 필요'} | {note} |" for name, ok, note in rv.checks]
    out += ["", "## 검토 전 확인할 것", "",
            "- 일회성 항목(희망퇴직 비용, 충당금 환입, 보험 계리가정 변경 등)은 조정하지 않았습니다.",
            "- 회사 실적발표 자료의 수치·정의(CIR, ROE)와 다를 수 있습니다. 배포 전 원문과 대조하십시오.",
            "- 투자의견·목표주가·주가가 없습니다.", ""]
    md = "\n".join(out).rstrip() + "\n"
    bad = uncited_figures_ko(md)
    if bad:
        raise ReviewError(f"uncited figures in the review: {', '.join(bad[:5])}")
    return md


def coverage_model(rv: Review, ds: Dataset | None = None) -> dict:
    """The 'model' a reviewer updates: every standalone quarter the dataset holds for the group, with sources."""
    ds = ds or Dataset(DATA_DIR)
    out = {"group": rv.code, "name": rv.name, "as_of_report": f"{rv.year}:{rv.period}", "quarters": {}}
    for key in sorted(ds.companies[rv.code].data["statements"], key=lambda k: (k.split(":")[0], ORDER[k.split(":")[1]])):
        y, p, basis = key.split(":")
        q = quarter(ds, rv.code, int(y), p) if basis == "CFS" else None
        if q:
            out["quarters"][f"{y} {QUARTER[p][0]}"] = {
                a: {"value": v, "facts": [f.id for f in fs], "rcept_no": sorted({f.meta["rcept_no"] for f in fs})}
                for a, (v, fs) in q.values.items()}
    return out


def write(rv: Review, out_dir: str | Path = OUT_DIR, data_dir: str | Path = DATA_DIR) -> Path:
    d = Path(out_dir) / f"{rv.code}_{rv.year}{rv.period}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "review.md").write_text(render(rv), encoding="utf-8", newline="\n")
    (d / "model.json").write_text(json.dumps(coverage_model(rv, Dataset(Path(data_dir))), ensure_ascii=False, indent=1)
                                  + "\n", encoding="utf-8", newline="\n")
    return d


# ---------------------------------------------------------------------- an optional model draft (paid)
SYSTEM = ("당신은 한국 금융지주를 담당하는 리서치 애널리스트의 실적 리뷰 담당입니다. 주어진 데이터 팩의 숫자만 쓰고, 모든 숫자에 "
          "근거를 붙입니다. 데이터에 없는 숫자는 쓰지 않고 '확인 필요'라고 적습니다. 투자의견, 목표주가, 주가는 쓰지 않습니다.")


def data_rows(rv: Review, data_dir: str | Path = DATA_DIR) -> list[tuple[Fact, str]]:
    ds = Dataset(Path(data_dir))
    c = ds.companies[rv.code]
    keys = {(rv.year, rv.period), (rv.year - 1, rv.period), (rv.year - 1, "FY")}
    for q in (rv.now, rv.year_ago, rv.prev):
        if q and QUARTER[q.period][1]:
            keys |= {(q.year, q.period), (q.year, QUARTER[q.period][1])}
        elif q:
            keys.add((q.year, q.period))
    rows = [(f, "") for y, p in sorted(keys, key=lambda k: (k[0], ORDER[k[1]])) for f in statement_facts(c, y, p, "CFS")]
    rows += [(vu.target_fact(t), f"원문: “{t.quote}”, 기한: {t.deadline[:4] + '년' if t.deadline else '명시 없음'}")
             for t in rv.targets]
    return rows


def draft_prompt(rv: Review, data_dir: str | Path = DATA_DIR) -> str:
    return (f"{rv.name}({rv.code})의 {rv.label} 실적 노트 초안을 한국어 마크다운으로 써 주세요(A4 1쪽 이내). 다룰 것: 분기 "
            "단독 실적(전년 같은 분기, 직전 분기와 비교), 비용 효율(CIR), 자본과 밸류업 목표 대비 진척, 검토자가 확인할 점.\n\n"
            f"정의: 분기 단독 = 이번 누적 − 직전 누적. CIR = 일반관리비 ÷ (영업이익 + 일반관리비 + 신용손실충당금 전입액) × 100. "
            "ROE = 최근 12개월 지배주주순이익 ÷ 12개월 전과 지금의 지배주주지분 평균 × 100.\n\n"
            f"{ar.DATA_TAIL}\n\n## 데이터 팩\n\n{ar.pack_markdown(data_rows(rv, data_dir))}\n")


def draft(client, rv: Review, out_dir: str | Path = OUT_DIR, max_cost: float = 1.0, max_tokens: int = 16000,
          data_dir: str | Path = DATA_DIR, log=print) -> dict:
    """One paid call, refused before calling when its worst case exceeds max_cost; then the rule fact-check."""
    d = Path(out_dir) / f"{rv.code}_{rv.year}{rv.period}"
    d.mkdir(parents=True, exist_ok=True)
    prompt = draft_prompt(rv, data_dir)
    from ..llm import PRICING
    p_in, p_out, _ = PRICING[client.model]
    bound = ((len(SYSTEM) + len(prompt)) * p_in + max_tokens * p_out) / 1e6  # one token per character, full output
    if bound > max_cost:
        raise ReviewError(f"worst case ${bound:.2f} exceeds --max-cost ${max_cost:.2f}")
    (d / "draft_prompt.md").write_text(prompt, encoding="utf-8", newline="\n")
    resp, usage = client.create(system=SYSTEM, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    (d / "draft.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    facts = {f.id: f for f, _ in data_rows(rv, data_dir)}
    records = ar.check_answer(text, ar.references(data_dir), facts)
    summary = ar.summarize(records, text)
    status = ("검토 가능" if not (summary["불일치"] or summary["인용 불일치"] or summary["인용 오류"])
              and resp.stop_reason == "end_turn" else "수정 필요")
    result = {"status": status, "summary": summary, "stop_reason": resp.stop_reason, "usage": usage,
              "model": getattr(resp, "model", client.model), "figures": records}
    (d / "draft_check.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                                        newline="\n")
    log(f"{rv.name} {rv.label}: draft {status} ({summary['figures']} figures, {summary['불일치']} mismatched, "
        f"{summary['인용 불일치'] + summary['인용 오류']} citation problems), ${usage['cost_usd']:.4f}")
    return result


def groups(arg: str) -> list[str]:
    if arg in ("all", "전체"):
        return list(FHC.values())
    names = {**{v: v for v in FHC.values()}, **{n: c for c, n in vu.NAMES.items()}, **{g: c for g, c in FHC.items()}}
    if arg not in names:
        raise ReviewError(f"unknown group {arg!r}; use all, a stock code, or one of {', '.join(vu.NAMES.values())}")
    return [names[arg]]
