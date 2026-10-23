"""Korea and Canada side by side: one ROE formula, each market's cost-to-income, reported CET1, every figure cited.
Offline: reads the committed DART and EDGAR data sets."""

import json
import re
import statistics
from pathlib import Path

import pytest

from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.catalog import _cir
from agentworld_lite.finance.compare import comparison, render
from agentworld_lite.finance.note import uncited_figures

ROOT = Path(__file__).resolve().parents[1]


def _st(market_dir, code, key):
    return json.loads((ROOT / "data" / "finance" / market_dir / f"{code}.json").read_text(encoding="utf-8"))[
        "statements"][key]


def _rows():
    return {r["code"]: r for r in comparison()["rows"]}


def test_one_roe_formula_in_both_markets():
    rows = _rows()
    fy, h25, h26 = (_st("dart_fhc", "105560", k) for k in ("2025:FY:CFS", "2025:H1:CFS", "2026:H1:CFS"))
    kb = (fy["net_income_owners"] - h25["net_income_owners"] + h26["net_income_owners"]) / \
        ((h25["equity_owners"] + h26["equity_owners"]) / 2) * 100
    assert rows["105560"]["roe"][0] == pytest.approx(kb) and round(kb, 2) == 10.47
    fy, q325, q326 = (_st("edgar", "RY", k) for k in ("2025:FY:CFS", "2025:Q3:CFS", "2026:Q3:CFS"))
    ry = (fy["net_income_owners"] - q325["net_income_owners"] + q326["net_income_owners"]) / \
        ((q325["equity_owners"] + q326["equity_owners"]) / 2) * 100
    assert rows["RY"]["roe"][0] == pytest.approx(ry) and round(ry, 2) == 16.21
    assert [f.meta["account"] for f in rows["TD"]["roe"][2]] == ["net_income"] * 3 + ["equity_total"] * 2   # no owners' lines
    assert "includes any NCI" in rows["TD"]["roe_basis"]


def test_cost_to_income_follows_each_market_and_matches_the_tasks():
    rows = _rows()
    assert rows["055550"]["cir"][0] == pytest.approx(_cir("055550"), abs=1e-6)          # k02's answer
    assert rows["316140"]["cir"][0] == pytest.approx(_cir("316140"), abs=1e-6)
    assert round(rows["RY"]["cir"][0], 2) == 52.81 and round(rows["TD"]["cir"][0], 2) == 50.19   # c02's answers


def test_cet1_is_the_reported_ratio_and_woori_has_none():
    rows = _rows()
    assert {c: rows[c]["cet1"][0] for c in ("105560", "055550", "086790")} == {"105560": 13.74, "055550": 13.43,
                                                                            "086790": 13.27}
    assert rows["316140"]["cet1"] is None and "12.89%" in rows["316140"]["cet1_note"]
    assert [rows[c]["cet1"][0] for c in ("RY", "TD", "BNS", "BMO", "CM")] == [13.5, 14.3, 13.1, 13.0, 13.4]
    med = comparison()["medians"]
    assert med["Korea"]["cet1"][0] == 13.43 and med["Canada"]["cet1"][0] == 13.4
    assert med["Canada"]["roe"][0] == pytest.approx(statistics.median(rows[c]["roe"][0] for c in
                                                                      ("RY", "TD", "BNS", "BMO", "CM")))


def test_report_cites_every_figure_in_english():
    md = render()
    assert uncited_figures(md) == []
    assert not re.search(r"[가-힣]", md)                                    # English throughout
    assert "13.74% [fact `105560.cet1.2026H1` from DART 20260814004200]" in md
    assert "from DART amended half-year report 20261007000223" in md                # Shinhan read from the amendment
    assert "16.21% [facts `RY.2025FY.CFS.net_income_owners`" in md and "40-F 0001193125-25-305927" in md
    assert "dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814004200" in md and "sec.gov/Archives/edgar/data/1000275/" in md
    assert "not investment advice" in md.lower()


def test_compare_cli_writes_lf_markdown(tmp_path):
    out = tmp_path / "compare.md"
    assert finance_main(["compare", "--out", str(out)]) == 0
    raw = out.read_bytes()
    assert b"\r\n" not in raw and raw.startswith(b"# Korea and Canada side by side")
