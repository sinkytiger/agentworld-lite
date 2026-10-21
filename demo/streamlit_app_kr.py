"""Streamlit demo of the Korean research desk (4대 금융지주): replay a run, compare the four groups' DART figures, and
read the cited Korean sector note. The desk uses DART filings only and the repository ships no prices, so the valuation
view appears only after sourced closing prices are added to the dataset (fetch --price ... --price-source ...).

    pip install streamlit
    streamlit run demo/streamlit_app_kr.py

No model, no API key and no network: every view reads the committed dataset (data/finance/dart_fhc) and tasks
(tasks/finance_kr). A run is either a task's scripted reference solution, replayed here, or a trajectory JSON written by
`python -m agentworld_lite.finance run` (upload it in the sidebar). Query parameters open a given state:
?task=<task id>&view=<view>&round=<n>&group=<stock code>
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
from agentworld_lite.task import load_tasks  # noqa: E402

TASK_DIR = ROOT / "tasks" / "finance_kr"
DATA_DIR = ROOT / "data" / "finance" / "dart_fhc"
BASE_VIEWS = ("데스크 재생", "4대 금융지주 데이터", "섹터 노트", "과제")
VALUATION = "밸류에이션"  # listed only when every group has a sourced closing price
DEFAULT_TASK = "k03_big4_q2_sector_shared"
CODES = tuple(FHC.values())
SERIES = {"light": "#2a78d6", "dark": "#3987e5"}  # one series: categorical slot 1 of the reference palette
INK = {"light": "#0b0b0b", "dark": "#f0efec"}     # values and labels wear text ink, not the series color
TOOL_KO = {"fetch_financials": "재무제표 조회", "fetch_shares": "주식 수 조회", "calculate": "계산",
           "share_facts": "사실 공유", "submit_answer": "답 제출", "send_chat": "채팅", "wait": "대기"}
ROLE_KO = {"data collector": "공시 수집", "analyst": "분석", "reporter": "제출"}
PERIOD_KO = {"Q1": "1분기", "H1": "상반기", "Q3": "3분기", "FY": "연간"}


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
    return task.meta.get("name_ko") or task.name


def summary(action: dict) -> str:
    a = action.get("args") or {}
    tool = action["tool"]
    if tool in ("fetch_financials", "fetch_shares"):
        c = kr_dataset().find(str(a.get("company", "")))
        return f"{c.name if c else a.get('company')} {a.get('year')}년 {PERIOD_KO.get(str(a.get('period')), a.get('period'))}"
    if tool == "calculate":
        return f"`{a.get('name')}` = `{a.get('formula')}`"
    if tool == "share_facts":
        ids = a.get("fact_ids") or []
        return f"사실 {len(ids) if isinstance(ids, list) else 1}개 → {a.get('to_agent')}"
    if tool == "submit_answer":
        answers = a.get("answers") or []
        return f"답 {len(answers) if isinstance(answers, list) else 1}개"
    if tool == "send_chat":
        return f"{a.get('to')}에게: {str(a.get('message', ''))[:80]}"
    return str(a.get("reason", ""))


# ---------------------------------------------------------------------- views
def view_replay(task, traj: dict, start_round: int | None) -> None:
    res = traj["result"]
    done = sum(cp["done"] for cp in res["checkpoints"])
    cols = st.columns(4)
    cols[0].metric("사용한 라운드", f"{res['rounds_used']} / {traj['max_rounds']}")
    cols[1].metric("허용 오차 안의 답", f"{done} / {len(res['checkpoints'])}")
    cols[2].metric("도구 호출", res["n_actions"])
    cols[3].metric("실패한 호출", res["n_failed_actions"])

    last = max(1, res["rounds_used"])
    default = min(start_round or last, last)
    r = st.slider("라운드", 1, last, default) if last > 1 else 1
    snap = traj["snapshots"][min(r, len(traj["snapshots"]) - 1)]

    for col, spec in zip(st.columns(len(task.agents)), task.agents):
        name = spec["username"]
        acts = [x for x in traj["actions"] if x["agent"] == name and x["round"] <= r]
        moves = [x for x in acts if x["tool"] != "wait"]
        with col, st.container(border=True):
            st.subheader(name)
            banned = ", ".join(TOOL_KO.get(t, t) for t in spec.get("restricted_tools") or []) or "없음"
            st.caption(f"{ROLE_KO.get(spec.get('role', ''), spec.get('role', ''))} 담당. 쓸 수 없는 도구: {banned}")
            st.metric("노트북의 사실", len(snap["agents"][name]["inventory"]),
                      help="이 에이전트가 가진 사실 ID 수. 계산과 답에는 이것만 쓸 수 있습니다.")
            for x in moves[-4:]:
                mark = ":green[성공]" if x["ok"] else ":red[실패]"
                st.markdown(f"`R{x['round']}` **{TOOL_KO.get(x['tool'], x['tool'])}** {mark}  \n{summary(x)}")
            if moves:
                st.caption(moves[-1]["message"].splitlines()[0][:160])
            st.caption(f"지금까지 대기한 턴: {len(acts) - len(moves)}")

    st.markdown(f"**{r}라운드가 끝났을 때의 답**")
    rows = [{"답": cp["name"], "상태": "허용 오차 이내" if cp["done"] else "아직"} for cp in snap["checkpoints"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def groups_frame(ds: Dataset) -> pd.DataFrame:
    rows = []
    for code in CODES:
        c = ds.companies[code]
        q1, h1 = c.statement(2026, "Q1", "CFS"), c.statement(2026, "H1", "CFS")
        q2 = {a: h1[a] - q1[a] for a in ("net_income_owners", "general_admin_expense", "operating_income",
                                         "credit_loss_provision", "net_interest_income", "net_fee_income")}
        revenue = q2["operating_income"] + q2["general_admin_expense"] + q2["credit_loss_provision"]
        rows.append({
            "금융지주": c.name, "종목코드": code,
            "2분기 지배주주순이익(억원)": q2["net_income_owners"] / 1e8,
            "2분기 CIR(%)": q2["general_admin_expense"] / revenue * 100,
            "보통주자본(조원)": (h1["equity_owners"] - h1["hybrid_capital"]) / 1e12,
            "2분기 순이자이익(억원)": q2["net_interest_income"] / 1e8,
            "2분기 순수수료이익(억원)": q2["net_fee_income"] / 1e8,
            "상반기 지배주주순이익(억원)": h1["net_income_owners"] / 1e8,
            "반기보고서": f"{h1.get('form', '반기보고서')} {h1['rcept_no']}", "접수일": h1["filed"],
            "1분기보고서": f"{q1.get('form', '분기보고서')} {q1['rcept_no']}",
        })
    return pd.DataFrame(rows)


def hbar(df: pd.DataFrame, field: str, title: str, fmt: str) -> alt.Chart:
    """Magnitude: bars from zero, highest first, value at the tip in text ink."""
    order = df.sort_values(field, ascending=False)["금융지주"].tolist()
    base = alt.Chart(df, title=title).encode(
        y=alt.Y("금융지주:N", sort=order, title=None),
        x=alt.X(f"{field}:Q", title=None, scale=alt.Scale(domain=[0, df[field].max() * 1.2]),
                axis=alt.Axis(grid=False, labels=False, ticks=False, domain=False)),
        tooltip=[alt.Tooltip("금융지주:N"), alt.Tooltip(f"{field}:Q", format=fmt)],
    )
    bars = base.mark_bar(size=18, cornerRadiusEnd=4, color=series_color())
    labels = base.mark_text(align="left", dx=6, color=ink()).encode(text=alt.Text(f"{field}:Q", format=fmt))
    return (bars + labels).properties(height=200)


def dots(df: pd.DataFrame, field: str, title: str, fmt: str) -> alt.Chart:
    """Ratios bunched near one level: dots on a zoomed axis, with the average as a dashed rule."""
    order = df.sort_values(field, ascending=True)["금융지주"].tolist()
    lo, hi = df[field].min(), df[field].max()
    x = alt.X(f"{field}:Q", title=None, scale=alt.Scale(domain=[lo - 1.5, hi + 1.5], zero=False),
              axis=alt.Axis(grid=True, gridOpacity=0.25, tickCount=5, domain=False, ticks=False))
    base = alt.Chart(df, title=title).encode(y=alt.Y("금융지주:N", sort=order, title=None), x=x,
                                             tooltip=[alt.Tooltip("금융지주:N"), alt.Tooltip(f"{field}:Q", format=fmt)])
    points = base.mark_circle(size=140, opacity=1, color=series_color())
    labels = base.mark_text(align="left", dx=10, color=ink()).encode(text=alt.Text(f"{field}:Q", format=fmt))
    mean = alt.Chart(pd.DataFrame({"avg": [df[field].mean()]})).mark_rule(strokeDash=[4, 4], color=ink(), opacity=0.5
                                                                           ).encode(x="avg:Q")
    return (mean + points + labels).properties(height=200)


def view_groups() -> None:
    df = groups_frame(kr_dataset())
    st.markdown("2026년 2분기(4~6월) 단독 = 상반기 누적 − 1분기. 보통주자본 = 지배주주지분 − 신종자본증권(2026-06-30). "
                "CIR = 일반관리비 / (영업이익 + 일반관리비 + 신용손실충당금 전입액). 모든 값은 각 그룹이 DART에 낸 "
                "연결 재무제표 원값입니다.".replace("~", "\\~"))
    left, mid, right = st.columns(3)
    left.altair_chart(hbar(df, "2분기 지배주주순이익(억원)", "2분기 지배주주순이익 (억원)", ",.0f"), width="stretch")
    mid.altair_chart(dots(df, "2분기 CIR(%)", "2분기 영업이익경비율 CIR (%)", ".1f"), width="stretch")
    right.altair_chart(hbar(df, "보통주자본(조원)", "보통주자본, 2026-06-30 (조원)", ",.1f"), width="stretch")
    amended = [(c.name, st_["filed"], st_["first_filed"]) for c in (kr_dataset().companies[x] for x in CODES)
               for st_ in [c.statement(2026, "H1", "CFS")] if st_.get("first_rcept_no")]
    st.caption(f"점선: CIR 단순 평균 {df['2분기 CIR(%)'].mean():.2f}%. 보통주자본 평균 "
               f"{df['보통주자본(조원)'].mean():.2f}조원, 최대-최소 차 "
               f"{df['보통주자본(조원)'].max() - df['보통주자본(조원)'].min():.2f}조원. "
               + " ".join(f"{n}의 반기보고서는 {f}에 정정되었습니다(최초 접수 {o})." for n, f, o in amended))
    st.markdown("**표: 각 수치가 나온 보고서와 접수번호**")
    config = {c: st.column_config.NumberColumn(format="%,.0f") for c in df.columns if c.endswith("(억원)")}
    config["2분기 CIR(%)"] = st.column_config.NumberColumn(format="%.2f")
    config["보통주자본(조원)"] = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(df, hide_index=True, column_config=config)


def show_markdown(md: str) -> None:
    body = md.split("\n", 1)[1] if md.startswith("# ") else md  # the page header already shows a title
    with st.container(border=True):
        st.markdown(body.replace("$", "\\$").replace("~", "\\~"))


def view_note(task, traj: dict) -> None:
    try:
        note = sector_note_ko(traj, task, require_success=False)
    except NoteError as exc:
        st.error(f"이 실행으로는 노트를 쓸 수 없습니다: {exc}")
        return
    if not traj["result"]["success"]:
        st.warning("허용 오차 밖의 답이 있습니다. 노트가 답마다 표시합니다.")
    st.download_button("노트 내려받기 (Markdown)", note, file_name=f"{task.id}_note_ko.md", mime="text/markdown")
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
    st.markdown(task.meta.get("description_ko") or task.description)
    st.markdown(f"**목표(에이전트에게 주는 영문 지시).** {task.primary_objective}")
    inputs = task.meta.get("inputs") or {}
    matched = task.meta.get("matched") or {}
    where = ("모든 문장이 공유 문서에 있습니다." if matched.get("placement") == "shared"
             else "각 문장은 그 일을 맡은 에이전트의 개인 브리핑에만 있습니다.")
    st.markdown(f"**기준일** {inputs.get('as_of')}. **라운드 한도** {task.max_rounds}. "
                f"**방법론 위치** {matched.get('placement')}: {where}")
    st.dataframe(pd.DataFrame([{"에이전트": a["username"], "역할": ROLE_KO.get(a.get("role", ""), a.get("role", "")),
                                "쓸 수 없는 도구": ", ".join(a.get("restricted_tools") or []),
                                "개인 브리핑": a.get("role_brief", "")} for a in task.agents]), hide_index=True)
    if task.relevant_game_context:
        st.markdown("**공유 문서**")
        st.markdown(task.relevant_game_context)
    with st.expander("정답"):
        st.dataframe(pd.DataFrame([{"답": cp["field"], "정답": cp["expected"], "상대 허용 오차": cp["rel_tol"]}
                                   for cp in task.verifier["checkpoints"]]), hide_index=True)


# ---------------------------------------------------------------------- page
def main() -> None:
    st.set_page_config(page_title="한국 리서치 데스크: 4대 금융지주", layout="wide")
    qp = st.query_params
    views = BASE_VIEWS[:3] + ((VALUATION,) if prices_ready(DATA_DIR) else ()) + BASE_VIEWS[3:]
    all_tasks = kr_tasks()
    ids = list(all_tasks)
    start_task = qp.get("task", DEFAULT_TASK)
    start_view = qp.get("view", views[0])
    start_round = int(qp["round"]) if str(qp.get("round", "")).isdigit() else None

    with st.sidebar:
        st.title("한국 리서치 데스크")
        st.caption("AI 에이전트 셋(collector, analyst, reporter)이 4대 금융지주의 DART 공시로 질문에 답합니다. "
                   "모든 답은 사실 ID를 인용해야 합니다.")
        task_id = st.selectbox("과제", ids, index=ids.index(start_task) if start_task in ids else 0,
                               format_func=lambda i: f"{title_of(all_tasks[i])} ({i.rsplit('_', 1)[-1]})")
        source = st.radio("실행", ("정답 시나리오", "궤적 올리기"), horizontal=True)
        upload = st.file_uploader("`finance run`이 쓴 궤적 JSON", type="json") if source != "정답 시나리오" else None
        view = st.radio("화면", views, index=views.index(start_view) if start_view in views else 0)
        st.caption("모델과 API 키를 쓰지 않고, 네트워크에도 접속하지 않습니다. 데이터: 각 그룹이 DART에 낸 "
                   "정기보고서(OpenAPI 전체 재무제표).")
        if VALUATION not in views:
            st.caption("이 데스크는 공시만으로 검증할 수 있도록 DART 수치만 씁니다. DART에는 주가가 없고 KRX Open API "
                       "약관은 데이터를 제3자에게 넘기는 것을 금지해서 주가를 싣지 않았으므로, PER·PBR을 쓰는 "
                       "밸류에이션 화면은 꺼져 있습니다.")

    task, traj = all_tasks[task_id], None
    if upload is not None:
        traj = json.loads(upload.getvalue().decode("utf-8"))
        if traj.get("task_id") not in all_tasks:
            st.error(f"이 궤적은 {traj.get('task_id')} 과제의 것이며, 한국 데스크 과제가 아닙니다.")
            return
        task = all_tasks[traj["task_id"]]
        try:
            replay(traj, task)
        except NoteError as exc:
            st.error(f"이 궤적은 지금 데이터셋에서 재현되지 않습니다: {exc}")
            return
    elif source == "정답 시나리오":
        traj = kr_reference_run(task_id)

    st.header(title_of(task))
    run = (f"실행: {(traj.get('meta') or {}).get('model', 'uploaded')}" if traj else "궤적을 올리면 재생합니다")
    st.caption(f"`{task.id}` · 기준일 {(task.meta.get('inputs') or {}).get('as_of')} · {run}")
    if view == "4대 금융지주 데이터":
        view_groups()
    elif view == VALUATION:
        view_valuation(qp.get("group"))
    elif view == "과제":
        view_task(task)
    elif traj is None:
        st.info("사이드바에서 궤적 JSON을 올리거나 정답 시나리오로 바꾸세요.")
    elif view == "섹터 노트":
        view_note(task, traj)
    else:
        view_replay(task, traj, start_round)


main()
