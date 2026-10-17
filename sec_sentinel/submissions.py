"""Company submissions JSON: fallback source for 8-K item numbers.

The Atom feed summary normally lists 8-K items already; this is only used for a
watchlist 8-K whose feed entry has none.

Shape (verified, see tests/fixtures/submissions_mstr.json):
  {"filings": {"recent": {"accessionNumber": [...], "form": [...], "items": ["7.01,8.01", ...], ...}}}
"""

from __future__ import annotations

import logging

import httpx

from .http import SecClient, SecError

log = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def fetch_items(sec: SecClient, cik: int, accession: str) -> tuple[str, ...]:
    """Return the 8-K items for one filing, or () if unavailable. Never raises."""
    try:
        recent = sec.get(SUBMISSIONS_URL.format(cik=cik)).json()["filings"]["recent"]
        idx = recent["accessionNumber"].index(accession)
        raw = recent["items"][idx] or ""
    except ValueError:
        log.info("submissions for CIK %d do not list %s yet", cik, accession)
        return ()
    except (SecError, httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
        log.warning("submissions lookup for CIK %d failed: %s", cik, exc)
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())
