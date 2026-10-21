"""The DART OpenAPI client (../dart.py) behind the FilingSource interface."""

from __future__ import annotations

from pathlib import Path

from . import SourceError


class DartSource:
    name = "dart"

    def __init__(self, cache_dir: str | Path = "data/finance/cache", bases=("CFS", "OFS"), profile: str = "default",
                 log=print) -> None:
        from ..dart import PROFILES, DartClient, DartError
        if profile not in PROFILES:
            raise SourceError(f"unknown account profile {profile!r}; choose {', '.join(PROFILES)}")
        try:
            self.client = DartClient(cache_dir=cache_dir)
        except DartError as exc:
            raise SourceError(str(exc)) from None
        self.bases = tuple(bases)
        self.profile = profile
        self.log = log

    def build_company(self, company: str, periods: list[str], *, prices: dict | None = None,
                      price_sources: dict | None = None, out_dir: str | Path = "data/finance/dart",
                      aliases: list[str] | None = None, **_) -> Path:
        from ..dart import DartError, fetch_company
        try:
            return fetch_company(self.client, company, periods, bases=self.bases, prices=prices, out_dir=out_dir,
                                 log=self.log, profile=self.profile, price_sources=price_sources, aliases=aliases,
                                 first_filings=True)
        except DartError as exc:
            raise SourceError(str(exc)) from None
