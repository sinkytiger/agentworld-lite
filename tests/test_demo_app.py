"""The Streamlit demo (demo/streamlit_app.py) renders every view offline. Skipped when Streamlit is not installed."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "demo" / "streamlit_app.py"


def _open(**query) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=60)
    for k, v in query.items():
        at.query_params[k] = v
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    return at


def test_desk_replay_opens_at_the_requested_round():
    at = _open(view="Desk replay", round="11")
    assert at.header[0].value == "Big 5 Canadian banks: fiscal Q3 2026 earnings and capital"
    assert [m.value for m in at.metric][:2] == ["18 / 30", "7 / 7"]
    assert at.slider[0].value == 11
    at.slider[0].set_value(18).run()
    assert not at.exception


def test_big5_view_shows_the_c03_figures():
    at = _open(view="Big 5 data")
    text = " ".join(c.value for c in at.caption)
    assert "CET1 average 13.46%" in text and "1.30 pp" in text
    df = at.dataframe[0].value
    assert dict(zip(df["Bank"], df["Q3 net income to common (C$ m)"])) == {
        "RY": 5879, "TD": 4521, "BNS": 2778, "BMO": 1667, "CM": 2271}


def test_valuation_view_and_news_view_without_network():
    at = _open(view="Valuation", bank="TD")
    assert next(s for s in at.selectbox if s.label == "Subject bank").value == "TD"
    assert [m.value for m in at.metric][0] == "17.99x"
    assert "C\\$168.04 [fact `TD.price.2026-07-31`" in " ".join(m.value for m in at.markdown)
    at = _open(view="News digest")
    assert at.button[0].label.startswith("Fetch the latest headlines")   # nothing is fetched until it is pressed
    assert "article pages are never opened" in " ".join(m.value for m in at.markdown)


def test_sector_note_view_and_other_tasks():
    at = _open(view="Sector note")
    note = " ".join(m.value for m in at.markdown)
    assert "calc.cet1_average" in note and "0001562762-26-000098" in note
    assert "C\\$5,879 million" in note  # escaped so Streamlit does not read C$ ... $ as LaTeX
    at = _open(view="Task", task="c01_ry_ttm_pe_private")
    assert at.header[0].value == "Trailing P/E of Royal Bank of Canada"
