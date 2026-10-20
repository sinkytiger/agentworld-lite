"""Filing sources: everything that turns regulatory filings into the dataset format in ../dataset.py.

Each source writes one JSON file per company with the same schema, so the research desk, the tasks and the
verifiers never need to know where a number came from beyond the provenance stored on every fact.

  dart       Korean periodic reports through DART OpenAPI (free key in DART_API_KEY)
  edgar      SEC EDGAR XBRL of each periodic filing (40-F, 6-K, 10-K, 10-Q, 20-F). Canadian issuers that are
             cross-listed in the U.S. file the same annual and interim reports they put on SEDAR+.
  local_pdf  figures a person transcribed from documents they downloaded themselves (for example from the
             SEDAR+ website), each checked against the text of the cited page before it is accepted
  sedar_dds  placeholder for the CSA Data Distribution Services bulk feed (licensed subscription)

SEDAR+ has no public API and its terms of use prohibit automated access to the public website, so there is
deliberately no SEDAR+ crawler here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SourceError(RuntimeError):
    """A source could not produce a dataset (bad input, missing credentials, network or data problem)."""


class SourceUnavailable(SourceError):
    """The source exists as an interface but cannot be used in this installation."""


class FilingSource(Protocol):
    name: str

    def build_company(self, company: str, periods: list[str], *, prices: dict | None = None,
                      out_dir: str | Path) -> Path:
        """Write <out_dir>/<code>.json for one company and return its path.

        periods are "YEAR:PERIOD" strings with PERIOD in Q1 / H1 / Q3 / FY (fiscal, year-to-date)."""
        ...


def parse_periods(periods: list[str]) -> list[tuple[int, str]]:
    out = []
    for item in periods:
        year, _, period = str(item).partition(":")
        period = period.upper()
        if not year.isdigit() or period not in ("Q1", "H1", "Q3", "FY"):
            raise SourceError(f"bad period {item!r}: use YEAR:PERIOD with PERIOD in Q1, H1, Q3, FY")
        out.append((int(year), period))
    return out


def get_source(name: str, **kw) -> FilingSource:
    if name == "dart":
        from .dart_source import DartSource
        return DartSource(**kw)
    if name == "edgar":
        from .edgar import EdgarSource
        return EdgarSource(**kw)
    if name == "local_pdf":
        from .local_pdf import LocalPdfSource
        return LocalPdfSource(**kw)
    if name == "sedar_dds":
        from .sedar_dds import SedarDdsSource
        return SedarDdsSource(**kw)
    raise SourceError(f"unknown source {name!r}; choose dart, edgar, local_pdf or sedar_dds")


SOURCES = ("dart", "edgar", "local_pdf", "sedar_dds")
