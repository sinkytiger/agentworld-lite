"""Sector risk digest for the Big 5 Canadian banks, from news feeds and filed figures. No model writes it.

    python -m agentworld_lite.finance news --cache C:/news_cache --out digest.md

Reads RSS feeds and keeps, per item, only the headline, link, publication date, feed name and the feed's own short
summary. Article pages are never fetched, and article text that a feed carries (e.g. RSS `content:encoded`) is
dropped before anything is cached. Each item is tagged with the banks it names (names and aliases from
data/finance/edgar) and with risk themes by the literal keyword rules in THEMES, which the digest prints. The digest
then pairs each bank's news themes with its filed figures (CET1 ratio, single-quarter efficiency ratio, net income to
common by quarter); every filed figure is followed by its fact id and accession number. The Bank of Canada policy
rate and 5-year yield come from the Valet API (free, no key) and are cited by series and observation date.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .catalog import BIG5
from .dataset import Dataset, statement_facts
from .env import REPO_ROOT
from .note import cited, uncited_figures

FEEDS = {
    "Financial Post": "https://financialpost.com/feed",
    "Financial Post Banking": "https://financialpost.com/category/fp-finance/banking/feed",
    "Financial Post Economy": "https://financialpost.com/category/news/economy/feed",
    "CBC Business": "https://www.cbc.ca/webfeed/rss/rss-business",
}
USER_AGENT = "agentworld-lite-news-digest/0.1 (+https://github.com/sinkytiger/agentworld-lite)"
VALET_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json?recent=1"
VALET_SERIES = {"V39079": "Bank of Canada target for the overnight rate",
                "BD.CDN.5YR.DQ.YLD": "Government of Canada 5-year benchmark bond yield"}
SUMMARY_CHARS = 280
QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
HEADLINES_PER_THEME = 5

# Short upper-case names match case-sensitively as whole words; longer names match case-insensitively.
EXTRA_NAMES = {"RY": ["RBC", "Royal Bank"], "TD": ["TD", "Toronto Dominion"], "BNS": ["Scotiabank"],
               "BMO": ["BMO", "Bank of Montreal"], "CM": ["CIBC"]}
SECTOR = [r"\bbig (?:five|six|5|6) banks?\b", r"\bbig banks\b", r"\bCanada's (?:biggest|largest|big|major) banks\b",
          r"\bCanadian banks\b", r"\bbank stocks\b"]
THEMES = {
    "credit": ("Credit and loan losses", [r"credit loss", r"loan loss", r"provisions? for credit", r"\bPCLs?\b",
                                          r"impaired loans?", r"delinquen", r"\bdefaults?\b", r"bad loans?",
                                          r"insolvenc"]),
    "housing": ("Housing and mortgages", [r"\bhousing\b", r"mortgage", r"home prices?", r"home sales", r"real estate",
                                          r"\bcondos?\b"]),
    "trade": ("Tariffs and trade", [r"tariff", r"trade war", r"trade deal", r"\bCUSMA\b", r"\bUSMCA\b", r"\bduties\b",
                                    r"trade (?:dispute|tensions?)"]),
    "rates": ("Rates and the Bank of Canada", [r"Bank of Canada", r"interest rates?", r"rate (?:cut|hike)s?",
                                               r"policy rate", r"bond yields?", r"\binflation\b", r"\bMacklem\b"]),
    "capital": ("Capital and CET1", [r"\bCET1\b", r"capital ratios?", r"capital (?:buffer|requirement)s?",
                                     r"domestic stability buffer", r"\bbuybacks?\b", r"share repurchases?"]),
    "regulation": ("Regulation, AML and fines", [r"\bOSFI\b", r"\bregulators?\b", r"\bAML\b", r"money.laundering",
                                                 r"\bFINTRAC\b", r"\bfined\b", r"\bfines\b", r"penalt(?:y|ies)",
                                                 r"\bprobe\b", r"\bguilty\b"]),
    "restructuring": ("Restructuring and layoffs", [r"layoffs?", r"job cuts?", r"cut(?:s|ting)? jobs", r"restructuring",
                                                    r"severance", r"workforce reduction", r"\bdivest"]),
}
# Which filed figure each theme is set beside, and why. Provision for credit losses is not in the dataset.
PAIRING = {
    "credit": ("earnings", "credit costs reach net income; provision for credit losses itself is not in the dataset"),
    "housing": ("earnings", "mortgage volumes and losses reach net income"),
    "trade": ("earnings", "trade shocks reach loan demand and credit costs, and through them net income"),
    "rates": ("earnings", "rates move net interest income"),
    "capital": ("capital", "CET1 is the regulatory capital measure"),
    "regulation": ("cost", "compliance, remediation and fines run through non-interest expense"),
    "restructuring": ("cost", "severance and restructuring charges run through non-interest expense"),
}
VALET_CITATION = r"\[Bank of Canada Valet `[^`]+`, [^\]]*\]"


class NewsError(Exception):
    pass


@dataclass
class Item:
    feed: str
    title: str
    link: str
    published: str          # YYYY-MM-DD (UTC)
    summary: str            # the feed's own short summary, tags stripped, at most SUMMARY_CHARS
    banks: list
    sector: bool
    themes: list


# ---------------------------------------------------------------------- feeds
def http_get(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/rss+xml, application/xml, application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def clean(text: str | None, limit: int | None = None) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(text or "")))
    s = re.sub(r"\s+", " ", s.translate(QUOTES)).strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " ..."
    return s


def _date(text: str | None) -> str:
    if not text:
        return ""
    try:
        d = email.utils.parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return ""
    if d.tzinfo is not None:
        d = d.astimezone(dt.timezone.utc)
    return d.date().isoformat()


def parse_feed(xml_bytes: bytes, feed: str) -> list[dict]:
    """RSS 2.0 or Atom -> [{feed, title, link, published, summary}]. Nothing else from the item is kept."""
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        out.append({"feed": feed, "title": clean(it.findtext("title")), "link": (it.findtext("link") or "").strip(),
                    "published": _date(it.findtext("pubDate")),
                    "summary": clean(it.findtext("description"), SUMMARY_CHARS)})
    atom = "{http://www.w3.org/2005/Atom}"
    for e in root.iter(atom + "entry"):
        link = next((l.get("href") for l in e.findall(atom + "link") if l.get("rel", "alternate") == "alternate"), "")
        out.append({"feed": feed, "title": clean(e.findtext(atom + "title")), "link": (link or "").strip(),
                    "published": _date(e.findtext(atom + "published") or e.findtext(atom + "updated")),
                    "summary": clean(e.findtext(atom + "summary"), SUMMARY_CHARS)})
    return [x for x in out if x["title"] and x["link"]]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def load_feed(name: str, url: str, cache_dir: Path, fetch: Callable[[str], bytes] | None) -> tuple[list[dict], str]:
    """Fetch and parse one feed (fetch=None: cache only). The cache holds parsed items, never the raw feed."""
    path = cache_dir / f"feed_{_slug(name)}.json"
    reason = "offline"
    if fetch is not None:
        try:
            items = parse_feed(fetch(url), name)
            cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {"feed": name, "url": url, "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "items": items}
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            return items, f"fetched, {len(items)} items"
        except (urllib.error.URLError, OSError, ET.ParseError, ValueError) as exc:
            reason = f"fetch failed: {getattr(exc, 'reason', None) or type(exc).__name__}"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload["items"], f"{reason}; cached copy from {payload['fetched'][:10]}"
    return [], f"{reason}; no cached copy"


def load_valet(series: str, cache_dir: Path, fetch: Callable[[str], bytes] | None) -> tuple[dict | None, str]:
    path = cache_dir / f"valet_{_slug(series)}.json"
    reason = "offline"
    if fetch is not None:
        try:
            data = json.loads(fetch(VALET_URL.format(series=series)))
            obs = data["observations"][-1]
            point = {"series": series, "label": VALET_SERIES.get(series, series), "date": obs["d"],
                     "value": float(obs[series]["v"])}
            cache_dir.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(point, f, indent=1)
            return point, "fetched"
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
            reason = f"fetch failed: {getattr(exc, 'reason', None) or type(exc).__name__}"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), f"{reason}; cached copy"
    return None, f"{reason}; no cached copy"


# ---------------------------------------------------------------------- tagging
def bank_patterns(ds: Dataset) -> dict[str, list[re.Pattern]]:
    out = {}
    for code in BIG5:
        c = ds.companies[code]
        names = {re.sub(r"^The ", "", n) for n in [c.name, *c.data.get("aliases", []), *EXTRA_NAMES.get(code, [])]}
        pats = []
        for n in sorted(names, key=len, reverse=True):
            if len(n) <= 5 and n.isupper():
                pats.append(re.compile(rf"(?<![\w-]){re.escape(n)}(?![\w-])"))
            else:
                pats.append(re.compile(rf"\b{re.escape(n)}\b", re.I))
        out[code] = pats
    return out


def tag(raw: dict, banks: dict[str, list[re.Pattern]]) -> Item:
    text = f"{raw['title']} {raw['summary']}"
    named = [code for code, pats in banks.items() if any(p.search(text) for p in pats)]
    sector = any(re.search(p, text, re.I) for p in SECTOR)
    themes = [k for k, (_, pats) in THEMES.items() if any(re.search(p, text, re.I) for p in pats)]
    return Item(raw["feed"], raw["title"], raw["link"], raw["published"], raw["summary"], named, sector, themes)


# ---------------------------------------------------------------------- filed figures
def bank_figures(ds: Dataset, code: str, as_of: str) -> dict | None:
    """Latest fiscal year with Q1, H1 and Q3 reports filed by as_of -> CET1, single-quarter efficiency ratio, net
    income to common for each of the three quarters, each as (value, unit, input facts)."""
    c = ds.companies[code]
    years = sorted({int(k.split(":")[0]) for k, st in c.data["statements"].items()
                    if k.endswith(":Q3:CFS") and (st.get("filed") or "") <= as_of}, reverse=True)
    for year in years:
        f = {p: {x.meta["account"]: x for x in statement_facts(c, year, p, "CFS")} for p in ("Q1", "H1", "Q3")}
        if not all(f.values()) or any((x.meta.get("filed") or "") > as_of for p in f for x in f[p].values()):
            continue
        q1, h1, q3 = f["Q1"], f["H1"], f["Q3"]
        nie, rev, ni = "non_interest_expense", "revenue", "net_income_common"
        return {
            "year": year, "period_end": c.statement(year, "Q3", "CFS").get("period_end"),
            "cet1": (q3["cet1_ratio"].value, "%", [q3["cet1_ratio"]]),
            "efficiency": ((q3[nie].value - h1[nie].value) / (q3[rev].value - h1[rev].value) * 100, "%",
                           [q3[nie], h1[nie], q3[rev], h1[rev]]),
            "ni": [(q1[ni].value, c.currency, [q1[ni]]), (h1[ni].value - q1[ni].value, c.currency, [h1[ni], q1[ni]]),
                   (q3[ni].value - h1[ni].value, c.currency, [q3[ni], h1[ni]])],
        }
    return None


# ---------------------------------------------------------------------- digest
def build_digest(feeds: dict[str, str] | None = None, *, cache_dir: str | Path = "data/finance/cache/news",
                 as_of: str | None = None, days: int = 14, macro: bool = True, offline: bool = False,
                 fetch: Callable[[str], bytes] = http_get, data_dir: str | Path | None = None,
                 pause: float = 1.0) -> tuple[str, list[Item]]:
    feeds = feeds or FEEDS
    as_of = as_of or dt.date.today().isoformat()
    start = (dt.date.fromisoformat(as_of) - dt.timedelta(days=days - 1)).isoformat()
    cache = Path(cache_dir)
    ds = Dataset(Path(data_dir) if data_dir else REPO_ROOT / "data" / "finance" / "edgar")
    patterns = bank_patterns(ds)

    statuses, items, seen = [], [], set()
    for i, (name, url) in enumerate(feeds.items()):
        if i and not offline and pause:
            time.sleep(pause)
        raw, status = load_feed(name, url, cache, None if offline else fetch)
        kept = 0
        for r in raw:
            if not (start <= r["published"] <= as_of) or r["link"] in seen:
                continue
            seen.add(r["link"])
            items.append(tag(r, patterns))
            kept += 1
        statuses.append((name, url, status, kept))
    items.sort(key=lambda x: (x.published, x.title), reverse=True)
    points = [load_valet(s, cache, None if offline else fetch) for s in VALET_SERIES] if macro else []
    md = render(items, statuses, points, ds, as_of, start, days)
    bad = uncited_figures(re.sub(r"\[[^\]]*\]\([^)]*\)", "\x02", md), extra=(VALET_CITATION,))
    if bad:
        raise NewsError(f"uncited figures in the digest: {', '.join(bad)}")
    return md, items


def render(items: list[Item], statuses: list, points: list, ds: Dataset, as_of: str, start: str, days: int) -> str:
    names = {code: ds.companies[code].name for code in BIG5}
    relevant = [x for x in items if x.banks or x.sector]
    out = [
        f"# Big 5 Canadian banks: sector risk digest, {as_of}", "",
        "*Built from news headlines and the banks' filed figures by fixed rules; no language model wrote it. "
        "Educational sample, not investment advice.*", "",
        f"- **As of:** {as_of}. News window: the {days} days from {start} to {as_of}.",
        f"- **Items:** {len(items)} unique items in the window; {len(relevant)} name one of the banks or the sector.",
        "- **Figures:** every filed figure is followed by its fact id and the filing's form and SEC EDGAR accession "
        "number. Headlines are quoted with their link and date.", "",
        "## Sources", "", "| Feed | URL | Status | Items in window |", "|---|---|---|---|",
    ]
    out += [f"| {n} | {u} | {s} | {k} |" for n, u, s, k in statuses]
    out.append("")
    if points:
        out += ["## Macro context", ""]
        for point, status in points:
            if point:
                out.append(f"- {point['label']}: {point['value']:.2f}% [Bank of Canada Valet `{point['series']}`, "
                           f"{point['date']}]")
            else:
                out.append(f"- Bank of Canada Valet: {status}.")
        out.append("")

    out += ["## Theme counts", "", "Items that name a bank count for that bank (an item can name several). "
            "Items that name the banks as a group but none in particular count in the last row.", "",
            "| Bank | " + " | ".join(label for label, _ in THEMES.values()) + " | Items |",
            "|---|" + "---|" * (len(THEMES) + 1)]
    rows = [(f"{names[c]} ({c})", [x for x in items if c in x.banks]) for c in BIG5]
    rows.append(("Banks as a group", [x for x in items if x.sector and not x.banks]))
    for label, xs in rows:
        counts = Counter(t for x in xs for t in x.themes)
        out.append(f"| {label} | " + " | ".join(str(counts.get(k, 0)) for k in THEMES) + f" | {len(xs)} |")
    out.append("")

    out += ["## Latest headlines by theme", ""]
    for key, (label, _) in THEMES.items():
        xs = [x for x in items if key in x.themes][:HEADLINES_PER_THEME]
        out += [f"### {label}", ""]
        if not xs:
            out += ["No item in the window.", ""]
            continue
        for x in xs:
            who = f" · banks: {', '.join(x.banks)}" if x.banks else (" · banks as a group" if x.sector else "")
            out.append(f"- {x.published} · {x.feed} · [{x.title.replace('[', '(').replace(']', ')')}]({x.link}){who}")
        out.append("")

    out += ["## Bank by bank", ""]
    by_bank = {c: bank_figures(ds, c, as_of) for c in BIG5}
    have = [c for c in BIG5 if by_bank[c]]
    avg = (sum(by_bank[c]["cet1"][0] for c in have) / len(have), "%",
           [by_bank[c]["cet1"][2][0] for c in have]) if have else None
    for code in BIG5:
        figs, xs = by_bank[code], [x for x in items if code in x.banks]
        counts = Counter(t for x in xs for t in x.themes)
        out += [f"### {names[code]} ({code})", ""]
        if xs:
            themes = ", ".join(f"{THEMES[k][0]} ({n})" for k, n in counts.most_common()) or "no theme matched"
            out.append(f"{len(xs)} item(s) in the window name the bank. Themes: {themes}.")
        else:
            out.append("No item in the window names the bank.")
        out.append("")
        if not figs:
            out += ["No complete set of fiscal-year reports filed by the as-of date.", ""]
            continue
        q = ", ".join(f"Q{i + 1} {cited(*figs['ni'][i])}" for i in range(3))
        out += [
            f"- **Capital.** CET1 ratio at {figs['period_end']}: {cited(*figs['cet1'])}. Big 5 average: {cited(*avg)}.",
            f"- **Cost.** Efficiency ratio (non-interest expense / revenue) for fiscal {figs['year']} Q3 alone: "
            f"{cited(*figs['efficiency'])}.",
            f"- **Earnings.** Net income available to common shareholders, each quarter of fiscal {figs['year']} on its "
            f"own: {q}.",
        ]
        if counts:
            pairs = "; ".join(f"{THEMES[k][0]} with the {PAIRING[k][0]} line ({PAIRING[k][1]})" for k in counts)
            out.append(f"- **Set beside the news.** {pairs}.")
        out.append("")

    out += ["## Rules", "", "| Theme | Keywords (case-insensitive) |", "|---|---|"]
    out += [f"| {label} | {', '.join(f'`{p}`' for p in pats)} |" for label, pats in THEMES.values()]
    out += ["", "Banks are matched by name and alias: " + "; ".join(
        f"{c}: {', '.join(f'`{p.pattern}`' for p in bank_patterns(ds)[c])}" for c in BIG5) + ". "
        "Short upper-case names match case-sensitively as whole words.", "",
        "## Notes", "",
        "- Headlines and summaries belong to their publishers. The digest keeps only the title, link, date, feed name "
        "and the feed's own short summary of each item, never article text, and it does not open article pages.",
        "- Tagging is literal keyword matching: an item can be tagged for a passing mention, and an item that "
        "describes a risk in other words is missed. Read the headline before drawing anything from a count.",
        "- Filed figures are the XBRL of each bank's 6-K and 40-F filings on SEC EDGAR, as originally filed, and "
        "are reported, not adjusted, figures. Single quarters are year-to-date differences.", ""]
    return "\n".join(out).rstrip() + "\n"
