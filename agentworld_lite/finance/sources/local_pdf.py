"""Figures transcribed by a person from documents they downloaded themselves -> a company dataset.

This is the route for SEDAR+ filings: the SEDAR+ website allows a person to search and download documents,
but not automated access. Download the report by hand, write a manifest that says where each number is,
and this source checks every number against the text of the cited page before it accepts it. The documents
stay on your disk; only the manifest and the dataset (numbers plus citations) go into a repository.

Manifest (YAML):

    company: {name: Example Corp, code: EXM, currency: CAD, fiscal_year_end: "12-31", market: ca}
    documents:
      q2_2026:
        path: C:/Users/me/sedar/example-q2-2026.pdf   # PDF (needs `pip install pypdf`), .txt (pages split by
        filed: "2026-08-05"                           #   form feeds) or .htm/.html (one page)
        title: Q2 2026 interim financial statements
    statements:
      "2026:H1:CFS":
        document: q2_2026
        period_end: "2026-06-30"
        accounts:
          revenue: {value: 1234000000, page: 3, text: "1,234.0"}   # text as printed; value in units
    prices:
      "2026-06-30": {value: 41.25, document: q2_2026, page: 1, text: "41.25"}

Checks: the cited text must appear on that page (whitespace-insensitive), and `value` must equal the printed
number times a power of ten (1, 1e3, 1e6 or 1e9), so a slip in the typed value or the unit is caught.
The SHA-256 of each document is stored with the dataset so a later reader can tell which file was used.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

import yaml

from . import SourceError, parse_periods
from .edgar import ACCOUNT_RULES

SCALES = (1.0, 1e3, 1e6, 1e9)


def page_texts(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise SourceError("reading PDFs needs pypdf: pip install pypdf") from None
        return [page.extract_text() or "" for page in PdfReader(str(path)).pages]
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".htm", ".html"):
        return [html.unescape(re.sub(r"<[^>]+>", " ", text))]
    return text.split("\f")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def printed_number(text: str) -> float | None:
    s = str(text).strip().replace(",", "").replace("$", "").replace("\u2212", "-")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        return -float(s) if neg else float(s)
    except ValueError:
        return None


def check_citation(pages: list[str], cite: dict, where: str) -> None:
    page, text, value = cite.get("page"), str(cite.get("text", "")), cite.get("value")
    if not isinstance(page, int) or not 1 <= page <= len(pages):
        raise SourceError(f"{where}: page {page!r} is outside the document (1-{len(pages)})")
    if not text or _squash(text) not in _squash(pages[page - 1]):
        raise SourceError(f"{where}: {text!r} does not appear on page {page}")
    printed = printed_number(text)
    if printed is None or value is None:
        raise SourceError(f"{where}: need a numeric `text` and `value`")
    if not any(abs(float(value) - printed * k) <= 1e-9 * max(1.0, abs(float(value))) for k in SCALES):
        raise SourceError(f"{where}: value {value} is not the printed {text!r} times 1, 1e3, 1e6 or 1e9")


class LocalPdfSource:
    name = "local_pdf"

    def __init__(self, manifest: str | Path | None = None, log=print) -> None:
        self.manifest = Path(manifest) if manifest else None
        self.log = log

    def build_company(self, company: str | None = None, periods: list[str] | None = None, *,
                      prices: dict | None = None, out_dir: str | Path = "data/finance/local", **_) -> Path:
        if self.manifest is None:
            raise SourceError("local_pdf needs --manifest <file.yaml>")
        spec = yaml.safe_load(self.manifest.read_text(encoding="utf-8")) or {}
        info = spec.get("company") or {}
        if not info.get("name") or not info.get("code"):
            raise SourceError("manifest `company` needs name and code")
        wanted = {f"{y}:{p}:CFS" for y, p in parse_periods(periods)} if periods else None
        base = self.manifest.parent
        docs: dict[str, dict] = {}
        for key, d in (spec.get("documents") or {}).items():
            path = Path(d["path"]).expanduser()
            path = path if path.is_absolute() else base / path
            if not path.exists():
                raise SourceError(f"document {key}: {path} not found")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = d.get("sha256")  # YAML may read an all-digit hash as a number
            if expected not in (None, "") and str(expected).lower() != digest:
                raise SourceError(f"document {key}: file changed since the manifest was written (sha256 differs)")
            docs[key] = {"pages": page_texts(path), "filed": str(d["filed"]), "title": d.get("title", key),
                         "sha256": digest}

        data = {"name": info["name"], "stock_code": str(info["code"]), "currency": info.get("currency", "CAD"),
                "lang": info.get("lang", "en"), "market": info.get("market", "ca"),
                "fiscal_year_end": str(info.get("fiscal_year_end", "12-31")),
                "source": f"documents downloaded by hand, figures transcribed in {self.manifest.name}",
                "accounts": {k: {"label": r.label, "unit": r.unit} for k, r in ACCOUNT_RULES.items()},
                "documents": {k: {"title": v["title"], "filed": v["filed"], "sha256": v["sha256"]} for k, v in docs.items()},
                "statements": {}, "shares": {}, "prices": {}, "price_sources": {}}
        data["accounts"].update(spec.get("accounts_extra") or {})
        for key, st in (spec.get("statements") or {}).items():
            if wanted is not None and key not in wanted:
                continue
            doc = docs.get(st.get("document"))
            if doc is None:
                raise SourceError(f"statement {key}: unknown document {st.get('document')!r}")
            out = {"filed": doc["filed"], "doc_id": f"{st['document']}:{doc['sha256'][:12]}", "form": doc["title"],
                   "period_end": str(st.get("period_end", "")), "pages": {}}
            for account, cite in (st.get("accounts") or {}).items():
                if account not in data["accounts"]:
                    raise SourceError(f"statement {key}: unknown account {account!r} (add it under accounts_extra)")
                check_citation(doc["pages"], cite, f"{key} {account}")
                out[account] = float(cite["value"])
                out["pages"][account] = cite["page"]
            data["statements"][key] = out
            self.log(f"  {info['code']} {key}: {len(out['pages'])} figures checked against {doc['title']}")
        for date, cite in (spec.get("prices") or {}).items():
            doc = docs.get(cite.get("document"))
            if doc is None:
                raise SourceError(f"price {date}: unknown document {cite.get('document')!r}")
            check_citation(doc["pages"], cite, f"price {date}")
            data["prices"][str(date)] = float(cite["value"])
            data["price_sources"][str(date)] = f"{doc['title']}, page {cite['page']}"
        data["prices"].update(prices or {})
        path = Path(out_dir) / f"{info['code']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        return path
