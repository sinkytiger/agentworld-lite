"""The Korean Streamlit demo (demo/streamlit_app_kr.py) renders every view offline. Skipped when Streamlit is not
installed."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agentworld_lite.finance.catalog import kb_close  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "demo" / "streamlit_app_kr.py"


def _open(**query) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=60)
    for k, v in query.items():
        at.query_params[k] = v
    at.run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    return at


def test_desk_replay_opens_at_the_requested_round():
    at = _open(view="데스크 재생", round="9")
    assert at.header[0].value == "4대 금융지주 2026년 2분기 지배주주순이익과 보통주자본"
    assert [m.value for m in at.metric][:2] == ["15 / 30", "6 / 6"]
    assert at.slider[0].value == 9
    at.slider[0].set_value(15).run()
    assert not at.exception


def test_group_view_shows_the_k03_figures_and_receipt_numbers():
    at = _open(view="4대 금융지주 데이터")
    text = " ".join(c.value for c in at.caption)
    assert "보통주자본 평균 47.62조원" in text and "21.31조원" in text and "2026-10-07에 정정" in text
    df = at.dataframe[0].value
    assert dict(zip(df["종목코드"], df["2분기 지배주주순이익(억원)"].round())) == {
        "105560": 19922, "055550": 18201, "086790": 11928, "316140": 10046}
    assert dict(zip(df["종목코드"], df["2분기 CIR(%)"].round(2)))["055550"] == 36.29
    assert df.loc[df["종목코드"] == "055550", "반기보고서"].item() == "[기재정정]반기보고서 20261007000223"


def test_note_task_and_valuation_views():
    at = _open(view="섹터 노트")
    note = " ".join(m.value for m in at.markdown)
    assert "calc.common_equity_average" in note and "접수번호 20260814004200" in note
    at = _open(view="과제", task="k02_q2_cost_income_private")
    assert at.header[0].value == "신한지주·우리금융지주 2026년 2분기 영업이익경비율(CIR)"
    at = _open(view="밸류에이션", group="055550")
    assert next(s for s in at.selectbox if s.label == "대상 금융지주").value == "055550"
    if kb_close():
        assert "교육용 예시·투자 권유 아님" in " ".join(c.value for c in at.caption)
    else:  # no sourced closing prices in the dataset yet: the view says what is missing instead of failing
        assert "KRX 종가가 필요" in at.info[0].value
