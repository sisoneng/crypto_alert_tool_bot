"""Priority tiers.

HIGH:   watchlist filer AND form 8-K AND items intersect [1.01, 2.01, 7.01, 8.01]
HIGH:   form S-1, S-1/A or 8-A12B AND keyword match (filer name or full-text)
HIGH:   SEC press release AND keyword match (title or summary)
HIGH:   Federal Register order approving/disapproving an exchange listing (19b-4)
MEDIUM: watchlist filer, any configured form
MEDIUM: non-watchlist filer AND keyword match (filer name or full-text)
MEDIUM: any other Federal Register document matching a keyword
LOW:    everything else -> log only
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from .edgar import Filing
from .fedreg import FedRegDoc
from .press import PressRelease

# New fund registrations (S-1) and exchange registration right before trading starts (8-A12B).
KEYWORD_HIGH_FORMS = frozenset({"S-1", "S-1/A", "8-A12B"})
HIGH_8K_ITEMS = frozenset({"1.01", "2.01", "7.01", "8.01"})

# A decision, not "Proceedings To Determine Whether To Approve or Disapprove".
_DECISION_RE = re.compile(r"Granting (?:Accelerated )?Approval|\bApproving\b|\bDisapproving\b")
_LISTING_RE = re.compile(r"List(?:ing)? and Trad|Listing Standards", re.IGNORECASE)


class Tier(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2


@dataclass(frozen=True)
class Verdict:
    tier: Tier
    reasons: tuple[str, ...]


def compile_keywords(keywords: tuple[str, ...]) -> list[tuple[str, re.Pattern[str]]]:
    # Whole-word, case-insensitive, optional plural "s": "crypto asset" matches
    # "Crypto Assets", but "ether" must not match "Aether Holdings".
    return [
        (kw, re.compile(rf"(?<![A-Za-z0-9]){re.escape(kw)}s?(?![A-Za-z0-9])", re.IGNORECASE))
        for kw in keywords
    ]


def keyword_hits(texts: list[str], patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    return [kw for kw, pattern in patterns if any(pattern.search(t) for t in texts)]


def _keyword_reasons(name_hits: list[str], text_hits: list[str]) -> tuple[str, ...]:
    return ((f"keyword: {', '.join(name_hits)}",) if name_hits else ()) + (
        (f"full-text: {', '.join(text_hits)}",) if text_hits else ()
    )


def classify(
    filing: Filing,
    watch_ciks: set[int] | dict[int, str],
    patterns: list[tuple[str, re.Pattern[str]]],
    fulltext_hits: list[str] | tuple[str, ...] = (),
) -> Verdict:
    name_hits = keyword_hits([e.name for e in filing.entities], patterns)
    text_hits = [kw for kw in fulltext_hits if kw not in name_hits]
    watched = bool(filing.ciks & set(watch_ciks))
    hot_items = [i for i in filing.items if i in HIGH_8K_ITEMS] if watched and filing.form == "8-K" else []
    reasons = (
        (("watchlist",) if watched else ())
        + tuple(f"item {i}" for i in hot_items)
        + _keyword_reasons(name_hits, text_hits)
    )
    any_keyword = bool(name_hits or text_hits)

    if hot_items:
        return Verdict(Tier.HIGH, reasons)
    if filing.form in KEYWORD_HIGH_FORMS and any_keyword:
        return Verdict(Tier.HIGH, reasons)
    if watched or any_keyword:
        return Verdict(Tier.MEDIUM, reasons)
    return Verdict(Tier.LOW, reasons)


def classify_press(release: PressRelease, patterns: list[tuple[str, re.Pattern[str]]]) -> Verdict:
    hits = keyword_hits([release.title, release.summary], patterns)
    if hits:
        return Verdict(Tier.HIGH, (f"keyword: {', '.join(hits)}",))
    return Verdict(Tier.LOW, ())


def classify_fedreg(doc: FedRegDoc, patterns: list[tuple[str, re.Pattern[str]]]) -> Verdict:
    """Every document the search returned already mentions a keyword somewhere in its text."""
    title_hits = keyword_hits([doc.title, doc.abstract], patterns)
    text_hits = [kw for kw in keyword_hits([doc.excerpts], patterns) if kw not in title_hits]
    reasons = _keyword_reasons(title_hits, text_hits) or ("full-text match",)
    if _DECISION_RE.search(doc.title) and _LISTING_RE.search(doc.title):
        return Verdict(Tier.HIGH, ("listing decision",) + reasons)
    return Verdict(Tier.MEDIUM, reasons)
