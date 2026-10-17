"""EDGAR "latest filings" Atom feed: fetch and parse.

Entry shape (verified against real responses, see tests/fixtures/atom_*.xml):
  <title>{form} - {name} ({cik:010d}) ({role})</title>
  <link href="https://www.sec.gov/Archives/edgar/data/.../{accession}-index.htm"/>
  <summary type="html"> ...<br>Item 8.01: Other Events<br>Item 9.01: ... </summary>  (8-K items)
  <updated>2026-10-16T17:30:58-04:00</updated>
  <category term="{form}"/>
  <id>urn:tag:sec.gov,2008:accession-number={accession}</id>

One filing can appear as several entries, one per entity (e.g. Form 4:
Reporting + Issuer; Schedule 13D: Filed by + Subject). Entries are grouped by
accession number.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from .http import SecClient, SecError

log = logging.getLogger(__name__)

ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
NS = {"a": "http://www.w3.org/2005/Atom"}

_TITLE_RE = re.compile(r"^(?P<form>.+?) - (?P<name>.+) \((?P<cik>\d{10})\) \((?P<role>[^()]+)\)$")
_ACCESSION_RE = re.compile(r"\d{10}-\d{2}-\d{6}")
_ITEM_RE = re.compile(r"\bItem (\d+\.\d+)")

# Lower index = better choice for the entity shown in an alert.
_ROLE_RANK = {"Issuer": 0, "Subject": 1, "Filer": 2, "Filed by": 3, "Reporting": 4}


@dataclass(frozen=True)
class Entity:
    cik: int
    name: str
    role: str
    link: str


@dataclass
class Filing:
    accession: str
    form: str
    filed_at: datetime
    entities: list[Entity] = field(default_factory=list)
    items: tuple[str, ...] = ()  # 8-K item numbers, e.g. ("8.01", "9.01")
    has_time: bool = True  # False when only the filing date is known (full-text search)

    @property
    def primary(self) -> Entity:
        return min(self.entities, key=lambda e: _ROLE_RANK.get(e.role, len(_ROLE_RANK)))

    @property
    def ciks(self) -> set[int]:
        return {e.cik for e in self.entities}

    @property
    def link(self) -> str:
        return self.primary.link

    def merge(self, other: Filing) -> None:
        for entity in other.entities:
            if entity not in self.entities:
                self.entities.append(entity)
        if not self.items:
            self.items = other.items


def extract_accession(text: str) -> str | None:
    match = _ACCESSION_RE.search(text)
    return match.group(0) if match else None


def extract_items(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_ITEM_RE.findall(text)))


def index_link(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm"


def feed_params(form: str) -> dict[str, str]:
    return {"action": "getcurrent", "type": form, "owner": "include", "count": "100", "output": "atom"}


def parse_atom(content: bytes) -> list[Filing]:
    root = ET.fromstring(content)
    filings: dict[str, Filing] = {}
    for entry in root.findall("a:entry", NS):
        title = (entry.findtext("a:title", "", NS) or "").strip()
        match = _TITLE_RE.match(title)
        accession = extract_accession(entry.findtext("a:id", "", NS) or "")
        if not match or not accession:
            log.warning("skipping unparseable Atom entry: %r", title)
            continue
        try:
            filed_at = datetime.fromisoformat((entry.findtext("a:updated", "", NS) or "").strip())
        except ValueError:
            log.warning("skipping Atom entry with bad <updated>: %r", title)
            continue
        category = entry.find("a:category", NS)
        form = category.get("term", "") if category is not None else ""
        link_el = entry.find("a:link", NS)
        link = link_el.get("href", "") if link_el is not None else ""

        items = extract_items(entry.findtext("a:summary", "", NS) or "")

        entity = Entity(int(match["cik"]), match["name"], match["role"], link)
        filing = Filing(accession, form or match["form"], filed_at, [entity], items)
        if accession in filings:
            filings[accession].merge(filing)
        else:
            filings[accession] = filing
    return list(filings.values())


def fetch_filings(sec: SecClient, forms: tuple[str, ...]) -> tuple[dict[str, Filing], list[str]]:
    """Fetch one feed per form. Returns (filings by accession, forms whose feed failed).

    The feed's `type` filter is a prefix match (S-1 also returns S-1/A and
    S-1MEF), so results are narrowed to the configured forms plus their /A
    amendments.
    """
    allowed = set(forms) | {f"{f}/A" for f in forms}
    merged: dict[str, Filing] = {}
    failed: list[str] = []
    for form in forms:
        try:
            resp = sec.get(ATOM_URL, params=feed_params(form))
            filings = parse_atom(resp.content)
        except (SecError, httpx.HTTPError, ET.ParseError) as exc:
            log.error("feed for form %s failed: %s", form, exc)
            failed.append(form)
            continue
        kept = 0
        for filing in filings:
            if filing.form not in allowed:
                continue
            kept += 1
            if filing.accession in merged:
                merged[filing.accession].merge(filing)
            else:
                merged[filing.accession] = filing
        log.info("feed %s: %d filings (%d kept)", form, len(filings), kept)
    return merged, failed
