"""The Korean Streamlit demo (demo/streamlit_app_kr.py) renders every view offline. Skipped when Streamlit is not
installed."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

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


def test_note_and_task_views_and_no_valuation_without_prices():
    at = _open(view="섹터 노트")
    note = " ".join(m.value for m in at.markdown)
    assert "calc.common_equity_average" in note and "접수번호 20260814004200" in note
    at = _open(view="과제", task="k01_kb_bps_roe_private")
    assert at.header[0].value == "KB금융 BPS·TTM ROE (2026년 상반기 말)"
    views = next(r for r in at.radio if r.label == "화면").options
    assert views == ["데스크 재생", "4대 금융지주 데이터", "섹터 노트", "과제"]       # DART only: no valuation view
    assert "주가를 싣지 않았으므로" in " ".join(c.value for c in at.caption)
    at = _open(view="밸류에이션")                                                      # an old link falls back
    assert next(r for r in at.radio if r.label == "화면").value == "데스크 재생"
