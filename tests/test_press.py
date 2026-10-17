from datetime import datetime, timedelta, timezone

import httpx
import respx

from conftest import fixture_bytes
from sec_sentinel.press import PRESS_RSS_URL, fetch_press_releases, parse_rss
from sec_sentinel.priority import Tier, classify_press, compile_keywords

CONFIG_KEYWORDS = compile_keywords(
    ("bitcoin", "ethereum", "ether", "solana", "XRP", "digital asset", "crypto asset",
     "stablecoin", "staking", "spot ETF")
)


def releases():
    return parse_rss(fixture_bytes("press_releases.rss"))


def test_parse_rss_fields():
    items = releases()
    assert len(items) == 25
    first = items[0]
    assert first.key == "24cedc35-40a2-45f1-a64e-40656c3ddcbf"  # guid
    assert first.title == (
        "SEC Small Business Advisory Committee to Discuss IPO Modernization and Regulation Crowdfunding"
    )
    assert first.link.startswith("https://www.sec.gov/newsroom/press-releases/2026-105-")
    assert first.published == datetime(2026, 10, 14, 10, 30, 47, tzinfo=timezone(timedelta(hours=-4)))
    assert first.summary.startswith("The Securities and Exchange Commission")  # &nbsp; decoded
    assert "&nbsp;" not in first.summary


def test_keys_are_unique():
    assert len({r.key for r in releases()}) == 25


def test_crypto_release_is_high_via_plural():
    custody = releases()[5]
    verdict = classify_press(custody, CONFIG_KEYWORDS)
    assert verdict.tier is Tier.HIGH
    assert verdict.reasons == ("keyword: crypto asset",)


def test_only_one_crypto_release_in_fixture():
    hits = [r for r in releases() if classify_press(r, CONFIG_KEYWORDS).tier is Tier.HIGH]
    assert [r.key for r in hits] == ["a6418b36-75ad-41ac-8303-10a13eb4073b"]


@respx.mock
def test_fetch_ok(sec):
    respx.get(PRESS_RSS_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("press_releases.rss")))
    items, ok = fetch_press_releases(sec)
    assert ok and len(items) == 25


@respx.mock
def test_fetch_failure_is_reported_not_raised(sec):
    respx.get(PRESS_RSS_URL).mock(return_value=httpx.Response(503))
    assert fetch_press_releases(sec) == ([], False)
