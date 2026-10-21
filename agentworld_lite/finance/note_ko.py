"""한국어 섹터 노트: 끝난 궤적의 제출 사실(fact)을 인용과 함께 정리한다. 언어 모델은 쓰지 않는다.

The Korean counterpart of note.py for the DART desk (4대 금융지주). The trajectory is replayed on a fresh desk exactly as
in note.py, which recovers every fact with its provenance, and the replay must reproduce the recorded submission. Every
figure is followed by its fact id and the DART receipt numbers (접수번호) behind it; a price cites its stated source:

    1조 9,922억원 [사실 `calc.kb_q2_net_income_owners` · 접수번호 20260814004200, 20260515002888]

`uncited_figures_ko` finds figures without one (amounts in 원·억원·조, 배, %, %p, 주, or any number with a thousands
separator or decimal point); `sector_note_ko` runs it on its own output before returning.
"""

from __future__ import annotations

import re

from ..task import Task
from .catalog import FHC
from .dart import FHC_ACCOUNT_RULES
from .dataset import PERIODS, Fact
from .env import FinanceEnv
from .note import NoteError, _leaves, replay

PERIOD_KO = {"Q1": "1분기", "H1": "상반기 누적", "Q3": "3분기 누적", "FY": "연간"}
PERIOD_END_KO = {"Q1": "1분기 말", "H1": "상반기 말", "Q3": "3분기 말", "FY": "연말"}
BALANCE = {k for k, (kinds, _, _) in FHC_ACCOUNT_RULES.items() if "BS" in kinds}
REPORT_KO = {"Q1": "1분기", "H1": "반기", "Q3": "3분기", "FY": "사업연도"}
METRIC_KO = {
    "q2_net_income_owners": "2분기 지배주주순이익",
    "q2_cost_income_ratio": "2분기 영업이익경비율(CIR)",
    "ttm_net_income_owners": "TTM 지배주주순이익",
    "common_equity_average": "4개 그룹 보통주자본 평균",
    "common_equity_spread": "보통주자본 최대-최소 차",
    "bps": "BPS(주당 보통주자본)",
    "ttm_roe": "TTM ROE",
    "q2_yoy_growth": "2분기 지배주주순이익 전년 대비 증가율",
    "sector_q2_yoy_growth": "4개 그룹 합산 2분기 증가율",
    "sector_cost_income_ratio": "4개 그룹 합산 CIR",
    "cost_income_spread_pp": "그룹별 CIR 최고-최저 차",
    "roe_gap_pp": "ROE 목표까지 남은 격차",
}
ID_NOTE = ("모든 수치 뒤에 사실 ID와 그 수치가 나온 DART 접수번호를 붙였습니다. 계산한 수치는 계산에 들어간 보고서의 "
           "접수번호를 모두 적습니다. 금액은 억원 미만에서 반올림했습니다.")


# ---------------------------------------------------------------------- figures and citations
def fmt_krw(value: float) -> str:
    """조·억 단위, 억원 미만 반올림. 1억원 미만(주가 등)은 원 단위."""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v < 1e8:
        return f"{sign}{v:,.0f}원"
    eok = round(v / 1e8)
    jo, rest = divmod(eok, 10_000)
    if not jo:
        return f"{sign}{rest:,}억원"
    return f"{sign}{jo}조 {rest:,}억원" if rest else f"{sign}{jo}조원"


def fmt_figure_ko(value: float, unit: str) -> str:
    if unit == "KRW":
        return fmt_krw(value)
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "pp":
        return f"{value:,.2f}%p"
    if unit == "x":
        return f"{value:,.2f}배"
    if unit == "shares":
        return f"{value:,.0f}주"
    return f"{value:,.2f} {unit}".strip()


