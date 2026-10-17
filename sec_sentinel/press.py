"""SEC press releases RSS.

URL verified on 2026-10-17: listed at https://www.sec.gov/about/rss-feeds
(https://www.sec.gov/rss now redirects to an unreachable host).

Item shape (verified, see tests/fixtures/press_releases.rss):
  <item>
    <title>  SEC Proposes ...\n</title>                (padded with whitespace)
    <link>https://www.sec.gov/newsroom/press-releases/2026-104-...</link>
    <description>The&amp;nbsp;Securities ...…</description>  (HTML entities, truncated)
    <pubDate>Fri, 09 Oct 2026 10:59:00 -0400</pubDate>
    <guid isPermaLink="false">d1fd3bfa-e932-4aa7-a7ee-8ca856f3e27a</guid>
  </item>
"""

from __future__ import annotations

import html
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from .http import SecClient, SecError

log = logging.getLogger(__name__)

PRESS_RSS_URL = "https://www.sec.gov/news/pressreleases.rss"


@dataclass(frozen=True)
class PressRelease:
    key: str  # guid, else link
    title: str
    summary: str
    link: str
    published: datetime


def _clean(text: str | None) -> str:
    return " ".join(html.unescape(text or "").split())


def parse_rss(content: bytes) -> list[PressRelease]:
    releases = []
    for item in ET.fromstring(content).iterfind("./channel/item"):
        title = _clean(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        key = (item.findtext("guid") or "").strip() or link
        try:
            published = parsedate_to_datetime((item.findtext("pubDate") or "").strip())
        except (TypeError, ValueError):
            log.warning("skipping press release with bad pubDate: %r", title)
            continue
        if not key or not title:
            log.warning("skipping press release without guid/link or title")
            continue
        releases.append(PressRelease(key, title, _clean(item.findtext("description")), link, published))
    return releases


def fetch_press_releases(sec: SecClient) -> tuple[list[PressRelease], bool]:
    """Return (releases, ok). Never raises."""
    try:
        releases = parse_rss(sec.get(PRESS_RSS_URL).content)
    except (SecError, httpx.HTTPError, ET.ParseError) as exc:
        log.warning("press release feed failed: %s", exc)
        return [], False
    log.info("press releases: %d items", len(releases))
    return releases, True
