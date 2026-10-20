"""Placeholder for the CSA Data Distribution Services (DDS) SEDAR+ feed.

DDS is the Canadian Securities Administrators' licensed bulk feed of SEDAR+ filings: structured files
delivered to a subscriber's SFTP endpoint as filings are published (NRD and SEDI data are daily). It is
the sanctioned way to collect SEDAR+ filings automatically. Subscriptions, schemas and pricing are set up
with the DDS team (https://www.securities-administrators.ca/national-systems/data-distribution-services/),
so this project cannot ship a working adapter.

The intended shape, once a subscriber has the documentation:
  1. Point DDS_DELIVERY_DIR at the directory the SFTP feed syncs to.
  2. Read filing metadata (issuer profile, filing type, filing date, document references) to pick the
     periodic filings (annual and interim financial statements, MD&A) for the requested fiscal periods.
  3. Use the filing date as `filed` for the desk's as-of guard, and the SEDAR+ filing reference as doc_id.
  4. Extract figures from the documents through the same citation checks as local_pdf, or from structured
     fields if the feed provides them, and write the usual dataset JSON.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import SourceUnavailable

DELIVERY_ENV = "DDS_DELIVERY_DIR"


class SedarDdsSource:
    name = "sedar_dds"
    available = False

    def __init__(self, delivery_dir: str | Path | None = None, **_) -> None:
        directory = delivery_dir or os.environ.get(DELIVERY_ENV)
        self.delivery_dir = Path(directory) if directory else None

    def build_company(self, company: str, periods: list[str], **_) -> Path:
        raise SourceUnavailable(
            "sedar_dds is a placeholder. SEDAR+ bulk data comes from the CSA Data Distribution Services "
            "subscription, whose schemas are shared with subscribers. Use `edgar` for cross-listed issuers, "
            "or `local_pdf` with documents you downloaded from sedarplus.ca yourself.")
