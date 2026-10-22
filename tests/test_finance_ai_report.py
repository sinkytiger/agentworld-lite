"""AI 리포트와 팩트체크 (agentworld_lite/finance/ai_report.py), offline: a fake client stands in for the model."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentworld_lite.finance import ai_report as ar
from agentworld_lite.llm import UsageMeter

OUT = Path(__file__).resolve().parents[1] / "data" / "finance" / "ai_report"


@pytest.fixture(scope="module")
def refs():
    return ar.references()


@pytest.fixture(scope="module")
def facts():
    return {f.id: f for f, _ in ar.data_pack()}


def test_figures_are_parsed_with_their_written_precision():
    figs = ar.parse_figures("순이익 1조 9,922억원, -1.42%, 4억 7,465만 주, 450백만주, 67bp, 3.9조원, 2026.06.30, "
                            "CET1 13.74% [사실 `105560.cet1.2026H1`], 접수번호 20260814004200, 2분기")
    got = [(f.text, f.value, f.unit, f.precision, f.signed) for f in figs]
    assert got == [("1조 9,922억원", 1.9922e12, "KRW", 1e8, False), ("-1.42%", -1.42, "%", 0.01, True),
                   ("4억 7,465만 주", 474_650_000, "shares", 1e4, False), ("450백만주", 450e6, "shares", 1e6, False),
                   ("67bp", 0.67, "pp", 0.01, False), ("3.9조원", 3.9e12, "KRW", 1e11, False),
                   ("13.74%", 13.74, "%", 0.01, False)]
    assert ar.parse_figures("| 18,201 | 17.5 |") == []                        # bare numbers need a unit from a header
    assert [f.value for f in ar.parse_figures("18,201", "억원")] == [1.8201e12]


def test_rounding_and_truncation_are_accepted_nothing_else():
    fig = ar.parse_figures("1.66%")[0]
    assert ar._close(fig, 1.665152) and ar._close(fig, 1.6649) and not ar._close(fig, 1.6712)
    assert ar._close(ar.parse_figures("1조 9,922억원")[0], 1_992_189e6)
    assert ar._close(ar.parse_figures("1조 9,921억원")[0], 1_992_189e6)          # 1조 9,921.89억 truncated
    assert not ar._close(ar.parse_figures("1조 9,920억원")[0], 1_992_189e6)


SAMPLE = """# 4대 금융지주 2026년 2분기

## KB금융
- 2026년 2분기 지배주주순이익은 1조 9,922억원으로 전분기 대비 5.3%, 전년 동기 대비 14.6% 증가했습니다.
- 2025년 2분기 지배주주순이익은 1조 9,922억원입니다.
- 상반기 CIR은 37.5%입니다. ROE 목표 10%를 넘어 PBR은 0.6배입니다.

| 그룹 | 2Q26 지배주주순이익(억원) | YoY(%) |
|---|---|---|
| 하나금융지주 | 11,928 | -1.4 |
| 4대 합산 | 60,097 | 11.37 |

신한지주 ROE는 9.17%로 목표(10%)까지 0.83%p 남았습니다. 발행주식 수 목표는 4억 5,000만 주이고 확인 필요 항목이 있습니다.
"""


def test_context_check_verdicts(refs):
    recs = ar.check_answer(SAMPLE, refs)
    got = [(r["line"], r["figure"], r["verdict"], (r["ref"] or {}).get("label")) for r in recs]
    assert got == [
        (4, "1조 9,922억원", "일치", "KB금융 2026년 2분기 지배주주순이익"),
        (4, "5.3%", "일치", "KB금융 2026년 2분기 지배주주순이익 전분기 대비 증감률"),
        (4, "14.6%", "일치", "KB금융 2026년 2분기 지배주주순이익 전년 대비 증감률"),
        (5, "1조 9,922억원", "기준 다름", "KB금융 2026년 2분기 지배주주순이익"),      # the year is wrong
        (6, "37.5%", "불일치", "KB금융 2026년 상반기 CIR"),
        (6, "10%", "일치", "KB금융 기업가치 제고 계획 ROE 목표"),
        (6, "0.6배", "확인 불가", None),                                                # no prices in the desk
        (10, "11,928", "일치", "하나금융지주 2026년 2분기 지배주주순이익"),
        (10, "-1.4", "기준 다름", "하나금융지주 2026년 2분기 지배주주순이익 전분기 대비 증감률"),  # QoQ shown as YoY
        (11, "60,097", "일치", "4개 그룹 2026년 2분기 지배주주순이익 합계"),
        (11, "11.37", "일치", "4개 그룹 2026년 2분기 합산 지배주주순이익 전년 대비 증감률"),
        (13, "9.17%", "일치", "신한지주 TTM ROE(2026년 6월 말, 평균 지배주주지분)"),
        (13, "10%", "일치", "신한지주 기업가치 제고 계획 ROE 목표"),
        (13, "0.83%p", "일치", "신한지주 ROE 목표와 현재의 차"),
        (13, "4억 5,000만 주", "일치", "신한지주 기업가치 제고 계획 발행주식 수 목표"),
    ]
    assert recs[4]["ref"]["shown"] == "36.16%" and recs[4]["ref"]["sources"] == ["20260814004200"]
    s = ar.summarize(recs, SAMPLE)
    assert (s["figures"], s["일치"], s["기준 다름"], s["불일치"], s["확인 불가"], s["확인 필요 표시"]) == (15, 11, 2, 1, 1, 1)


def test_stated_targets_are_checked_against_the_quoted_targets_only(refs):
    text = """| 그룹 | ROE 목표 | 주주환원 목표 |
