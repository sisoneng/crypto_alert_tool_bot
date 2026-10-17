"""Federal Register API: SEC documents, including exchange 19b-4 rule filings
(crypto ETF listing notices, proceedings, approvals and disapprovals).

Not an SEC endpoint: federalregister.gov is the official US government journal,
linked from https://www.sec.gov/about/rss-feeds. Its RSS feed redirects
automated clients to a CAPTCHA page; the JSON API below is meant for programs.

Behavior verified against real responses (see tests/fixtures/fedreg_*.json):
- `conditions[term]` searches the full document text; `"a" | "b"` is OR and
  quotes make phrases, so one request covers every keyword.
- Crypto ETF titles often lack a keyword ("Canary PENGU ETF"), which is why the
  body search is used rather than matching titles locally.
- With no matches the response has no "results" key at all.
- `excerpts` is an HTML snippet of the body and may not show the matched word.
- Only a publication date is given; documents can be listed a day early.

Result shape:
  {"count": 3, "results": [{"title": "Self-Regulatory Organizations; ...", "type": "Notice",
    "abstract": null, "document_number": "2026-21128", "publication_date": "2026-10-16",
    "html_url": "https://www.federalregister.gov/documents/...", "excerpts": "...<span class=\\"match\\">..."}]}
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from .http import SecClient, SecError

log = logging.getLogger(__name__)

FEDREG_URL = "https://www.federalregister.gov/api/v1/documents.json"
SEC_AGENCY = "securities-and-exchange-commission"
WINDOW_DAYS = 3
FIELDS = ("title", "type", "abstract", "document_number", "html_url", "publication_date", "excerpts")
EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class FedRegDoc:
    number: str
    title: str
    doc_type: str  # "Notice", "Rule", "Proposed Rule"
    abstract: str
    excerpts: str  # plain text
    link: str
    published: datetime  # midnight ET of the publication date


def _plain(text: str | None) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def search_params(keywords: tuple[str, ...], start: date) -> list[tuple[str, str]]:
    return [
        ("conditions[agencies][]", SEC_AGENCY),
        ("conditions[term]", " | ".join(f'"{kw}"' for kw in keywords)),
        ("conditions[publication_date][gte]", start.isoformat()),
        ("order", "newest"),
        ("per_page", "100"),
        *(("fields[]", f) for f in FIELDS),
    ]


def parse_documents(data: dict) -> list[FedRegDoc]:
    docs = []
    for row in data.get("results") or []:
        try:
            published = datetime.combine(date.fromisoformat(row["publication_date"]), time(0), EASTERN)
            docs.append(
                FedRegDoc(
                    number=row["document_number"],
                    title=_plain(row["title"]),
                    doc_type=row.get("type") or "Document",
                    abstract=_plain(row.get("abstract")),
                    excerpts=_plain(row.get("excerpts")),
                    link=row["html_url"],
                    published=published,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("skipping Federal Register row: %s", exc)
    return docs


def fetch_fedreg(sec: SecClient, keywords: tuple[str, ...], today: date) -> tuple[list[FedRegDoc], bool]:
    """Return (documents, ok). Never raises."""
    try:
        data = sec.get(FEDREG_URL, params=search_params(keywords, today - timedelta(days=WINDOW_DAYS))).json()
        docs = parse_documents(data)
        total = int(data.get("count", 0))
    except (SecError, httpx.HTTPError, ValueError, TypeError) as exc:
        log.warning("Federal Register search failed: %s", exc)
        return [], False
    if total > len(docs):
        log.warning("Federal Register: %d matches, only the first page was read", total)
    log.info("Federal Register: %d SEC documents matched", len(docs))
    return docs, True
