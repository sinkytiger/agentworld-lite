"""The Korean Streamlit demo (demo/streamlit_app_kr.py) renders every view offline. Skipped when Streamlit is not
installed."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "demo" / "streamlit_app_kr.py"


def _open(**query) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=180)
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
    assert views == ["데스크 재생", "4대 금융지주 데이터", "밸류업 점검", "섹터 노트", "과제"]  # DART only: no valuation
    assert "주가를 싣지 않았으므로" in " ".join(c.value for c in at.caption)
    at = _open(view="밸류에이션")                                                      # an old link falls back
    assert next(r for r in at.radio if r.label == "화면").value == "데스크 재생"


def test_english_labels_with_the_same_figures():
    at = _open(view="Value-up check", lang="en")                    # English label in the link works too
    assert next(r for r in at.radio if r.label == "언어 · Language").value == "English"
    assert at.header[0].value == "Value-up promises vs filings: Korea's four financial groups"
    assert {m.label: m.value for m in at.metric} == {"Met": "5", "On track": "1", "Behind": "1",
                                                     "Short (no deadline)": "4"}
    df = at.dataframe[0].value
    kb = df[(df["Group"] == "KB Financial") & (df["Measure"] == "ROE (%)")].iloc[0]
    assert kb["Verdict"] == "met" and round(kb["Now"], 2) == 10.47
    at = _open(view="Group data", lang="en")
    df = at.dataframe[0].value
    assert dict(zip(df["Stock code"], df["Q2 net income to owners (KRW bn)"].round(1)))["105560"] == 1992.2
    assert df.loc[df["Stock code"] == "055550", "Half-year report"].item() == "Half-year report (amended) 20261007000223"
    assert "Shinhan Financial's half-year report was amended on 2026-10-07" in " ".join(c.value for c in at.caption)
    at = _open(view="Task", task="k01_kb_bps_roe_private", lang="en")
    assert at.header[0].value == "BPS and trailing ROE of KB Financial at the end of 2026 H1"
    assert [r for r in at.radio if r.label == "View"][0].options[0] == "Desk replay"


def test_valueup_view_shows_verdicts_and_receipt_numbers():
    at = _open(view="밸류업 점검")
    assert {m.label: m.value for m in at.metric} == {"달성": "5개", "순항": "1개", "지연": "1개", "미달(기한 미명시)": "4개"}
    df = at.dataframe[0].value
    row = lambda g, metric: df[(df["금융지주"] == g) & (df["지표"].str.startswith(metric))].iloc[0]
    assert row("KB금융", "ROE")["판정"] == "달성" and round(row("KB금융", "ROE")["현재"], 2) == 10.47
    assert row("신한지주", "ROE")["판정"] == "순항" and row("신한지주", "ROE")["목표 공시"] == "20260423800235"
    shares = row("신한지주", "발행주식 수")
    assert shares["판정"] == "지연" and shares["목표"] == 450 and round(shares["현재"], 2) == 474.65
    assert row("하나금융지주", "ROE")["기한"] == "명시 없음" and round(row("하나금융지주", "CET1")["현재"], 2) == 13.27
    woori = df[df["금융지주"] == "우리금융지주"].set_index("지표")["판정"].to_dict()    # read from the PDF's page images
    assert woori == {"ROE (%)": "미달(기한 미명시)", "CET1 비율 (%)": "측정 불가", "주주환원율 (%)": "미달(기한 미명시)"}
    full = " ".join(m.value for m in at.markdown)
    assert "“목표 1) ROE 12%” [사실 `086790.target.roe` · 접수번호 20260724800373]" in full
