"""EDGAR full-text search: secondary keyword detector over filing text.

Behavior verified against real responses (see tests/fixtures/fulltext_*.json):
- `forms=8-K,S-1` filters on root form, so amendments (8-K/A, S-1/A) are included.
  Passing an amendment form ("S-1/A") adds a second filter that is AND-ed with
  the first, so only base forms may be sent.
- Unquoted multi-word queries match any word; every keyword is sent quoted.
- Hits do not say which keyword matched, so there is one query per keyword.
- One hit per document (main doc and each exhibit); hits are grouped by `adsh`.
- Only the filing date is available, not the acceptance time.
- Up to 100 hits per page.

Hit shape:
  {"_source": {"adsh": "0001193125-26-423400", "form": "8-K", "file_date": "2026-10-16",
               "ciks": ["0001050446"], "items": ["7.01", "8.01"],
               "display_names": ["Strategy Inc  (MSTR, STRC)  (CIK 0001050446)"]}}
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from .edgar import Entity, Filing, index_link
from .http import SecClient, SecError

log = logging.getLogger(__name__)

FULLTEXT_URL = "https://efts.sec.gov/LATEST/search-index"
WINDOW_DAYS = 3  # re-query recent days to absorb indexing lag
EASTERN = ZoneInfo("America/New_York")

# "Strategy Inc  (MSTR, STRC)  (CIK 0001050446)" or "CARMAX AUTO FUNDING LLC  (CIK 0001259380)"
_DISPLAY_RE = re.compile(r"^(?P<name>.+?)\s+(?:\([^()]*\)\s+)?\(CIK (?P<cik>\d{10})\)$")


def search_params(keyword: str, forms: tuple[str, ...], start: date, end: date) -> dict[str, str]:
    return {
        "q": f'"{keyword}"',
        "forms": ",".join(forms),
        "dateRange": "custom",
        "startdt": start.isoformat(),
        "enddt": end.isoformat(),
    }


def parse_hits(data: dict) -> list[Filing]:
    filings: dict[str, Filing] = {}
    for hit in data["hits"]["hits"]:
        src = hit["_source"]
        accession = src["adsh"]
        entities = []
        for display in src.get("display_names") or []:
            match = _DISPLAY_RE.match(display.strip())
            if match:
                cik = int(match["cik"])
                entities.append(Entity(cik, match["name"], "Filer", index_link(cik, accession)))
        if not entities:
            log.warning("skipping full-text hit without parseable filer: %s", accession)
            continue
        filed_on = date.fromisoformat(src["file_date"])
        filing = Filing(
            accession,
            src["form"],
            datetime.combine(filed_on, time(0), EASTERN),
            entities,
            tuple(src.get("items") or ()),
            has_time=False,
        )
        if accession in filings:
            filings[accession].merge(filing)
        else:
            filings[accession] = filing
    return list(filings.values())


def fetch_fulltext(
    sec: SecClient, keywords: tuple[str, ...], forms: tuple[str, ...], today: date
) -> tuple[dict[str, tuple[Filing, list[str]]], bool]:
    """Return ({accession: (filing, matched keywords)}, all_queries_ok)."""
    allowed = set(forms) | {f"{f}/A" for f in forms}
    start = today - timedelta(days=WINDOW_DAYS)
    results: dict[str, tuple[Filing, list[str]]] = {}
    ok = True
    for keyword in keywords:
        try:
            data = sec.get(FULLTEXT_URL, params=search_params(keyword, forms, start, today)).json()
            filings = parse_hits(data)
            total = data["hits"]["total"]["value"]
        except (SecError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.warning("full-text search for %r failed: %s", keyword, exc)
            ok = False
            continue
        if total > len(data["hits"]["hits"]):
            log.warning("full-text %r: %d hits, only the first page was read", keyword, total)
        for filing in filings:
            if filing.form not in allowed:
                continue
            if filing.accession in results:
                results[filing.accession][1].append(keyword)
            else:
                results[filing.accession] = (filing, [keyword])
    log.info("full-text: %d filings matched across %d keywords", len(results), len(keywords))
    return results, ok