def _sources(facts: list[Fact]) -> str:
    rcepts = list(dict.fromkeys(f.meta["rcept_no"] for f in facts if f.meta.get("rcept_no")))
    others = list(dict.fromkeys(
        f"종가 출처 {f.meta.get('source')}" if f.meta.get("date") else str(f.meta.get("source") or "과제 입력")
        for f in facts if not f.meta.get("rcept_no")))
    parts = ([f"접수번호 {', '.join(rcepts)}"] if rcepts else []) + others
    return "; ".join(parts) or "과제 입력"


def cite_ko(env: FinanceEnv, fid: str) -> str:
    return f"[사실 `{fid}` · {_sources([env.facts[x] for x in _leaves(env, fid)])}]"


def figure_ko(env: FinanceEnv, fid: str) -> str:
    f = env.facts[fid]
    return f"{fmt_figure_ko(f.value, f.unit)} {cite_ko(env, fid)}"


def cite_facts_ko(facts: list[Fact]) -> str:
    """Citation for a figure computed outside a desk run from dataset facts (statement, share or price facts)."""
    facts = list({f.id: f for f in facts}.values())
    return f"[사실 {', '.join(f'`{f.id}`' for f in facts)} · {_sources(facts)}]"


def cited_ko(value: float, unit: str, facts: list[Fact]) -> str:
    return f"{fmt_figure_ko(value, unit)} {cite_facts_ko(facts)}"


CITATION_KO = re.compile(r"\[사실 `[^`]+`(?:, `[^`]+`)* · [^\]]*\]")
_CODE = re.compile(r"`[^`]*`")
_UNIT = r"(?:억원|백만원|원|주|배|%p|%)"
_FIGURE_KO = re.compile(r"(?<![\w.,])-?(?:\d+조(?: \d{1,3}(?:,\d{3})*억)?원"
                        rf"|\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?{_UNIT}?|\d+\.\d+{_UNIT}?|\d+{_UNIT})")


_QUOTE = re.compile(r"“[^”]*”")


def uncited_figures_ko(markdown: str) -> list[str]:
    """Figures not immediately followed by a citation. Headings, code spans (fact ids, formulas) and the citations
    themselves are skipped, so years, dates, 접수번호 and period names such as 2분기 do not count. Wording quoted from a
    filing in “curly quotes” is not checked figure by figure, but the quote itself must be followed by a citation."""
    bad = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("#"):
            continue
        text = CITATION_KO.sub("\x00", line)
        bad += [q.group(0)[:40] for q in _QUOTE.finditer(text) if not text[q.end():].startswith(" \x00")]
        text = _CODE.sub("\x01", _QUOTE.sub("\x01", text))
        for m in _FIGURE_KO.finditer(text):
            if not text[m.end():].lstrip("*").startswith(" \x00"):
                bad.append(m.group(0))
    return bad


# ---------------------------------------------------------------------- the note
def dart_link(rcept_no: str) -> str:
    return f"[{rcept_no}](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no})"


def period_ko(year: int, period: str, account: str | None = None) -> str:
    """Flows are cumulative from January ("2026년 상반기 누적"); balances are period-end ("2026년 상반기 말")."""
    return f"{year}년 {(PERIOD_END_KO if account in BALANCE else PERIOD_KO)[period]}"


def _metric_name(metric: str) -> str:
    return METRIC_KO.get(metric, f"`{metric}`")


def _account_label(env: FinanceEnv, fact: Fact) -> str:
    m = fact.meta
    company = env.dataset.companies[m["company"]]
    label = company.accounts().get(m["account"], (m["account"],))[0]
    return f"{label} ({period_ko(m['year'], m['period'], m['account'])})"


