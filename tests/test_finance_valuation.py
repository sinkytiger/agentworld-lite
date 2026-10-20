"""Valuation snapshot: TTM EPS, P/E, peer range arithmetic, prices with their exhibit, and citations. Offline."""

import json
import re
import statistics
from pathlib import Path

import pytest

from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.catalog import BIG5
from agentworld_lite.finance.note import uncited_figures
from agentworld_lite.finance.valuation import RATING_WORDS, ValuationError, snapshot

ROOT = Path(__file__).resolve().parents[1]
BANKS = ROOT / "data" / "finance" / "edgar"
EPS = {"RY": (14.07, 10.31, 12.10), "TD": (11.56, 9.72, 7.50), "BNS": (5.67, 4.02, 6.00), "BMO": (11.44, 8.47, 9.30),
       "CM": (8.57, 6.37, 8.22)}  # FY 2025, Q3 YTD 2025, Q3 YTD 2026 diluted EPS from the dataset
PRICE = {"RY": 293.41, "TD": 168.04, "BNS": 122.97, "BMO": 251.47, "CM": 166.04}


def _bank(code):
    return json.loads((BANKS / f"{code}.json").read_text(encoding="utf-8"))


def test_every_bank_has_its_q3_close_with_the_exhibit_it_came_from():
    for code in BIG5:
        data = _bank(code)
        q3 = data["statements"]["2026:Q3:CFS"]
        assert data["prices"]["2026-07-31"] == PRICE[code]
        src = data["price_sources"]["2026-07-31"]
        assert q3["doc_id"] in src and re.search(r"Exhibit 99\.\d of Form 6-K", src) and "$" not in src, code


def test_arithmetic_matches_the_filed_eps_and_prices():
    _, n = snapshot("RY")
    for code, (fy, prev, cur) in EPS.items():
        assert n["ttm_eps"][code] == pytest.approx(fy - prev + cur)
        assert n["pe"][code] == pytest.approx(PRICE[code] / (fy - prev + cur))
    assert n["pe"]["RY"] == pytest.approx(18.50, abs=0.005)                     # c01's answer
    peers = [n["pe"][c] for c in BIG5 if c != "RY"]
    assert n["peer_pe"] == pytest.approx({"low": min(peers), "median": statistics.median(peers), "high": max(peers)})
    assert n["implied"]["low"] == pytest.approx(15.86 * min(peers))
    assert n["implied"]["high"] == pytest.approx(15.86 * max(peers))
    assert n["implied"]["low"] < n["price"] < n["implied"]["high"]


def test_snapshot_cites_every_number_and_gives_no_rating():
    md, _ = snapshot("RY")
    assert uncited_figures(md) == []
    assert "18.50x [facts `RY.price.2026-07-31`, `RY.2025FY.CFS.eps_diluted`" in md
    assert "C$168.04 [fact `TD.price.2026-07-31` from TSX closing price" in md and "0001562762-26-000098" in md
    body = md.split("## Disclaimer")[0]
    assert not any(re.search(rf"(?<!no )\b{w}\b", body, re.I) for w in RATING_WORDS)   # only "no target price"
    assert "not investment advice" in md.lower() and "## Methods" in md


def test_other_subjects_and_errors():
    md, n = snapshot("TD")
    assert "# Valuation snapshot: The Toronto-Dominion Bank (TD)" in md
    assert sorted(n["peer_pe"].values())[0] == pytest.approx(min(n["pe"][c] for c in BIG5 if c != "TD"))
    assert snapshot("scotiabank")[0].startswith("# Valuation snapshot: The Bank of Nova Scotia (BNS)")
    with pytest.raises(ValuationError, match="unknown bank"):
        snapshot("XYZ")
    with pytest.raises(ValuationError, match="filed by"):
        snapshot("RY", as_of="2026-08-26")                                       # before the Q3 6-Ks


def test_valuation_cli_writes_lf_markdown(tmp_path):
    out = tmp_path / "ry.md"
    assert finance_main(["valuation", "--bank", "RY", "--out", str(out)]) == 0
    raw = out.read_bytes()
    assert b"\r\n" not in raw and raw.startswith(b"# Valuation snapshot: Royal Bank of Canada (RY)")
    assert finance_main(["valuation", "--bank", "nope"]) == 1
