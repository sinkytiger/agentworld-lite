"""The DART OpenAPI client (../dart.py) behind the FilingSource interface."""

from __future__ import annotations

from pathlib import Path

from . import SourceError


class DartSource:
    name = "dart"

    def __init__(self, cache_dir: str | Path = "data/finance/cache", bases=("CFS", "OFS"), log=print) -> None:
        from ..dart import DartClient, DartError
        try:
            self.client = DartClient(cache_dir=cache_dir)
        except DartError as exc:
            raise SourceError(str(exc)) from None
        self.bases = tuple(bases)
        self.log = log

    def build_company(self, company: str, periods: list[str], *, prices: dict | None = None,
                      out_dir: str | Path = "data/finance/dart", **_) -> Path:
        from ..dart import DartError, fetch_company
        try:
            return fetch_company(self.client, company, periods, bases=self.bases, prices=prices, out_dir=out_dir,
                                 log=self.log)
        except DartError as exc:
            raise SourceError(str(exc)) from None
