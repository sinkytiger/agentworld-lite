"""Canadian earnings review (agentworld_lite/finance/earnings_review_ca.py), offline. The fiscal 2025 H1 reports added
in some tests are synthetic (flows and EPS of each bank's fiscal 2025 Q3 report x 2/3, balances and ratios unchanged,
accession numbers that do not exist on EDGAR)."""

import json
import shutil
from pathlib import Path

import pytest

from agentworld_lite.finance import earnings_review_ca as er
from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.catalog import BIG5
from agentworld_lite.finance.note import uncited_figures

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "finance" / "edgar"
TODAY = "2026-10-22"
FLOWS = ("revenue", "non_interest_expense", "net_income", "net_income_owners", "net_income_common", "eps_basic",
         "eps_diluted")
C03 = {"RY": (16_894, 11_015), "TD": (12_512, 7_991), "BNS": (7_401, 4_623), "BMO": (6_563, 4_896),
       "CM": (7_601, 5_330)}  # net income to common, Q3 and H1 year to date, C$ millions (catalog c03)


def _st(code, key, data=DATA):
    return json.loads((data / f"{code}.json").read_text(encoding="utf-8"))["statements"][key]


@pytest.mark.parametrize("code, efficiency", [("RY", 52.81), ("TD", 50.19)])
def test_latest_report_review_matches_the_desk_answers(code, efficiency):
    rv = er.review(code, today=TODAY)
    assert (rv.year, rv.period, rv.label) == (2026, "Q3", "fiscal 2026 Q3")
    q3, h1 = _st(code, "2026:Q3:CFS"), _st(code, "2026:H1:CFS")
    for a in ("revenue", "non_interest_expense", "net_income_common"):
        assert rv.now.values[a][0] == q3[a] - h1[a]
    assert round(er._efficiency(rv.now.values)[0], 2) == efficiency                       # c02 answers
    assert rv.prev.label == "fiscal 2026 Q2" and rv.year_ago is None
    md = er.render(rv)
    assert md.startswith(f"# {rv.name} ({code}): fiscal 2026 Q3 earnings review\n")
    assert er.BANNER in md and uncited_figures(md) == []
    assert "no earnings-call transcript and no consensus estimate" in md
    assert "fiscal 2025 Q3 alone cannot be computed: the dataset has no fiscal 2025 H1 report" in md
    assert "| Line | 9 months, fiscal 2026 | 9 months, fiscal 2025 | vs a year earlier |" in md
    assert "| Line | fiscal 2026 Q3 | fiscal 2026 Q2 | vs previous quarter |\n" in md
    assert f"| Efficiency ratio | {efficiency:.2f}% [facts `{code}.2026Q3.CFS.non_interest_expense`" in md
    assert md.split("## Medium-term objectives\n\n")[1].startswith("No quoted medium-term objectives yet.\n")
    assert [name for name, ok, _ in rv.checks if not ok] == ["Comparison reports are in the dataset"]


def test_big5_net_income_to_common_and_cet1_match_c03():
    rvs = {code: er.review(code, today=TODAY) for code in BIG5}
    assert {c: rv.now.values["net_income_common"][0] for c, rv in rvs.items()} == \
        {c: (q3 - h1) * 1_000_000 for c, (q3, h1) in C03.items()}
    cet1 = [rv.now.cum["cet1_ratio"].value for rv in rvs.values()]
    assert round(sum(cet1) / 5, 2) == 13.46 and round(max(cet1) - min(cet1), 2) == 1.30
    for rv in rvs.values():
        assert uncited_figures(er.render(rv)) == []


def test_td_without_equity_owners_renders():
    rv = er.review("TD", today=TODAY)
    assert "equity_owners" not in rv.now.cum and "net_income_owners" not in rv.now.values
    name, ok, note = next(c for c in rv.checks if c[0].startswith("Total equity"))
    assert ok and "not in the filing's XBRL" in note
    md = er.render(rv)
    assert "| Net income to common shareholders | C$4,521 million [facts `TD.2026Q3.CFS.net_income_common`" in md


@pytest.fixture
def edgar_copy(tmp_path):
    d = tmp_path / "edgar"
    shutil.copytree(DATA, d)
    return d


@pytest.fixture
def with_2025_h1(edgar_copy):
    """The dataset with a synthetic fiscal 2025 H1 report for every bank."""
    for p in edgar_copy.glob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        q3 = data["statements"]["2025:Q3:CFS"]
        h1 = {k: (round(v * 2 / 3, 2) if k.startswith("eps") else round(v * 2 / 3 / 1e6) * 1e6)
              if k in FLOWS and v is not None else v for k, v in q3.items()}
        h1.update(doc_id=f"SYNTHETIC-2025H1-{data['stock_code']}", filed="2025-05-28", period_end="2025-04-30")
        data["statements"]["2025:H1:CFS"] = h1
        p.write_text(json.dumps(data), encoding="utf-8")
    return edgar_copy


