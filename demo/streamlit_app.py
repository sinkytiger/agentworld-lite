"""Streamlit demo of the Canadian research desk: replay a run, compare the Big 5 banks, read the cited sector note,
a valuation snapshot and a news-based sector risk digest.

    pip install streamlit
    streamlit run demo/streamlit_app.py

No model and no API key. Everything is offline except the News digest view, which fetches public RSS headlines and
Bank of Canada data only when its button is pressed. A run is either a task's scripted reference solution, replayed
here, or a trajectory JSON written by `python -m agentworld_lite.finance run` (upload it in the sidebar).
Query parameters open a given state, which demo/record_gif.py uses: ?task=<task id>&view=<view>&round=<n>
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agentworld_lite.finance.catalog import BIG5  # noqa: E402
from agentworld_lite.finance.dataset import Dataset  # noqa: E402
from agentworld_lite.finance.news import FEEDS, NewsError, build_digest  # noqa: E402
from agentworld_lite.finance.note import NoteError, replay, sector_note  # noqa: E402
from agentworld_lite.finance.run import make_factory, run_episode  # noqa: E402
from agentworld_lite.finance.valuation import ValuationError, snapshot  # noqa: E402
from agentworld_lite.task import load_tasks  # noqa: E402

TASK_DIR = ROOT / "tasks" / "finance_ca"
DATA_DIR = ROOT / "data" / "finance" / "edgar"
VIEWS = ("Desk replay", "Big 5 data", "Sector note", "Valuation", "News digest", "Task")
DEFAULT_TASK = "c03_big5_q3_sector_shared"
SERIES = {"light": "#2a78d6", "dark": "#3987e5"}  # one series: categorical slot 1 of the reference palette
INK = {"light": "#0b0b0b", "dark": "#f0efec"}     # values and labels wear text ink, not the series color


# ---------------------------------------------------------------------- data
@st.cache_resource
def tasks() -> dict:
    return {t.id: t for t in load_tasks(str(TASK_DIR))}


@st.cache_resource
def dataset() -> Dataset:
    return Dataset(DATA_DIR)


@st.cache_data
def reference_run(task_id: str) -> dict:
    return run_episode(tasks()[task_id], make_factory("scripted"),
                       meta={"agent": "scripted", "model": "scripted reference"})


def _mode() -> str:
    theme = getattr(getattr(st, "context", None), "theme", None)
    return "dark" if getattr(theme, "type", None) == "dark" else "light"


def series_color() -> str:
    return SERIES[_mode()]


def ink() -> str:
    return INK[_mode()]


def summary(action: dict) -> str:
    a = action.get("args") or {}
    tool = action["tool"]
    if tool == "fetch_financials":
        return f"{a.get('company')} fiscal {a.get('year')} {a.get('period')}"
    if tool == "calculate":
        return f"`{a.get('name')}` = `{a.get('formula')}`"
    if tool == "share_facts":
        ids = a.get("fact_ids") or []
        return f"{len(ids) if isinstance(ids, list) else 1} fact(s) to {a.get('to_agent')}"
    if tool == "submit_answer":
        answers = a.get("answers") or []
        return f"{len(answers) if isinstance(answers, list) else 1} answer(s)"
    if tool == "send_chat":
        return f"to {a.get('to')}: {str(a.get('message', ''))[:80]}"
    return str(a.get("reason", ""))


# ---------------------------------------------------------------------- views
def view_replay(task, traj: dict, start_round: int | None) -> None:
    res = traj["result"]
    done = sum(cp["done"] for cp in res["checkpoints"])
    cols = st.columns(4)
    cols[0].metric("Rounds used", f"{res['rounds_used']} / {traj['max_rounds']}")
    cols[1].metric("Answers within tolerance", f"{done} / {len(res['checkpoints'])}")
    cols[2].metric("Tool calls", res["n_actions"])
    cols[3].metric("Failed calls", res["n_failed_actions"])

    last = max(1, res["rounds_used"])
    default = min(start_round or last, last)
    r = st.slider("Round", 1, last, default) if last > 1 else 1
    snap = traj["snapshots"][min(r, len(traj["snapshots"]) - 1)]

    for col, spec in zip(st.columns(len(task.agents)), task.agents):
        name = spec["username"]
        acts = [x for x in traj["actions"] if x["agent"] == name and x["round"] <= r]
        moves = [x for x in acts if x["tool"] != "wait"]
        with col, st.container(border=True):
            st.subheader(name)
            st.caption(f"{spec.get('role', '')}. Cannot use: {', '.join(spec.get('restricted_tools') or []) or 'none'}")
            st.metric("Facts in notebook", len(snap["agents"][name]["inventory"]),
                      help="Fact ids this agent holds; only these can be used in its calculations or answers.")
            for x in moves[-4:]:
                mark = ":green[ok]" if x["ok"] else ":red[failed]"
                st.markdown(f"`R{x['round']}` **{x['tool']}** {mark}  \n{summary(x)}")
            if moves:
                st.caption(moves[-1]["message"].splitlines()[0][:160])
            st.caption(f"{len(acts) - len(moves)} turn(s) spent waiting so far.")

    st.markdown(f"**Answers at the end of round {r}**")
    rows = [{"Answer": cp["name"], "Status": "within tolerance" if cp["done"] else "not yet"}
            for cp in snap["checkpoints"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def big5_frame(ds: Dataset) -> pd.DataFrame:
    rows = []
    for code in BIG5:
        c = ds.companies[code]
        q3, h1 = c.statement(2026, "Q3", "CFS"), c.statement(2026, "H1", "CFS")
        rows.append({
            "Bank": code, "Name": c.name,
            "Q3 net income to common (C$ m)": (q3["net_income_common"] - h1["net_income_common"]) / 1e6,
            "CET1 ratio (%)": q3["cet1_ratio"],
            "9M net income to common (C$ m)": q3["net_income_common"] / 1e6,
            "H1 net income to common (C$ m)": h1["net_income_common"] / 1e6,
            "Q3 filing": f"{q3['form']} {q3['doc_id']}", "Filed": q3["filed"],
            "H1 filing": f"{h1['form']} {h1['doc_id']}",
        })
    return pd.DataFrame(rows)


def hbar(df: pd.DataFrame, field: str, title: str, fmt: str) -> alt.Chart:
    """Magnitude: bars from zero, highest first, value at the tip in text ink."""
    order = df.sort_values(field, ascending=False)["Bank"].tolist()
    base = alt.Chart(df, title=title).encode(
        y=alt.Y("Bank:N", sort=order, title=None),
        x=alt.X(f"{field}:Q", title=None, scale=alt.Scale(domain=[0, df[field].max() * 1.18]),
                axis=alt.Axis(grid=False, labels=False, ticks=False, domain=False)),
        tooltip=[alt.Tooltip("Name:N"), alt.Tooltip(f"{field}:Q", format=fmt)],
    )
    bars = base.mark_bar(size=18, cornerRadiusEnd=4, color=series_color())
    labels = base.mark_text(align="left", dx=6, color=ink()).encode(text=alt.Text(f"{field}:Q", format=fmt))
    return (bars + labels).properties(height=230)


def dots(df: pd.DataFrame, field: str, title: str, fmt: str) -> alt.Chart:
    """Ratios bunched near one level: dots on a zoomed axis, with the average as a dashed rule."""
    order = df.sort_values(field, ascending=False)["Bank"].tolist()
    lo, hi = df[field].min(), df[field].max()
    x = alt.X(f"{field}:Q", title=None, scale=alt.Scale(domain=[lo - 0.4, hi + 0.4], zero=False),
              axis=alt.Axis(grid=True, gridOpacity=0.25, tickCount=5, domain=False, ticks=False))
    base = alt.Chart(df, title=title).encode(y=alt.Y("Bank:N", sort=order, title=None), x=x,
                                             tooltip=[alt.Tooltip("Name:N"), alt.Tooltip(f"{field}:Q", format=fmt)])
    points = base.mark_circle(size=140, opacity=1, color=series_color())
    labels = base.mark_text(align="left", dx=10, color=ink()).encode(text=alt.Text(f"{field}:Q", format=fmt))
    mean = alt.Chart(pd.DataFrame({"avg": [df[field].mean()]})).mark_rule(strokeDash=[4, 4], color=ink(), opacity=0.5
                                                                           ).encode(x="avg:Q")
    return (mean + points + labels).properties(height=230)


def view_big5() -> None:
    df = big5_frame(dataset())
    st.markdown("Fiscal Q3 2026 (May to July 2026). A single quarter = Q3 year-to-date minus H1 year-to-date; "
                "CET1 is the ratio at July 31, 2026. Every value is read from the bank's own 6-K XBRL on SEC EDGAR.")
    left, right = st.columns(2)
    left.altair_chart(hbar(df, "Q3 net income to common (C$ m)", "Net income to common, Q3 alone (C$ millions)",
                           ",.0f"), width="stretch")
    right.altair_chart(dots(df, "CET1 ratio (%)", "CET1 ratio at July 31, 2026 (%)", ".1f"), width="stretch")
    st.caption(f"Dashed line: CET1 average {df['CET1 ratio (%)'].mean():.2f}%. Spread "
               f"{df['CET1 ratio (%)'].max() - df['CET1 ratio (%)'].min():.2f} pp (highest minus lowest). "
               "BMO's quarter includes a C$962 million after-tax goodwill charge on an announced divestiture.")
    st.markdown("**Table view, with the filings behind each figure**")
    config = {c: st.column_config.NumberColumn(format="%,.0f") for c in df.columns if c.endswith("(C$ m)")}
    config["CET1 ratio (%)"] = st.column_config.NumberColumn(format="%.1f")
    st.dataframe(df, hide_index=True, column_config=config)


def view_note(task, traj: dict) -> None:
    try:
        note = sector_note(traj, task, require_success=False)
    except NoteError as exc:
        st.error(f"No note for this run: {exc}")
        return
    if not traj["result"]["success"]:
        st.warning("Some answers are outside tolerance; the note marks each one.")
    st.download_button("Download the note (Markdown)", note, file_name=f"{task.id}_note.md", mime="text/markdown")
    show_markdown(note)


def show_markdown(md: str) -> None:
    body = md.split("\n", 1)[1] if md.startswith("# ") else md  # the page header already shows a title
    with st.container(border=True):
        st.markdown(body.replace("$", "\\$"))  # C$ amounts, not LaTeX


def view_valuation(start_bank: str | None) -> None:
    names = {code: dataset().companies[code].name for code in BIG5}
    bank = st.selectbox("Subject bank", BIG5, index=BIG5.index(start_bank) if start_bank in BIG5 else 0,
                        format_func=lambda c: f"{names[c]} ({c})")
    try:
        md, n = snapshot(bank)
    except ValuationError as exc:
        st.error(str(exc))
        return
    cols = st.columns(4)
    cols[0].metric("P/E (TTM diluted)", f"{n['pe'][bank]:.2f}x")
    cols[1].metric("Peer median P/E", f"{n['peer_pe']['median']:.2f}x")
    cols[2].metric("Implied by peer P/Es (C$)", f"{n['implied']['low']:.0f} to {n['implied']['high']:.0f}")
    cols[3].metric("Close on 2026-07-31 (C$)", f"{n['price']:.2f}")
    st.caption("Educational sample, not investment advice: no rating and no target price. Every figure below cites "
               "its filing.")
    st.download_button("Download the snapshot (Markdown)", md, file_name=f"{bank.lower()}_valuation.md",
                       mime="text/markdown")
    show_markdown(md)


@st.cache_data(ttl=3600, show_spinner="Fetching headlines and Bank of Canada data")
def live_digest(as_of: str) -> str:
    md, _ = build_digest(FEEDS, cache_dir=Path(tempfile.gettempdir()) / "awl_news", as_of=as_of)
    return md


def view_news() -> None:
    st.markdown("A sector risk digest built by fixed rules from public RSS headlines (Financial Post, CBC Business) "
                "and the Bank of Canada Valet API, set beside each bank's filed figures. Only the headline, link, "
                "date and the feed's short summary are read; article pages are never opened. Results are cached for "
                "an hour.")
    if not st.button("Fetch the latest headlines and build the digest"):
        return
    try:
        md = live_digest(dt.date.today().isoformat())
    except NewsError as exc:
        st.error(f"No digest: {exc}")
        return
    st.download_button("Download the digest (Markdown)", md, file_name="sector_risk_digest.md", mime="text/markdown")
    show_markdown(md)


def view_task(task) -> None:
    st.markdown(task.description)
    st.markdown(f"**Objective.** {task.primary_objective}")
    inputs = task.meta.get("inputs") or {}
    matched = task.meta.get("matched") or {}
    st.markdown(f"**As-of date** {inputs.get('as_of')}. **Round budget** {task.max_rounds}. "
                f"**Methodology placement** {matched.get('placement')}: "
                + ("every sentence is in the shared document." if matched.get("placement") == "shared"
                   else "each sentence sits only in the private brief of the agent who owns it."))
    st.dataframe(pd.DataFrame([{"Agent": a["username"], "Role": a.get("role", ""),
                                "Cannot use": ", ".join(a.get("restricted_tools") or []),
                                "Private brief": a.get("role_brief", "")} for a in task.agents]), hide_index=True)
    if task.relevant_game_context:
        st.markdown("**Shared document**")
        st.markdown(task.relevant_game_context)
    with st.expander("Answer key"):
        st.dataframe(pd.DataFrame([{"Answer": cp["field"], "Expected": cp["expected"], "Relative tolerance": cp["rel_tol"]}
                                   for cp in task.verifier["checkpoints"]]), hide_index=True)


# ---------------------------------------------------------------------- page
def main() -> None:
    st.set_page_config(page_title="Canadian research desk", layout="wide")
    qp = st.query_params
    all_tasks = tasks()
    ids = list(all_tasks)
    start_task = qp.get("task", DEFAULT_TASK)
    start_view = qp.get("view", VIEWS[0])
    start_round = int(qp["round"]) if str(qp.get("round", "")).isdigit() else None

    with st.sidebar:
        st.title("Canadian research desk")
        st.caption("Three AI agents (collector, analyst, reporter) answer questions about Canada's Big 5 banks "
                   "from their SEC EDGAR filings. Every answer must cite a fact.")
        task_id = st.selectbox("Task", ids, index=ids.index(start_task) if start_task in ids else 0,
                               format_func=lambda i: f"{all_tasks[i].name} ({i.rsplit('_', 1)[-1]})")
        source = st.radio("Run", ("Scripted reference", "Upload a trajectory"), horizontal=True)
        upload = st.file_uploader("Trajectory JSON from `finance run`", type="json") if source != "Scripted reference" else None
        view = st.radio("View", VIEWS, index=VIEWS.index(start_view) if start_view in VIEWS else 0)
        st.caption("Offline: no model and no network. Data: XBRL of each bank's 40-F and 6-K on SEC EDGAR.")

    task, traj = all_tasks[task_id], None
    if upload is not None:
        traj = json.loads(upload.getvalue().decode("utf-8"))
        if traj.get("task_id") not in all_tasks:
            st.error(f"This trajectory is for {traj.get('task_id')}, which is not a Canadian task.")
            return
        task = all_tasks[traj["task_id"]]
        try:
            replay(traj, task)
        except NoteError as exc:
            st.error(f"The trajectory does not replay on this dataset: {exc}")
            return
    elif source == "Scripted reference":
        traj = reference_run(task_id)

    st.header(task.name)
    st.caption(f"`{task.id}` · as of {(task.meta.get('inputs') or {}).get('as_of')} · "
               + (f"run: {(traj.get('meta') or {}).get('model', 'uploaded')}" if traj else "upload a run to replay it"))
    if view == "Big 5 data":
        view_big5()
    elif view == "Valuation":
        view_valuation(qp.get("bank"))
    elif view == "News digest":
        view_news()
    elif view == "Task":
        view_task(task)
    elif traj is None:
        st.info("Upload a trajectory JSON in the sidebar, or switch to the scripted reference.")
    elif view == "Sector note":
        view_note(task, traj)
    else:
        view_replay(task, traj, start_round)


main()
