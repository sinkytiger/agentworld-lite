"""News sector risk digest: RSS parsing, bank and theme tagging, cited filed figures. Fixture XML only, no network."""

import json
import urllib.error
from pathlib import Path

import pytest

from agentworld_lite.finance import news
from agentworld_lite.finance.__main__ import main as finance_main
from agentworld_lite.finance.dataset import Dataset
from agentworld_lite.finance.news import (FEEDS, NewsError, bank_figures, bank_patterns, build_digest, parse_feed, tag)
from agentworld_lite.finance.note import uncited_figures

ROOT = Path(__file__).resolve().parents[1]
BANKS = ROOT / "data" / "finance" / "edgar"

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Banking</title>
<item><title>TD and BMO are in the crosshairs of a U.S. regulator</title>
  <link>https://example.com/td-bmo-regulator</link><pubDate>Fri, 16 Oct 2026 14:00:00 +0000</pubDate>
  <description><![CDATA[<p>The probe follows TD&#8217;s money-laundering settlement.</p>]]></description>
  <content:encoded><![CDATA[<p>FULL ARTICLE TEXT THAT MUST NOT BE STORED</p>]]></content:encoded></item>
<item><title>RBC raises mortgage rates as bond yields climb</title>
  <link>https://example.com/rbc-mortgage</link><pubDate>Mon, 19 Oct 2026 09:30:00 -0400</pubDate>
  <description>Royal Bank of Canada lifted fixed mortgage rates by 0.15 percentage points.</description></item>
<item><title>Canada's biggest banks form coalition to tackle fraud</title>
  <link>https://example.com/coalition</link><pubDate>Wed, 14 Oct 2026 12:00:00 +0000</pubDate>
  <description>Lenders including CIBC and Scotiabank will share data.</description></item>
<item><title>Nova Scotia lobster season opens early</title>
  <link>https://example.com/lobster</link><pubDate>Tue, 13 Oct 2026 12:00:00 +0000</pubDate>
  <description>Prices are up 12% on last year.</description></item>
<item><title>Old story about tariffs</title>
  <link>https://example.com/old</link><pubDate>Mon, 01 Sep 2026 12:00:00 +0000</pubDate>
  <description>Out of the window.</description></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Economy</title>
<entry><title>Bank of Canada rate hike could land in 2026 after inflation tops 3%</title>
  <link rel="alternate" href="https://example.com/boc"/><updated>2026-10-19T15:00:00Z</updated>
  <summary>Economists see a hike as tariffs push prices up.</summary></entry>
<entry><title>TD and BMO are in the crosshairs of a U.S. regulator</title>
  <link rel="alternate" href="https://example.com/td-bmo-regulator"/><updated>2026-10-16T14:00:00Z</updated>
  <summary>Duplicate of an item in the other feed.</summary></entry>
</feed>"""

VALET = {"V39079": {"observations": [{"d": "2026-10-19", "V39079": {"v": "2.25"}}]},
         "BD.CDN.5YR.DQ.YLD": {"observations": [{"d": "2026-10-16", "BD.CDN.5YR.DQ.YLD": {"v": "2.91"}}]}}
FEEDS_T = {"Banking": "https://feeds.test/banking", "Economy": "https://feeds.test/economy"}


def fake_fetch(urls: dict):
    calls = []

    def fetch(url):
        calls.append(url)
        if url not in urls:
            raise urllib.error.URLError("refused")
        return urls[url]
    return fetch, calls


def _urls():
    return {FEEDS_T["Banking"]: RSS, FEEDS_T["Economy"]: ATOM,
            **{news.VALET_URL.format(series=s): json.dumps(v).encode() for s, v in VALET.items()}}


def test_parse_keeps_only_headline_link_date_and_short_summary():
    items = parse_feed(RSS, "Banking")
    assert set(items[0]) == {"feed", "title", "link", "published", "summary"}
    assert items[0]["summary"] == "The probe follows TD's money-laundering settlement."   # curly quote normalized
    assert all("FULL ARTICLE TEXT" not in json.dumps(x) for x in items)
    assert items[1]["published"] == "2026-10-19"                       # -0400 converted to UTC
    assert parse_feed(ATOM, "Economy")[0]["link"] == "https://example.com/boc"
    long = parse_feed(RSS.replace(b"Lenders including", b"word " * 200 + b"Lenders"), "B")[2]["summary"]
    assert len(long) <= news.SUMMARY_CHARS + 4 and long.endswith("...")


def test_banks_and_themes_are_tagged_by_literal_rules():
    pats = bank_patterns(Dataset(BANKS))
    t = {x["link"].rsplit("/", 1)[-1]: tag(x, pats) for x in parse_feed(RSS, "Banking")}
    assert t["td-bmo-regulator"].banks == ["TD", "BMO"] and set(t["td-bmo-regulator"].themes) == {"regulation"}
    assert t["rbc-mortgage"].banks == ["RY"] and set(t["rbc-mortgage"].themes) == {"housing", "rates"}
    assert t["coalition"].banks == ["BNS", "CM"] and t["coalition"].sector
    assert t["lobster"].banks == [] and not t["lobster"].sector          # "Nova Scotia" is not Scotiabank


def test_bank_figures_match_the_dataset():
    figs = bank_figures(Dataset(BANKS), "RY", "2026-10-20")
    assert figs["year"] == 2026 and figs["cet1"][0] == 13.5
    assert round(figs["efficiency"][0], 2) == 52.81                     # c02's answer
    assert [round(v / 1e6) for v, _, _ in figs["ni"]] == [5643, 11015 - 5643, 5879]
    assert bank_figures(Dataset(BANKS), "RY", "2026-08-26") is None   # 2026 Q3 6-K filed 08-27; no 2025 Q1/H1 kept


def test_digest_cites_every_filed_figure_and_counts_themes(tmp_path):
    fetch, calls = fake_fetch(_urls())
    md, items = build_digest(FEEDS_T, cache_dir=tmp_path, as_of="2026-10-20", days=14, fetch=fetch, pause=0)
    assert len(items) == 5                                                 # dedupe by link, old item dropped
    assert "| Royal Bank of Canada (RY) | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 1 |" in md
    assert "| Banks as a group | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |" in md       # the coalition item names banks
    assert "[TD and BMO are in the crosshairs of a U.S. regulator](https://example.com/td-bmo-regulator)" in md
    assert "2.25% [Bank of Canada Valet `V39079`, 2026-10-19]" in md
    assert "13.50% [fact `RY.2026Q3.CFS.cet1_ratio` from 6-K 0001193125-26-369461]" in md
    assert "52.81% [facts `RY.2026Q3.CFS.non_interest_expense`" in md and "Big 5 average: 13.46% [facts" in md
    masked = news.re.sub(r"\[[^\]]*\]\([^)]*\)", "", md)
    assert uncited_figures(masked, extra=(news.VALET_CITATION,)) == []
    assert "Regulation, AML and fines with the cost line" in md
    cached = json.loads((tmp_path / "feed_banking.json").read_text(encoding="utf-8"))
    assert all(set(x) == {"feed", "title", "link", "published", "summary"} for x in cached["items"])
    assert not any("FULL ARTICLE" in p.read_text(encoding="utf-8") for p in tmp_path.iterdir())


def test_failed_feed_is_reported_and_cache_is_used_offline(tmp_path):
    fetch, _ = fake_fetch(_urls())
    build_digest(FEEDS_T, cache_dir=tmp_path, as_of="2026-10-20", fetch=fetch, pause=0)
    fetch2, calls = fake_fetch({})
    md, items = build_digest({**FEEDS_T, "Down": "https://feeds.test/down"}, cache_dir=tmp_path,
                             as_of="2026-10-20", fetch=fetch2, pause=0)
    assert "fetch failed: refused; cached copy from" in md and "| Down | https://feeds.test/down | fetch failed: " \
           "refused; no cached copy | 0 |" in md
    assert len(items) == 5
    md, _ = build_digest(FEEDS_T, cache_dir=tmp_path, as_of="2026-10-20", offline=True, fetch=None, pause=0)
    assert "offline; cached copy" in md


def test_digest_refuses_an_uncited_figure(monkeypatch, tmp_path):
    fetch, _ = fake_fetch(_urls())
    real = news.render
    monkeypatch.setattr(news, "render", lambda *a: real(*a) + "\nRBC earned C$5,879 million.\n")
    with pytest.raises(NewsError, match="C\\$5,879 million"):
        build_digest(FEEDS_T, cache_dir=tmp_path, as_of="2026-10-20", fetch=fetch, pause=0)


def test_news_cli_writes_a_digest_offline(tmp_path):
    fetch, _ = fake_fetch(_urls())
    build_digest(FEEDS_T, cache_dir=tmp_path / "c", as_of="2026-10-20", fetch=fetch, pause=0)
    out = tmp_path / "digest.md"
    feeds = [f"{k}={v}" for k, v in FEEDS_T.items()]
    assert finance_main(["news", "--offline", "--cache", str(tmp_path / "c"), "--as-of", "2026-10-20",
                         "--feed", *feeds, "--out", str(out)]) == 0
    raw = out.read_bytes()
    assert b"\r\n" not in raw and b"# Big 5 Canadian banks: sector risk digest, 2026-10-20" in raw
    assert "Financial Post" in FEEDS and "CBC Business" not in FEEDS  # CBC ignores the declared bot User-Agent