|---|---|---|
| 하나금융 | 10%(2027년) | 50% |
| 우리금융 | 10% | 50% |

4사의 목표는 대체로 ROE 10%, CET1 13% 내외입니다. KB금융은 ROE 목표 10%를 넘어 상반기 기준 TTM ROE 10.47%입니다.
- CET1 13% 이상을 유지할 수 있는지"""
    got = [(r["figure"], r["verdict"], (r["ref"] or {}).get("label")) for r in ar.check_answer(text, refs)]
    assert got == [("10%", "불일치", "하나금융지주 기업가치 제고 계획 ROE 목표"),        # the current plan says 12%
                   ("50%", "일치", "하나금융지주 기업가치 제고 계획 주주환원율 목표"),
                   ("10%", "확인 불가", None), ("50%", "확인 불가", None),               # Woori: no quotable target
                   ("10%", "일치", "KB금융 기업가치 제고 계획 ROE 목표"),
                   ("13%", "일치", "KB금융 기업가치 제고 계획 CET1 비율 목표"),
                   ("10%", "일치", "KB금융 기업가치 제고 계획 ROE 목표"),
                   ("10.47%", "일치", "KB금융 TTM ROE(2026년 6월 말, 평균 지배주주지분)"),   # the current level
                   ("13%", "확인 불가", None)]                                         # no group named


def test_context_from_lead_in_lines_parent_bullets_and_left_cells(refs):
    text = """## 4. 밸류업
**최근 12개월 ROE**(최근 12개월 = 2025년 연간 + 2026년 상반기 − 2025년 상반기)

- **KB금융**: 10.5%
  - 1년 전: 9.9%
- 우리는 CIR이 40% 중반대로 가장 높습니다. 참고로 2025년 말 CET1 비율은 12.89%입니다.

| 그룹 | 지표 | 목표 | 현재 |
|---|---|---|---|
| 신한지주 | ROE | 10% (2027년까지) | 9.2% |

