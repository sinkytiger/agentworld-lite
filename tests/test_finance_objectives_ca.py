"""Canada's Big 5 objectives scorecard (agentworld_lite/finance/objectives_ca.py), offline. The quotes were checked
against the five MD&A exhibits on 2026-10-22; the saved-copy tests here use synthetic pages built from the quotes."""

import hashlib
from html import escape
from pathlib import Path

import pytest

from agentworld_lite.finance import earnings_review_ca as er
from agentworld_lite.finance import objectives_ca as oc
from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.catalog import BIG5
from agentworld_lite.finance.note import uncited_figures


def test_every_bank_has_a_quoted_roe_objective_and_a_hashed_source():
    assert list(oc.DOCS) == list(BIG5)
    for bank in BIG5:
        mine = [o for o in oc.OBJECTIVES if o.bank == bank]
        assert any(o.metric == "roe" and o.low for o in mine) and all(o.quote and o.metric in oc.METRIC for o in mine)
        d = oc.DOCS[bank]
        assert len(d["sha256"]) == 64 and d["bytes"] > 1_000_000 and d["document"].endswith(".htm")
    assert sum(len(oc.quotes(b)) for b in BIG5) == 35


def test_verdict_rules():
    O = oc.Objective
    plus = O("XX", "roe", 15, None, "", "medium term", "ROE of 15%+")
    assert [oc.verdict(plus, v) for v in (14.99, 15, 30)] == ["below", "meets", "meets"]
    rng = O("XX", "eps_growth", 7, 10, "adjusted", "medium term", "EPS growth of 7-10%")
    assert [oc.verdict(rng, v) for v in (6.9, 7, 10, 10.1)] == ["below", "meets", "meets", "above range"]
    about = O("XX", "roe", 16, 16, "adjusted", "fiscal 2029", "~16% Adj. ROE")       # "~16%": half a point either way
    assert [oc.verdict(about, v) for v in (15.5, 16.5, 15.4, 16.6)] == ["at target", "at target", "below", "above"]
    assert oc.verdict(O("XX", "cet1", None, None, "", "medium term", "Strong CET1"), 13.5) == "no figure to test"
    assert oc.verdict(plus, None) == "no figure to test"


def _row(md, bank):
    return next(line for line in md.splitlines() if line.startswith(f"| {oc.NAMES[bank]} |"))


def test_scorecard_cites_everything_and_only_rbc_meets_its_roe_objective():
    md = oc.render()
    assert uncited_figures(md, extra=(oc.QUOTE,)) == []
    assert "**meets** (reported)" in _row(md, "RY") and "16.30% [fact `RY.fy2025.roe_reported`" in _row(md, "RY")
    assert "**below** (adjusted)" in _row(md, "TD") and "12.90% [fact `TD.fy2025.roe_adjusted`" in _row(md, "TD")
    for bank in ("BNS", "BMO", "CM"):
        assert "**below**" in _row(md, bank)
    assert "13.50% [fact `RY.2026Q3.CFS.cet1_ratio`" in _row(md, "RY")
    assert round(oc.progress("RY")["eps_growth_ytd"][0], 2) == 17.36
    assert round(oc.progress("TD")["eps_growth_ytd"][0], 2) == -22.84
    for bank in BIG5:
        d = oc.DOCS[bank]
        assert all(f"“{q}” [quote from 40-F {d['accession']} {d['exhibit']}" in md for q in oc.quotes(bank))
        assert f"`{d['sha256']}`" in md
    assert "## Korea and Canada side by side" in md


def _page(quotes):
    """Quotes laid out the way MD&A tables render them: words split across cells, entities, line breaks."""
    cells = []
    for q in quotes:
        head, _, tail = q.partition(" ")
        cells.append(f"<td>{escape(head, quote=False)}</td>\n  <td>{escape(tail, quote=False).replace(' ', '&nbsp;', 1)}</td>")
    return "<html><body><table><tr>" + "</tr>\n<tr>".join(cells) + "</tr></table></body></html>"


def test_verify_local_finds_renamed_copies_by_hash_and_reports_missing_quotes(tmp_path, monkeypatch):
    ry = _page(oc.quotes("RY")).encode()
    (tmp_path / "download (1).htm").write_bytes(ry)                                       # renamed by the browser
    (tmp_path / "TD_ex992.htm").write_bytes(_page(oc.quotes("TD")[1:]).encode())         # other bytes, one quote short
    monkeypatch.setitem(oc.DOCS, "RY", {**oc.DOCS["RY"], "bytes": len(ry), "sha256": hashlib.sha256(ry).hexdigest()})
    r = oc.verify_local(tmp_path)
    assert r["RY"] == {"file": "download (1).htm", "same_sha256": True, "missing": []}
    assert r["TD"] == {"file": "TD_ex992.htm", "same_sha256": False, "missing": [oc.quotes("TD")[0]]}
    assert r["BNS"] == r["BMO"] == r["CM"] == {"file": None}


def test_cli(tmp_path, capsys):
    out = tmp_path / "objectives_ca.md"
    assert finance_main(["objectives-ca", "--out", str(out)]) == 0
    assert b"\r\n" not in out.read_bytes() and out.read_text(encoding="utf-8").startswith("# Canada's Big 5")
    capsys.readouterr()
    assert finance_main(["objectives-ca", "--verify-local", str(tmp_path)]) == 1
    assert "RY: no saved copy of d95203dex2.htm" in capsys.readouterr().out
    assert finance_main(["objectives-ca", "--verify-local", str(tmp_path / "nope")]) == 1


@pytest.mark.parametrize("bank", BIG5)
def test_each_earnings_review_quotes_its_banks_objectives(bank):
    md = er.render(er.review(bank, today="2026-10-22"))
    section = md.split("## Medium-term objectives\n\n")[1].split("\n## ")[0]
    accession = oc.DOCS[bank]["accession"]
    assert all(f"“{o.quote}” [quote from 40-F {accession}" in section for o in oc.OBJECTIVES if o.bank == bank)
    assert "objectives-ca" in section


def test_the_scorecard_never_touches_the_network():
    src = Path(oc.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    for word in ("urlopen", "requests", "socket", "subprocess", "EdgarClient"):
        assert word not in src, word