def _comparison(env: FinanceEnv, fields: dict[str, str]) -> tuple[list[tuple[str, dict]], list[str]]:
    """Columns of one fact per group: per-group answers (kb_x, shinhan_x, ...) and, for the other answers, inputs that
    take the same account of the same period from several groups (e.g. each group's owners' equity)."""
    groups: dict[str, dict] = {}
    for fld, fid in fields.items():
        prefix, _, metric = fld.partition("_")
        if prefix in FHC and metric:
            groups.setdefault(metric, {})[FHC[prefix]] = fid
    grouped = {f"{g}_{metric}" for metric, by in groups.items() if len(by) > 1 for g in FHC if FHC[g] in by}
    columns = [(_metric_name(metric), by) for metric, by in groups.items() if len(by) > 1]
    others = [f for f in fields if f not in grouped]
    inputs: dict[tuple, dict] = {}
    for f in others:
        for sub in _leaves(env, fields[f]):
            m = env.facts[sub].meta
            if m.get("account") and m.get("company"):
                inputs.setdefault((m["account"], m["year"], m["period"]), {})[m["company"]] = sub
    for (account, year, period), by in inputs.items():
        if len(by) > 1:
            label = env.dataset.companies[next(iter(by))].accounts().get(account, (account,))[0]
            columns.append((f"{label} ({period_ko(year, period, account)})", by))
    return columns, others


def _derivation(env: FinanceEnv, fid: str, depth: int = 0) -> list[str]:
    fact = env.facts[fid]
    if fact.meta.get("source") != "calculation":
        return []
    lines = []
    for var, sub in fact.meta["inputs"].items():
        s = env.facts[sub]
        if s.meta.get("source") == "calculation":
            what = f" = `{s.meta['formula']}`, 여기서:"
        elif s.meta.get("account"):
            what = f": {env.dataset.companies[s.meta['company']].name} {_account_label(env, s)}"
        else:
            what = f": {s.label}"
        lines.append(f"{'  ' * depth}- `{var}` = {figure_ko(env, sub)}{what}")
        lines += _derivation(env, sub, depth + 1)
    return lines


def sources_table(env: FinanceEnv, facts: list[Fact]) -> list[str]:
    """One row per DART report behind `facts`: group, period, report, 접수번호, filing date and the original filing."""
    reports = {}
    for f in facts:
        m = f.meta
        if m.get("rcept_no") and m.get("account") and m.get("company"):
            reports.setdefault((m["company"], m["rcept_no"]), f)
    if not reports:
        return []
    out = ["| 금융지주 | 기간 | 보고서 | 접수번호 | 접수일 | 최초 접수 |", "|---|---|---|---|---|---|"]
    rank = {c: i for i, c in enumerate(FHC.values())}
    rows = sorted(reports.items(), key=lambda kv: (rank.get(kv[0][0], 99), kv[1].meta["year"],
                                                   PERIODS.index(kv[1].meta["period"])))
    for (code, rcept_no), f in rows:
        company = env.dataset.companies[code]
        st = company.statement(f.meta["year"], f.meta["period"], f.meta["basis"]) or {}
        first = (f"{st['first_filed']} ({dart_link(st['first_rcept_no'])})" if st.get("first_rcept_no")
                 else "같음")
        out.append(f"| {company.name} | {f.meta['year']}년 {REPORT_KO[f.meta['period']]} | "
                   f"{st.get('form') or 'DART 정기보고서'} | {dart_link(rcept_no)} | {st.get('filed') or f.meta.get('filed')} | "
                   f"{first} |")
    return out


SOURCE_NOTE_KO = ("수치는 DART OpenAPI(전체 재무제표 `fnlttSinglAcntAll`, 주식 총수 `stockTotqySttus`)에서 읽은 연결 "
                  "재무제표 원값입니다. 정정 보고서가 있으면 DART는 정정본의 수치와 접수번호를 돌려주므로, 접수일은 "
                  "정정일이고 처음 접수된 보고서는 '최초 접수' 열에 적었습니다. 기준일 가드는 정정일을 씁니다. 회사가 "
                  "실적 발표에서 제외하는 일회성 항목은 조정하지 않았습니다.")