def test_year_ago_quarter_appears_when_the_prior_h1_report_exists(with_2025_h1):
    rv = er.review("RY", data_dir=with_2025_h1, today=TODAY)
    assert rv.year_ago.label == "fiscal 2025 Q3"
    h1 = _st("RY", "2025:H1:CFS", with_2025_h1)
    assert rv.year_ago.values["revenue"][0] == _st("RY", "2025:Q3:CFS")["revenue"] - h1["revenue"]
    md = er.render(rv)
    assert uncited_figures(md) == [] and "cannot be computed" not in md and "SYNTHETIC-2025H1-RY" in md
    assert ("| Line | fiscal 2026 Q3 | fiscal 2026 Q2 | vs previous quarter | fiscal 2025 Q3 | vs a year earlier |"
            in md)
    assert "## Year to date" in md and all(ok for _, ok, _ in rv.checks)
    model = er.coverage_model(rv, er.Dataset(with_2025_h1))
    assert list(model["quarters"])[:2] == ["fiscal 2025 Q3", "fiscal 2025 Q4"]
    assert "SYNTHETIC-2025H1-RY" in model["quarters"]["fiscal 2025 Q3"]["revenue"]["accession"]
    assert er.render(er.review("TD", data_dir=with_2025_h1, today=TODAY))   # TD: no equity_owners, no owners' income


def test_qc_flags_a_large_revenue_move_and_a_filing_after_today(edgar_copy):
    p = edgar_copy / "BMO.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["statements"]["2025:Q3:CFS"]["revenue"] /= 2
    p.write_text(json.dumps(data), encoding="utf-8")
    rv = er.review("BMO", data_dir=edgar_copy, today="2026-08-01")
    flags = {name: note for name, ok, note in rv.checks if not ok}
    assert list(flags) == ["Comparison reports are in the dataset", "Filing date is not after today",
                           "Revenue year to date within 40 percent of a year earlier",
                           "Net income within 25 percent of the previous quarter"]  # BMO's Q3 goodwill charge
    assert flags["Net income within 25 percent of the previous quarter"].startswith("-32.97% [facts")
    assert flags["Revenue year to date within 40 percent of a year earlier"].startswith("+117.48% [facts")
    assert "the XBRL concept is the same in both years (`ifrs-full:Revenue`)" in flags[
        "Revenue year to date within 40 percent of a year earlier"]
    assert uncited_figures(er.render(rv)) == []
    data["statements"]["2025:Q3:CFS"]["concepts"]["revenue"] = "ext:TotalRevenue"
    p.write_text(json.dumps(data), encoding="utf-8")
    rv = er.review("BMO", data_dir=edgar_copy, today=TODAY)
    assert any("the XBRL concept changed (`ext:TotalRevenue` to `ifrs-full:Revenue`)" in n for _, _, n in rv.checks)


def test_write_stages_the_review_and_the_model(tmp_path):
    rv = er.review("CM", today=TODAY)
    d = er.write(rv, tmp_path)
    assert d.name == "CM_2026Q3" and {p.name for p in d.iterdir()} == {"review.md", "model.json"}
    assert b"\r\n" not in (d / "review.md").read_bytes() and b"\r\n" not in (d / "model.json").read_bytes()
    model = json.loads((d / "model.json").read_text(encoding="utf-8"))
    assert list(model["quarters"]) == ["fiscal 2025 Q4", "fiscal 2026 Q1", "fiscal 2026 Q2", "fiscal 2026 Q3"]
    assert model["period_ends"]["fiscal 2025 Q4"] == "2025-10-31"
    ni = model["quarters"]["fiscal 2026 Q3"]["net_income_common"]
    assert ni["value"] == (7_601 - 5_330) * 1_000_000
    assert ni["facts"] == ["CM.2026Q3.CFS.net_income_common", "CM.2026H1.CFS.net_income_common"]
    assert ni["accession"] == sorted(_st("CM", k)["doc_id"] for k in ("2026:Q3:CFS", "2026:H1:CFS"))
    assert round(model["quarters"]["fiscal 2026 Q3"]["efficiency_ratio"]["value"], 2) == \
        round(er._efficiency(rv.now.values)[0], 2)


def test_groups():
    assert er.groups("all") == list(BIG5)
    assert er.groups("Royal Bank of Canada") == er.groups("ry") == ["RY"]
    assert er.groups("CIBC") == er.groups("Canadian Imperial Bank of Commerce") == ["CM"]
    with pytest.raises(er.ReviewError, match="unknown bank"):
        er.groups("105560")


def test_cli(tmp_path, capsys):
    out = tmp_path / "ca"
    assert finance_main(["earnings-review", "--market", "ca", "--group", "RY", "--out-dir", str(out)]) == 0
    assert {p.name for p in (out / "RY_2026Q3").iterdir()} == {"review.md", "model.json"}
    assert "Royal Bank of Canada fiscal 2026 Q3" in capsys.readouterr().out
    assert finance_main(["earnings-review", "--market", "ca", "--group", "TD", "--draft", "--out-dir", str(out)]) != 0
    assert "--draft is not available with --market ca" in capsys.readouterr().err
    assert not (out / "TD_2026Q3").exists()
    assert finance_main(["earnings-review", "--market", "ca", "--group", "nope", "--out-dir", str(out)]) == 1
    kr = tmp_path / "kr"
    assert finance_main(["earnings-review", "--market", "kr", "--group", "086790", "--out-dir", str(kr)]) == 0
    assert finance_main(["earnings-review", "--group", "woori", "--out-dir", str(kr)]) == 0          # kr by default
    assert {p.name for p in kr.iterdir()} == {"086790_2026H1", "316140_2026H1"}


def test_the_reviewer_posts_nothing_and_never_runs_git():
    src = Path(er.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    for word in ("subprocess", "os.system", "git ", "urlopen", "requests", "smtplib", "socket"):
        assert word not in src, word