**신한지주 발행주식 수**
- 목표까지 남은 주식 수: 24,654,361주
- 2026년 계획의 "발행주식수 50백만주 감축"이 어느 시점 기준인지 확인 필요"""
    got = [(r["figure"], r["verdict"], (r["ref"] or {}).get("label")) for r in ar.check_answer(text, refs)]
    assert got == [("10.5%", "일치", "KB금융 TTM ROE(2026년 6월 말, 평균 지배주주지분)"),
                   ("9.9%", "일치", "KB금융 TTM ROE(2025년 6월 말, 평균 지배주주지분)"),       # group from the parent
                   ("12.89%", "일치", "우리금융지주 보통주자본비율(2025년 말)"),                  # "40% 중반대" skipped
                   ("10%", "일치", "신한지주 기업가치 제고 계획 ROE 목표"),
                   ("9.2%", "일치", "신한지주 TTM ROE(2026년 6월 말, 평균 지배주주지분)"),       # ROE from a left cell
                   ("24,654,361주", "일치", "신한지주 발행주식 수 목표까지 남은 주식 수"),          # a gap, not a target
                   ("50백만주", "일치", "신한지주 기업가치 제고 계획 발행주식수 감축 목표(50백만주)")]  # quoted wording


def test_citations_are_recomputed_from_the_data_pack(refs, facts):
    text = ("- KB금융 2분기 순이익 1조 9,922억원 [계산: `105560.2026H1.CFS.net_income_owners` - "
            "`105560.2026Q1.CFS.net_income_owners`], CET1 13.74% [사실 `105560.cet1.2026H1`]\n"
            "- KB금융 상반기 CIR 36.16% [계산: `105560.2026H1.CFS.general_admin_expense` / (`105560.2026H1.CFS.operating_income`"
            " + `105560.2026H1.CFS.general_admin_expense` + `105560.2026H1.CFS.credit_loss_provision`)]\n"
            "- KB금융 2분기 순이익 2조원 [계산: `105560.2026H1.CFS.net_income_owners`], 1조원 [사실 `nope`], "
            "3% [계산: __import__('os')]")
    recs = ar.check_answer(text, refs, facts)
    assert [(r["figure"], r["citation_verdict"]) for r in recs] == [
        ("1조 9,922억원", "인용 일치"), ("13.74%", "인용 일치"), ("36.16%", "인용 일치"),   # a fraction shown x 100
        ("2조원", "인용 불일치"), ("1조원", "인용 오류"), ("3%", "인용 오류")]
    assert recs[3]["citation_value"] == "3조 8,846억원" and recs[3]["verdict"] == "일치"   # right figure, wrong source
    assert "nope" in recs[4]["citation_value"]


def test_data_pack_and_prompts():
    rows = ar.data_pack()
    ids = [f.id for f, _ in rows]
    assert len(ids) == len(set(ids)) and all(f.meta.get("rcept_no") for f, _ in rows)
    assert {"105560.cet1.2026H1", "316140.cet1.2025FY", "055550.target.shares", "086790.2024FY.CFS.equity_owners",
            "105560.dividends.FY2025"} <= set(ids)
    notes = dict((f.id, n) for f, n in rows)
    assert notes["086790.target.roe"] == "원문: “목표 1) ROE 12%”, 기한: 명시 없음"
    p = ar.prompts()
    assert p["nodata"].startswith(ar.QUESTION) and p["data"].startswith(ar.QUESTION)
    assert "`105560.2026H1.CFS.net_income_owners`" in p["data"] and "사실 id" not in p["nodata"]
    assert "| `105560.2026H1.CFS.net_income_owners` | KB금융 2026 반기보고서(누적) 연결 지배주주순이익" in p["data"]


class FakeSDK:
    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=self)
        self.messages = self

    def create(self, **params):
        self.calls.append(params)
        text = "## 리포트\n- KB금융 2분기 지배주주순이익 1조 9,922억원\n"
        return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="..."),
                                        SimpleNamespace(type="text", text=text)],
                               stop_reason="end_turn", model=params["model"],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))


class FakeClient:
    def __init__(self):
        self.model, self.sdk, self.usage = "claude-opus-5-5", FakeSDK(), UsageMeter()

    def create(self, **params):
        resp = self.sdk.create(model=self.model, **params)
        return resp, self.usage.add(resp.usage, self.model)


def test_run_writes_everything_and_respects_the_cost_cap(tmp_path):
    client = FakeClient()
    with pytest.raises(ar.AIReportError, match="exceeds --max-cost"):
        ar.run(client, tmp_path, max_cost=0.30, log=lambda s: None)
    assert client.sdk.calls == []                                             # refused before any call
    record = ar.run(client, tmp_path, max_cost=1.0, log=lambda s: None)
    assert [c["messages"][0]["content"][:30] for c in client.sdk.calls] == [ar.QUESTION[:30]] * 2
    assert all(c["system"] == ar.SYSTEM and c["max_tokens"] == ar.MAX_TOKENS for c in client.sdk.calls)
    assert record["total_cost_usd"] == pytest.approx(2 * (1000 * 4 + 500 * 20) / 1e6)
    assert (tmp_path / "answer_data.md").read_text(encoding="utf-8").startswith("## 리포트")
    assert ar.run(client, tmp_path, max_cost=0.01, log=lambda s: None)["calls"].keys() == {"nodata", "data"}
    assert len(client.sdk.calls) == 2                                         # saved answers are not asked again
    assert record["calls"]["data"]["content_blocks"] == {"thinking": 1, "text": 1}
    record = ar.run(client, tmp_path, max_cost=1.0, variants=["data"], force=True, max_tokens=12000, log=lambda s: None)
    assert client.sdk.calls[-1]["max_tokens"] == 12000 and len(record["replaced"]) == 1
    assert record["total_cost_usd"] == pytest.approx(3 * (1000 * 4 + 500 * 20) / 1e6)  # the replaced call still counts
    result = ar.check(tmp_path)
    assert result["data"]["summary"]["일치"] == 1
    md = (tmp_path / "factcheck.md").read_text(encoding="utf-8")
    assert md.startswith("# AI 리포트 팩트체크") and "합계 0.0420달러(다시 물어 교체한 답 1개 포함)" in md
    assert b"\r\n" not in (tmp_path / "factcheck.json").read_bytes()


@pytest.mark.skipif(not (OUT / "factcheck.json").exists(), reason="no saved model answers")
def test_saved_answers_still_check_the_same(tmp_path):
    saved = json.loads((OUT / "factcheck.json").read_text(encoding="utf-8"))
    refs, facts = ar.references(), {f.id: f for f, _ in ar.data_pack()}
    for variant, res in saved.items():
        text = (OUT / f"answer_{variant}.md").read_text(encoding="utf-8")
        assert ar.summarize(ar.check_answer(text, refs, facts), text) == res["summary"]
