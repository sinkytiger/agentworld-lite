"""Streamlit demo of the Korean research desk (4대 금융지주): replay a run, compare the four groups' DART figures,
check their value-up targets against the filings, and read the cited Korean sector note. The desk uses DART filings
only and the repository ships no prices, so the valuation view appears only after sourced closing prices are added to
the dataset (fetch --price ... --price-source ...).

    pip install streamlit
    streamlit run demo/streamlit_app_kr.py

No model, no API key and no network: every view reads the committed dataset (data/finance/dart_fhc), value-up file
(data/finance/valueup) and tasks (tasks/finance_kr). A run is either a task's scripted reference solution, replayed
here, or a trajectory JSON written by `python -m agentworld_lite.finance run` (upload it in the sidebar). Labels are
Korean by default; the sidebar switches them to English (the generated note and scorecard stay Korean, as filed).
Query parameters open a given state:
?task=<task id>&view=<view, Korean or English label>&round=<n>&group=<stock code>&lang=en
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agentworld_lite.finance.catalog import FHC  # noqa: E402
from agentworld_lite.finance.dataset import Dataset  # noqa: E402
from agentworld_lite.finance.note import NoteError, replay  # noqa: E402
from agentworld_lite.finance.note_ko import sector_note_ko  # noqa: E402
from agentworld_lite.finance.run import make_factory, run_episode  # noqa: E402
from agentworld_lite.finance.valuation import ValuationError  # noqa: E402
from agentworld_lite.finance.valuation_ko import NOTICE, prices_ready, snapshot_ko  # noqa: E402
from agentworld_lite.finance import valueup  # noqa: E402
from agentworld_lite.task import load_tasks  # noqa: E402

TASK_DIR = ROOT / "tasks" / "finance_kr"
DATA_DIR = ROOT / "data" / "finance" / "dart_fhc"
BASE_VIEWS = ("데스크 재생", "4대 금융지주 데이터", "밸류업 점검", "섹터 노트", "과제")
VALUATION = "밸류에이션"  # listed only when every group has a sourced closing price
VIEW_EN = {"데스크 재생": "Desk replay", "4대 금융지주 데이터": "Group data", "밸류업 점검": "Value-up check",
           "섹터 노트": "Sector note (Korean)", "과제": "Task", VALUATION: "Valuation"}
DEFAULT_TASK = "k03_big4_q2_sector_shared"
CODES = tuple(FHC.values())
SERIES = {"light": "#2a78d6", "dark": "#3987e5"}  # one series: categorical slot 1 of the reference palette
INK = {"light": "#0b0b0b", "dark": "#f0efec"}     # values and labels wear text ink, not the series color
TOOL_KO = {"fetch_financials": "재무제표 조회", "fetch_shares": "주식 수 조회", "calculate": "계산",
           "share_facts": "사실 공유", "submit_answer": "답 제출", "send_chat": "채팅", "wait": "대기"}
TOOL_EN = {"fetch_financials": "fetch financials", "fetch_shares": "fetch shares", "calculate": "calculate",
           "share_facts": "share facts", "submit_answer": "submit answers", "send_chat": "chat", "wait": "wait"}
ROLE_KO = {"data collector": "공시 수집", "analyst": "분석", "reporter": "제출"}
PERIOD_KO = {"Q1": "1분기", "H1": "상반기", "Q3": "3분기", "FY": "연간"}
NAMES_EN = {"105560": "KB Financial", "055550": "Shinhan Financial", "086790": "Hana Financial",
            "316140": "Woori Financial"}
STATUS_EN = {"달성": "met", "순항": "on track", "지연": "behind", "미달(기한 미명시)": "short (no deadline)"}
METRIC_EN = {"roe": "ROE", "cet1": "CET1 ratio", "payout": "Shareholder return ratio", "shares": "Shares outstanding",
             "policy": "Return policy"}
FORM_EN = {"반기보고서": "Half-year report", "분기보고서": "Quarterly report", "사업보고서": "Annual report",
           "[기재정정]반기보고서": "Half-year report (amended)", "[기재정정]사업보고서": "Annual report (amended)"}
LANG = "ko"  # set from the sidebar on every run


def tr(ko: str, en: str) -> str:
    return en if LANG == "en" else ko


def group_name(code: str) -> str:
    return NAMES_EN[code] if LANG == "en" else valueup.NAMES.get(code) or kr_dataset().companies[code].name


def en_names(text: str) -> str:
    """English group names inside English text that kept the Korean ones (task names and briefs)."""
    for code, ko in valueup.NAMES.items():
        text = text.replace(ko, NAMES_EN[code])
    return text.replace("KB금융", NAMES_EN["105560"])


# ---------------------------------------------------------------------- data
# Names differ from the Canadian app's: Streamlit keys its caches on the function's source, and the two apps can share
# a process (the tests run both).
@st.cache_resource
def kr_tasks() -> dict:
    return {t.id: t for t in load_tasks(str(TASK_DIR))}


@st.cache_resource
def kr_dataset() -> Dataset:
    return Dataset(DATA_DIR)


@st.cache_data
def kr_scorecard() -> str:
    return valueup.render_scorecard()


@st.cache_data
def kr_reference_run(task_id: str) -> dict:
    return run_episode(kr_tasks()[task_id], make_factory("scripted"),
                       meta={"agent": "scripted", "model": "scripted reference"})


def _mode() -> str:
    theme = getattr(getattr(st, "context", None), "theme", None)
    return "dark" if getattr(theme, "type", None) == "dark" else "light"


def series_color() -> str:
    return SERIES[_mode()]


def ink() -> str:
    return INK[_mode()]


def title_of(task) -> str:
    return en_names(task.name) if LANG == "en" else task.meta.get("name_ko") or task.name


def tool_name(tool: str) -> str:
    return (TOOL_EN if LANG == "en" else TOOL_KO).get(tool, tool)


def role_name(role: str) -> str:
    return role if LANG == "en" else ROLE_KO.get(role, role)


def summary(action: dict) -> str:
    a = action.get("args") or {}
    tool = action["tool"]
    if tool in ("fetch_financials", "fetch_shares"):
        c = kr_dataset().find(str(a.get("company", "")))
        name = (group_name(c.stock_code) if c else a.get("company"))
        if LANG == "en":
            return f"{name} {a.get('year')} {a.get('period')}"
        return f"{name} {a.get('year')}년 {PERIOD_KO.get(str(a.get('period')), a.get('period'))}"
    if tool == "calculate":
        return f"`{a.get('name')}` = `{a.get('formula')}`"
    if tool == "share_facts":
        ids = a.get("fact_ids") or []
        n = len(ids) if isinstance(ids, list) else 1
        return tr(f"사실 {n}개 → {a.get('to_agent')}", f"{n} fact(s) to {a.get('to_agent')}")
    if tool == "submit_answer":
        answers = a.get("answers") or []
        n = len(answers) if isinstance(answers, list) else 1
        return tr(f"답 {n}개", f"{n} answer(s)")
    if tool == "send_chat":
        return tr(f"{a.get('to')}에게: ", f"to {a.get('to')}: ") + str(a.get("message", ""))[:80]
    return str(a.get("reason", ""))


# ---------------------------------------------------------------------- views
def view_replay(task, traj: dict, start_round: int | None) -> None:
    res = traj["result"]
    done = sum(cp["done"] for cp in res["checkpoints"])
    cols = st.columns(4)
    cols[0].metric(tr("사용한 라운드", "Rounds used"), f"{res['rounds_used']} / {traj['max_rounds']}")
    cols[1].metric(tr("허용 오차 안의 답", "Answers within tolerance"), f"{done} / {len(res['checkpoints'])}")
    cols[2].metric(tr("도구 호출", "Tool calls"), res["n_actions"])
    cols[3].metric(tr("실패한 호출", "Failed calls"), res["n_failed_actions"])

    last = max(1, res["rounds_used"])
    default = min(start_round or last, last)
    r = st.slider(tr("라운드", "Round"), 1, last, default) if last > 1 else 1
    snap = traj["snapshots"][min(r, len(traj["snapshots"]) - 1)]

    for col, spec in zip(st.columns(len(task.agents)), task.agents):
        name = spec["username"]
        acts = [x for x in traj["actions"] if x["agent"] == name and x["round"] <= r]
        moves = [x for x in acts if x["tool"] != "wait"]
        with col, st.container(border=True):
            st.subheader(name)
            banned = ", ".join(tool_name(t) for t in spec.get("restricted_tools") or []) or tr("없음", "none")
            st.caption(tr(f"{role_name(spec.get('role', ''))} 담당. 쓸 수 없는 도구: {banned}",
                          f"Role: {role_name(spec.get('role', ''))}. Tools it cannot use: {banned}"))
            st.metric(tr("노트북의 사실", "Facts in its notebook"), len(snap["agents"][name]["inventory"]),
                      help=tr("이 에이전트가 가진 사실 ID 수. 계산과 답에는 이것만 쓸 수 있습니다.",
                              "Fact IDs this agent holds. Calculations and answers can use only these."))
            for x in moves[-4:]:
                mark = tr(":green[성공]", ":green[ok]") if x["ok"] else tr(":red[실패]", ":red[failed]")
                st.markdown(f"`R{x['round']}` **{tool_name(x['tool'])}** {mark}  \n{summary(x)}")
            if moves:
                st.caption(moves[-1]["message"].splitlines()[0][:160])
            st.caption(tr(f"지금까지 대기한 턴: {len(acts) - len(moves)}", f"Turns spent waiting: {len(acts) - len(moves)}"))

    st.markdown(tr(f"**{r}라운드가 끝났을 때의 답**", f"**Answers at the end of round {r}**"))
    rows = [{tr("답", "Answer"): cp["name"],
             tr("상태", "Status"): (tr("허용 오차 이내", "within tolerance") if cp["done"] else tr("아직", "not yet"))}
            for cp in snap["checkpoints"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def gcol() -> dict:
    """Column names of the group table; amounts in 억원/조원 (Korean) or KRW billion/trillion (English)."""
    return {"group": tr("금융지주", "Group"), "code": tr("종목코드", "Stock code"),
            "ni": tr("2분기 지배주주순이익(억원)", "Q2 net income to owners (KRW bn)"), "cir": tr("2분기 CIR(%)", "Q2 CIR (%)"),
            "ce": tr("보통주자본(조원)", "Common equity (KRW tn)"),
            "nii": tr("2분기 순이자이익(억원)", "Q2 net interest income (KRW bn)"),
            "fee": tr("2분기 순수수료이익(억원)", "Q2 net fee income (KRW bn)"),
            "ni_h1": tr("상반기 지배주주순이익(억원)", "H1 net income to owners (KRW bn)"),
            "h1": tr("반기보고서", "Half-year report"), "filed": tr("접수일", "Filed"), "q1": tr("1분기보고서", "Q1 report")}


def groups_frame(ds: Dataset) -> pd.DataFrame:
    c_, unit = gcol(), (1e9 if LANG == "en" else 1e8)
    form = (lambda f: FORM_EN.get(f, f)) if LANG == "en" else (lambda f: f)
    rows = []
    for code in CODES:
        c = ds.companies[code]
        q1, h1 = c.statement(2026, "Q1", "CFS"), c.statement(2026, "H1", "CFS")
        q2 = {a: h1[a] - q1[a] for a in ("net_income_owners", "general_admin_expense", "operating_income",
                                         "credit_loss_provision", "net_interest_income", "net_fee_income")}
        revenue = q2["operating_income"] + q2["general_admin_expense"] + q2["credit_loss_provision"]
        rows.append({
            c_["group"]: group_name(code), c_["code"]: code,
            c_["ni"]: q2["net_income_owners"] / unit,
            c_["cir"]: q2["general_admin_expense"] / revenue * 100,
            c_["ce"]: (h1["equity_owners"] - h1["hybrid_capital"]) / 1e12,
            c_["nii"]: q2["net_interest_income"] / unit,
            c_["fee"]: q2["net_fee_income"] / unit,
            c_["ni_h1"]: h1["net_income_owners"] / unit,
            c_["h1"]: f"{form(h1.get('form', '반기보고서'))} {h1['rcept_no']}", c_["filed"]: h1["filed"],
            c_["q1"]: f"{form(q1.get('form', '분기보고서'))} {q1['rcept_no']}",
        })
    return pd.DataFrame(rows)


def hbar(df: pd.DataFrame, field: str, title: str, fmt: str) -> alt.Chart:
    """Magnitude: bars from zero, highest first, value at the tip in text ink."""
    g = gcol()["group"]
    order = df.sort_values(field, ascending=False)[g].tolist()
    base = alt.Chart(df, title=title).encode(
        y=alt.Y(f"{g}:N", sort=order, title=None),
        x=alt.X(f"{field}:Q", title=None, scale=alt.Scale(domain=[0, df[field].max() * 1.2]),
                axis=alt.Axis(grid=False, labels=False, ticks=False, domain=False)),
        tooltip=[alt.Tooltip(f"{g}:N"), alt.Tooltip(f"{field}:Q", format=fmt)],
    )
    bars = base.mark_bar(size=18, cornerRadiusEnd=4, color=series_color())
    labels = base.mark_text(align="left", dx=6, color=ink()).encode(text=alt.Text(f"{field}:Q", format=fmt))
    return (bars + labels).properties(height=200)


def dots(df: pd.DataFrame, field: str, title: str, fmt: str) -> alt.Chart:
    """Ratios bunched near one level: dots on a zoomed axis, with the average as a dashed rule."""
    g = gcol()["group"]
    order = df.sort_values(field, ascending=True)[g].tolist()
    lo, hi = df[field].min(), df[field].max()
    x = alt.X(f"{field}:Q", title=None, scale=alt.Scale(domain=[lo - 1.5, hi + 1.5], zero=False),
              axis=alt.Axis(grid=True, gridOpacity=0.25, tickCount=5, domain=False, ticks=False))
    base = alt.Chart(df, title=title).encode(y=alt.Y(f"{g}:N", sort=order, title=None), x=x,
                                             tooltip=[alt.Tooltip(f"{g}:N"), alt.Tooltip(f"{field}:Q", format=fmt)])
    points = base.mark_circle(size=140, opacity=1, color=series_color())
    labels = base.mark_text(align="left", dx=10, color=ink()).encode(text=alt.Text(f"{field}:Q", format=fmt))
    mean = alt.Chart(pd.DataFrame({"avg": [df[field].mean()]})).mark_rule(strokeDash=[4, 4], color=ink(), opacity=0.5
                                                                           ).encode(x="avg:Q")
    return (mean + points + labels).properties(height=200)


def view_groups() -> None:
    df, c_ = groups_frame(kr_dataset()), gcol()
    st.markdown(tr("2026년 2분기(4~6월) 단독 = 상반기 누적 − 1분기. 보통주자본 = 지배주주지분 − 신종자본증권(2026-06-30). "
                   "CIR = 일반관리비 / (영업이익 + 일반관리비 + 신용손실충당금 전입액). 모든 값은 각 그룹이 DART에 낸 "
                   "연결 재무제표 원값입니다.",
                   "Q2 2026 (April to June) alone = H1 year-to-date minus Q1. Common equity = equity attributable to "
                   "owners minus hybrid capital (2026-06-30). CIR = general and administrative expense / (operating "
                   "income + G&A + credit-loss provisions). Every value is as filed on DART in the group's consolidated "
                   "statements.").replace("~", "\\~"))
    left, mid, right = st.columns(3)
    left.altair_chart(hbar(df, c_["ni"], tr("2분기 지배주주순이익 (억원)", "Q2 net income to owners (KRW bn)"),
                           ",.0f"), width="stretch")
    mid.altair_chart(dots(df, c_["cir"], tr("2분기 영업이익경비율 CIR (%)", "Q2 cost-to-income ratio, CIR (%)"),
                          ".1f"), width="stretch")
    right.altair_chart(hbar(df, c_["ce"], tr("보통주자본, 2026-06-30 (조원)", "Common equity, 2026-06-30 (KRW tn)"),
                            ",.1f"), width="stretch")
    amended = [(group_name(x), s["filed"], s["first_filed"]) for x in CODES
               for s in [kr_dataset().companies[x].statement(2026, "H1", "CFS")] if s.get("first_rcept_no")]
    mean_cir, ce = df[c_["cir"]].mean(), df[c_["ce"]]
    if LANG == "en":
        st.caption(f"Dashed line: simple average CIR {mean_cir:.2f}%. Common equity averages KRW {ce.mean():.2f} "
                   f"trillion, highest minus lowest KRW {ce.max() - ce.min():.2f} trillion. "
                   + " ".join(f"{n}'s half-year report was amended on {f} (first filed {o})." for n, f, o in amended))
    else:
        st.caption(f"점선: CIR 단순 평균 {mean_cir:.2f}%. 보통주자본 평균 {ce.mean():.2f}조원, 최대-최소 차 "
                   f"{ce.max() - ce.min():.2f}조원. "
                   + " ".join(f"{n}의 반기보고서는 {f}에 정정되었습니다(최초 접수 {o})." for n, f, o in amended))
    st.markdown(tr("**표: 각 수치가 나온 보고서와 접수번호**", "**Table: the report and DART receipt number behind each "
                                                       "figure**"))
    config = {c: st.column_config.NumberColumn(format="%,.0f") for c in (c_["ni"], c_["nii"], c_["fee"], c_["ni_h1"])}
    config[c_["cir"]] = st.column_config.NumberColumn(format="%.2f")
    config[c_["ce"]] = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(df, hide_index=True, column_config=config)


def vcol() -> dict:
    return {"group": tr("금융지주", "Group"), "metric": tr("지표", "Measure"), "target": tr("목표", "Target"),
            "dir": tr("방향", "Direction"), "deadline": tr("기한", "Deadline"), "now": tr("현재", "Now"),
            "prev": tr("1년 전", "A year earlier"), "gap": tr("남은 격차", "Gap to go"), "status": tr("판정", "Verdict"),
            "rcept": tr("목표 공시", "Target filing")}


def valueup_frame() -> pd.DataFrame:
    c_ = vcol()
    rows = []
    for r in valueup.scorecard_rows():
        t = r["target"]
        shares = t.metric == "shares"
        scale = 1e6 if shares else 1  # shares in millions, the unit of Shinhan's own target
        label = (METRIC_EN[t.metric] + (" (millions)" if shares else " (%)") if LANG == "en"
                 else valueup.METRIC_KO[t.metric] + (" (백만주)" if shares else " (%)"))
        rows.append({
            c_["group"]: group_name(t.code), c_["metric"]: label,
            c_["target"]: None if t.threshold is None else t.threshold / scale,
            c_["dir"]: {">=": tr("이상", "at least"), "<=": tr("이하", "at most")}.get(t.direction, tr("방식", "policy")),
            c_["deadline"]: t.deadline[:4] if t.deadline else tr("명시 없음", "none stated"),
            c_["now"]: r["now"][0] / scale if r["now"] else None,
            c_["prev"]: r["prev"][0] / scale if r["prev"] else None,
            c_["gap"]: (r["gap"] / scale) if r["gap"] else None,
            c_["status"]: STATUS_EN.get(r["status"], r["status"]) if LANG == "en" else r["status"],
            c_["rcept"]: t.rcept_no, "metric": t.metric,
        })
    return pd.DataFrame(rows)


def progress_chart(df: pd.DataFrame, metric: str, title: str) -> alt.Chart:
    """Now (filled), a year earlier (hollow) and the target (tick) per group, on a zoomed axis."""
    c_ = vcol()
    g, now_c, prev_c, tgt_c = c_["group"], c_["now"], c_["prev"], c_["target"]
    d = df[df["metric"] == metric]
    vals = pd.concat([d[now_c], d[prev_c].dropna(), d[tgt_c]])
    x = alt.X("v:Q", title=None, scale=alt.Scale(domain=[vals.min() - 0.8, vals.max() + 0.8], zero=False),
              axis=alt.Axis(grid=True, gridOpacity=0.25, tickCount=5, domain=False, ticks=False))
    y = alt.Y(f"{g}:N", title=None, sort=list(d[g]))
    long = d.melt(id_vars=[g], value_vars=[now_c, prev_c, tgt_c], var_name="k", value_name="v").dropna()
    tip = [alt.Tooltip(f"{g}:N"), alt.Tooltip("k:N", title=tr("값", "Value")), alt.Tooltip("v:Q", format=".2f")]
    base = alt.Chart(long, title=title).encode(y=y, x=x, tooltip=tip)
    target = base.transform_filter(alt.datum["k"] == tgt_c).mark_tick(thickness=2, size=22, color=ink())
    prev = base.transform_filter(alt.datum["k"] == prev_c).mark_point(size=110, filled=False, color=series_color())
    now = base.transform_filter(alt.datum["k"] == now_c).mark_circle(size=140, opacity=1, color=series_color())
    label = base.transform_filter(alt.datum["k"] == now_c).mark_text(align="left", dx=10, dy=-10, color=ink()).encode(
        text=alt.Text("v:Q", format=".2f"))
    return (target + prev + now + label).properties(height=60 + 40 * len(d))


def view_valueup() -> None:
    df, c_ = valueup_frame(), vcol()
    st.markdown(tr("각 그룹이 낸 **기업가치 제고 계획**의 목표를 원문 그대로 인용하고, 2026년 6월 말 공시 값과 비교합니다. "
                   "ROE = TTM 지배주주순이익 / 평균 지배주주지분(전년 6월 말, 올해 6월 말). CET1 비율은 반기보고서 본문의 "
                   "표. 주주환원율 = (현금배당 총액 + 그 해 이사회가 결정한 자기주식 취득·신탁 금액) / 지배주주순이익.",
                   "Each group's targets are quoted verbatim from its **value-up plan** (기업가치 제고 계획) filed on "
                   "DART and compared with what its filings show at the end of June 2026. ROE = trailing twelve-month "
                   "net income to owners / average equity attributable to owners (last June and this June). CET1 is the "
                   "ratio printed in the half-year report. Shareholder return ratio = (cash dividends + buybacks and "
                   "trusts the board decided that year) / net income to owners."))
    counts = df[c_["status"]].value_counts()
    cols = st.columns(4)
    for col, label in zip(cols, ("달성", "순항", "지연", "미달(기한 미명시)")):
        shown = STATUS_EN[label] if LANG == "en" else label
        col.metric(shown.capitalize() if LANG == "en" else shown, tr(f"{counts.get(shown, 0)}개", str(counts.get(shown, 0))))
    left, right = st.columns(2)
    left.altair_chart(progress_chart(df, "roe", tr("ROE: 현재(●)·1년 전(○)·목표(|) (%)",
                                                   "ROE (%): now ●, a year ago ○, target |")), width="stretch")
    right.altair_chart(progress_chart(df, "cet1", tr("CET1 비율: 2026-06-30(●)·목표(|) (%)",
                                                     "CET1 ratio (%): 2026-06-30 ●, target |")), width="stretch")
    st.caption(tr(f"{valueup.NOTICE}. 우리금융지주의 목표는 첨부 PDF에 이미지로만 있어 쪽 이미지에서 판독했습니다(원문 "
                  "대조 아님). 우리금융지주의 2026년 반기보고서에는 그룹 CET1이 없어 그 목표는 측정 불가입니다. 판정은 기한이 "
                  "있으면 남은 격차를 메우는 데 필요한 연간 개선폭과 최근 1년 개선폭을 비교합니다.",
                  "Educational example, not investment advice. Woori Financial's targets sit only in a PDF attached to its "
                  "plan, as page images, so they were read from the images rather than matched against text; its "
                  "half-year report gives no group CET1, so that target is not measured. Where a target has a deadline, "
                  "the verdict compares the yearly improvement still needed with the improvement over the last twelve "
                  "months."))
    st.markdown(tr("**표: 목표별 진척과 판정**", "**Table: progress and verdict for each target**"))
    num = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(df.drop(columns="metric"), hide_index=True,
                 column_config={c_["target"]: num, c_["now"]: num, c_["prev"]: num, c_["gap"]: num})
    md = kr_scorecard()
    st.download_button(tr("점검표 내려받기 (Markdown)", "Download the scorecard (Markdown, Korean)"), md,
                       file_name="valueup_scorecard_ko.md", mime="text/markdown")
    with st.expander(tr("점검표 전문 (목표 원문, 접수번호, 측정 방법)",
                        "Full scorecard, in Korean (quoted targets, DART receipt numbers, method)")):
        show_markdown(md)


def show_markdown(md: str) -> None:
    body = md.split("\n", 1)[1] if md.startswith("# ") else md  # the page header already shows a title
    with st.container(border=True):
        st.markdown(body.replace("$", "\\$").replace("~", "\\~"))


def view_note(task, traj: dict) -> None:
    try:
        note = sector_note_ko(traj, task, require_success=False)
    except NoteError as exc:
        st.error(tr(f"이 실행으로는 노트를 쓸 수 없습니다: {exc}", f"No note for this run: {exc}"))
        return
    if not traj["result"]["success"]:
        st.warning(tr("허용 오차 밖의 답이 있습니다. 노트가 답마다 표시합니다.",
                      "Some answers are outside tolerance; the note marks each one."))
    if LANG == "en":
        st.caption("The note is written in Korean by fixed rules (no model), with the DART receipt number (접수번호) "
                   "after every figure. It refuses to print a figure without a source.")
    st.download_button(tr("노트 내려받기 (Markdown)", "Download the note (Markdown, Korean)"), note,
                       file_name=f"{task.id}_note_ko.md", mime="text/markdown")
    show_markdown(note)


def view_valuation(start_group: str | None) -> None:
    names = {code: kr_dataset().companies[code].name for code in CODES}
    code = st.selectbox("대상 금융지주", CODES, index=CODES.index(start_group) if start_group in CODES else 0,
                        format_func=lambda c: f"{names[c]} ({c})")
    try:
        md, n = snapshot_ko(code)
    except ValuationError as exc:
        st.error(str(exc))
        return
    cols = st.columns(4)
    cols[0].metric("PBR", f"{n['pbr'][code]:.2f}배", help="시가총액 / 보통주자본(지배주주지분 − 신종자본증권)")
    cols[1].metric("동종 PBR 중간값", f"{n['peer_pbr']['median']:.2f}배", help="나머지 세 금융지주 PBR의 중간값")
    cols[2].metric("PER (TTM)", f"{n['per'][code]:.2f}배", help="시가총액 / TTM 지배주주순이익")
    cols[3].metric("단순 ROE", f"{n['roe'][code]:.2f}%", help="TTM 지배주주순이익 / 기말 보통주자본")
    st.caption(f"{NOTICE}: 투자의견과 목표주가가 없습니다. 아래 모든 수치는 DART 접수번호를 인용합니다.")
    st.download_button("스냅샷 내려받기 (Markdown)", md, file_name=f"{code}_valuation_ko.md", mime="text/markdown")
    show_markdown(md)


def view_task(task) -> None:
    if LANG == "en":
        st.markdown(en_names(task.description))
    else:
        st.markdown(task.meta.get("description_ko") or task.description)
        st.markdown(f"**목표(에이전트에게 주는 영문 지시).** {task.primary_objective}")
    inputs = task.meta.get("inputs") or {}
    matched = task.meta.get("matched") or {}
    shared = matched.get("placement") == "shared"
    where = (tr("모든 문장이 공유 문서에 있습니다.", "every sentence is in the shared document.") if shared
             else tr("각 문장은 그 일을 맡은 에이전트의 개인 브리핑에만 있습니다.",
                     "each sentence is only in the private brief of the agent that does that step."))
    st.markdown(tr(f"**기준일** {inputs.get('as_of')}. **라운드 한도** {task.max_rounds}. "
                   f"**방법론 위치** {matched.get('placement')}: {where}",
                   f"**As of** {inputs.get('as_of')}. **Round limit** {task.max_rounds}. "
                   f"**Where the method sits** {matched.get('placement')}: {where}"))
    st.dataframe(pd.DataFrame([{tr("에이전트", "Agent"): a["username"], tr("역할", "Role"): role_name(a.get("role", "")),
                                tr("쓸 수 없는 도구", "Tools it cannot use"): ", ".join(a.get("restricted_tools") or []),
                                tr("개인 브리핑", "Private brief"): (en_names if LANG == "en" else str)(a.get("role_brief", ""))}
                               for a in task.agents]),
                 hide_index=True)
    if task.relevant_game_context:
        st.markdown(tr("**공유 문서**", "**Shared document**"))
        st.markdown(en_names(task.relevant_game_context) if LANG == "en" else task.relevant_game_context)
    with st.expander(tr("정답", "Answer key")):
        st.dataframe(pd.DataFrame([{tr("답", "Answer"): cp["field"], tr("정답", "Expected"): cp["expected"],
                                    tr("상대 허용 오차", "Relative tolerance"): cp["rel_tol"]}
                                   for cp in task.verifier["checkpoints"]]), hide_index=True)


# ---------------------------------------------------------------------- page
def main() -> None:
    global LANG
    qp = st.query_params
    LANG = "en" if qp.get("lang") == "en" else "ko"
    st.set_page_config(page_title=tr("한국 리서치 데스크: 4대 금융지주", "Korean research desk: four financial groups"),
                       layout="wide")
    views = BASE_VIEWS[:4] + ((VALUATION,) if prices_ready(DATA_DIR) else ()) + BASE_VIEWS[4:]
    all_tasks = kr_tasks()
    ids = list(all_tasks)
    start_task = qp.get("task", DEFAULT_TASK)
    asked = qp.get("view", views[0])
    start_view = next((v for v in views if asked in (v, VIEW_EN[v])), views[0])
    start_round = int(qp["round"]) if str(qp.get("round", "")).isdigit() else None

    with st.sidebar:
        lang = st.radio("언어 · Language", ("한국어", "English"), index=1 if LANG == "en" else 0, horizontal=True,
                        key="lang")
        LANG = "en" if lang == "English" else "ko"
        st.title(tr("한국 리서치 데스크", "Korean research desk"))
        st.caption(tr("AI 에이전트 셋(collector, analyst, reporter)이 4대 금융지주의 DART 공시로 질문에 답합니다. "
                      "모든 답은 사실 ID를 인용해야 합니다.",
                      "Three AI agents (collector, analyst, reporter) answer questions about Korea's four largest "
                      "financial holding companies from their DART filings. Every answer must cite a fact."))
        task_id = st.selectbox(tr("과제", "Task"), ids, index=ids.index(start_task) if start_task in ids else 0,
                               format_func=lambda i: f"{title_of(all_tasks[i])} ({i.rsplit('_', 1)[-1]})", key="task")
        source = st.radio(tr("실행", "Run"), ("ref", "upload"), horizontal=True, key="source",
                          format_func=lambda s: {"ref": tr("정답 시나리오", "Scripted reference"),
                                                 "upload": tr("궤적 올리기", "Upload a trajectory")}[s])
        upload = (st.file_uploader(tr("`finance run`이 쓴 궤적 JSON", "Trajectory JSON from `finance run`"), type="json")
                  if source != "ref" else None)
        view = st.radio(tr("화면", "View"), views, index=views.index(start_view),
                        format_func=lambda v: VIEW_EN[v] if LANG == "en" else v, key="view")
        st.caption(tr("모델과 API 키를 쓰지 않고, 네트워크에도 접속하지 않습니다. 데이터: 각 그룹이 DART에 낸 "
                      "정기보고서(OpenAPI 전체 재무제표).",
                      "No model, no API key and no network. Data: each group's periodic reports on DART (full "
                      "financial statements from the OpenAPI)."))
        if VALUATION not in views:
            st.caption(tr("이 데스크는 공시만으로 검증할 수 있도록 DART 수치만 씁니다. DART에는 주가가 없고 KRX Open API "
                          "약관은 데이터를 제3자에게 넘기는 것을 금지해서 주가를 싣지 않았으므로, PER·PBR을 쓰는 "
                          "밸류에이션 화면은 꺼져 있습니다.",
                          "The desk uses DART figures only, so every number can be checked against a filing. DART has "
                          "no share prices and the KRX Open API's terms forbid passing its data on, so no prices are "
                          "shipped and the valuation view (P/E, P/B) is off."))

    task, traj = all_tasks[task_id], None
    if upload is not None:
        traj = json.loads(upload.getvalue().decode("utf-8"))
        if traj.get("task_id") not in all_tasks:
            st.error(tr(f"이 궤적은 {traj.get('task_id')} 과제의 것이며, 한국 데스크 과제가 아닙니다.",
                        f"This trajectory is for {traj.get('task_id')}, which is not a Korean desk task."))
            return
        task = all_tasks[traj["task_id"]]
        try:
            replay(traj, task)
        except NoteError as exc:
            st.error(tr(f"이 궤적은 지금 데이터셋에서 재현되지 않습니다: {exc}",
                        f"The trajectory does not replay on this dataset: {exc}"))
            return
    elif source == "ref":
        traj = kr_reference_run(task_id)

    if view == "밸류업 점검":  # not about the selected task
        st.header(tr("밸류업 약속 점검: 4대 금융지주", "Value-up promises vs filings: Korea's four financial groups"))
        st.caption(tr(f"기준일 {valueup.AS_OF} · 진척은 2026년 6월 말 공시 기준 · 규칙으로 판정(모델 없음)",
                      f"As of {valueup.AS_OF} · progress as filed for the end of June 2026 · verdicts by fixed rules "
                      "(no model)"))
        view_valueup()
        return
    st.header(title_of(task))
    model = (traj.get("meta") or {}).get("model", "uploaded") if traj else None
    run = tr(f"실행: {model}", f"run: {model}") if traj else tr("궤적을 올리면 재생합니다", "upload a run to replay it")
    st.caption(f"`{task.id}` · {tr('기준일', 'as of')} {(task.meta.get('inputs') or {}).get('as_of')} · {run}")
    if view == "4대 금융지주 데이터":
        view_groups()
    elif view == VALUATION:
        view_valuation(qp.get("group"))
    elif view == "과제":
        view_task(task)
    elif traj is None:
        st.info(tr("사이드바에서 궤적 JSON을 올리거나 정답 시나리오로 바꾸세요.",
                   "Upload a trajectory JSON in the sidebar, or switch to the scripted reference."))
    elif view == "섹터 노트":
        view_note(task, traj)
    else:
        view_replay(task, traj, start_round)


main()
