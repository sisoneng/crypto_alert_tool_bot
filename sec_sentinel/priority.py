"""Priority tiers.

HIGH:   watchlist filer AND form 8-K AND items intersect [1.01, 2.01, 7.01, 8.01]
HIGH:   form S-1 or S-1/A AND keyword match (filer name or full-text)
HIGH:   SEC press release AND keyword match (title or summary)
MEDIUM: watchlist filer, any configured form
MEDIUM: non-watchlist filer AND keyword match (filer name or full-text)
LOW:    everything else -> log only
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from .edgar import Filing
from .press import PressRelease

S1_FORMS = frozenset({"S-1", "S-1/A"})
HIGH_8K_ITEMS = frozenset({"1.01", "2.01", "7.01", "8.01"})


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
        + ((f"keyword: {', '.join(name_hits)}",) if name_hits else ())
        + ((f"full-text: {', '.join(text_hits)}",) if text_hits else ())
    )
    any_keyword = bool(name_hits or text_hits)

    if hot_items:
        return Verdict(Tier.HIGH, reasons)
    if filing.form in S1_FORMS and any_keyword:
        return Verdict(Tier.HIGH, reasons)
    if watched or any_keyword:
        return Verdict(Tier.MEDIUM, reasons)
    return Verdict(Tier.LOW, reasons)


def classify_press(release: PressRelease, patterns: list[tuple[str, re.Pattern[str]]]) -> Verdict:
    hits = keyword_hits([release.title, release.summary], patterns)
    if hits:
        return Verdict(Tier.HIGH, (f"keyword: {', '.join(hits)}",))
    return Verdict(Tier.LOW, ())