def sector_note_ko(traj: dict, task: Task, *, require_success: bool = True) -> str:
    env = replay(traj, task)
    order = [cp["field"] for cp in task.verifier["checkpoints"]]
    fields = {f: env.submission[f] for f in order if f in env.submission}
    fields.update({f: fid for f, fid in env.submission.items() if f not in fields})
    if not fields:
        raise NoteError("the team submitted no answers")
    done = {cp["name"]: cp["done"] for cp in traj["result"]["checkpoints"]}
    by_field = {cp["field"]: done.get(cp.get("name") or cp["field"], False) for cp in task.verifier["checkpoints"]}
    n_ok = sum(by_field.values())
    if require_success and not traj["result"]["success"]:
        raise NoteError(f"only {n_ok} of {len(by_field)} answers are within tolerance (pass --allow-failed to write anyway)")

    title = task.meta.get("name_ko") or task.name
    agent = (traj.get("meta") or {}).get("model") or (traj.get("meta") or {}).get("agent") or "unknown"
    verdict = ("모든 답이 과제의 허용 오차 안에 있습니다" if n_ok == len(by_field)
               else f"답 {len(by_field)}개 중 {n_ok}개만 허용 오차 안에 있습니다")
    out = [
        f"# {title}", "",
        "*데스크가 제출한 사실(fact)만으로 고정된 틀에 맞춰 쓴 섹터 노트입니다. 언어 모델이 쓰지 않았습니다.*", "",
        f"> {task.meta.get('description_ko') or task.description}", "",
        f"- **실행:** `{task.id}`, 설정 `{traj.get('setting', 'full')}`, 시드 `{traj.get('seed', '')}`, 에이전트 `{agent}`, "
        f"라운드 `{traj['result']['rounds_used']}` / `{traj.get('max_rounds', task.max_rounds)}`.",
        f"- **기준일:** {env.as_of}. 이 날 이후 접수된 보고서는 팀이 볼 수 없었습니다.",
        f"- **검증:** {verdict}.", "",
        ID_NOTE, "",
    ]
    columns, others = _comparison(env, fields)
    if columns:
        first = columns[0][1]
        codes = sorted(dict.fromkeys(c for _, by in columns for c in by),
                       key=lambda c: (c not in first, -(env.facts[first[c]].value if c in first else 0)))
        out += ["## 그룹 비교", "", f"{columns[0][0]} 기준 내림차순입니다.", "",
                "| 금융지주 | " + " | ".join(h for h, _ in columns) + " |", "|---|" + "---|" * len(columns)]
        for c in codes:
            cells = [figure_ko(env, by[c]) if c in by else "해당 없음" for _, by in columns]
            out.append(f"| {env.dataset.companies[c].name} ({c}) | " + " | ".join(cells) + " |")
        out.append("")
    if others:
        out += ["## 섹터 전체" if columns else "## 답", ""]
        out += [f"- {_metric_name(f)} (`{f}`): {figure_ko(env, fields[f])}" for f in others]
        out.append("")

    out += ["## 계산 과정", ""]
    for f, fid in fields.items():
        fact = env.facts[fid]
        check = "허용 오차 이내" if by_field.get(f) else "허용 오차 밖"
        out += [f"### `{f}` ({check})", ""]
        if fact.meta.get("source") == "calculation":
            out += [f"{figure_ko(env, fid)} = `{fact.meta['formula']}`, 여기서:", ""]
            out += _derivation(env, fid)
        else:
            out.append(f"{figure_ko(env, fid)}: {fact.label}.")
        out.append("")

    leaves = list(dict.fromkeys(x for fid in fields.values() for x in _leaves(env, fid)))
    table = sources_table(env, [env.facts[x] for x in leaves])
    if table:
        out += ["## 출처", "", *table, ""]
    out.append(SOURCE_NOTE_KO)
    note = "\n".join(out).rstrip() + "\n"
    bad = uncited_figures_ko(note)
    if bad:
        raise NoteError(f"uncited figures in the note: {', '.join(bad)}")
    return note
