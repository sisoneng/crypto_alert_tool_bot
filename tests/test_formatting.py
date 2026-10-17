from datetime import datetime, timedelta, timezone

from conftest import fixture_bytes
from sec_sentinel.edgar import Entity, Filing
from sec_sentinel.formatting import filing_alert, format_alert, format_digest, format_time, press_alert
from sec_sentinel.press import parse_rss
from sec_sentinel.priority import Tier, Verdict

ZONES = ("America/New_York", "Asia/Manila")
ET = timezone(timedelta(hours=-4))
LINK = "https://www.sec.gov/Archives/edgar/data/1050446/000105044626000123/0001050446-26-000123-index.htm"


def make_filing(name="Strategy Inc", items=(), has_time=True):
    return Filing(
        "0001050446-26-000123",
        "8-K",
        datetime(2026, 10, 17, 8, 31, tzinfo=ET),
        [Entity(1050446, name, "Filer", LINK)],
        items,
        has_time,
    )


def make_alert(name="Strategy Inc", label="MSTR", tier=Tier.HIGH, items=(), has_time=True):
    return filing_alert(make_filing(name, items, has_time), Verdict(tier, ("watchlist",)), label)


def test_format_alert():
    assert format_alert(make_alert(), ZONES) == (
        "🔴 HIGH | MSTR — 8-K\n"
        "Strategy Inc\n"
        "Filed: 2026-10-17 08:31 ET / 20:31 PHT\n"
        "Matched: watchlist\n"
        f"🔗 {LINK}"
    )


def test_medium_emoji():
    assert format_alert(make_alert(tier=Tier.MEDIUM), ZONES).startswith("🟡 MEDIUM | MSTR")


def test_html_is_escaped():
    text = format_alert(make_alert(name="AT&T <Digital> Trust", label="A&B"), ZONES)
    assert "AT&amp;T &lt;Digital&gt; Trust" in text
    assert "| A&amp;B —" in text
    assert "<Digital>" not in text


def test_format_time_shows_date_when_it_differs():
    dt = datetime(2026, 10, 17, 14, 5, tzinfo=ET)  # 02:05 next day in Manila
    assert format_time(dt, ZONES) == "2026-10-17 14:05 ET / 2026-10-18 02:05 PHT"


def test_format_time_winter_offset():
    dt = datetime(2026, 12, 1, 13, 0, tzinfo=timezone.utc)
    assert format_time(dt, ZONES) == "2026-12-01 08:00 ET / 21:00 PHT"


def test_digest_lists_alerts():
    alerts = [make_alert(label=f"T{i}", tier=Tier.MEDIUM) for i in range(3)]
    lines = format_digest(alerts).split("\n")
    assert lines[0] == "📦 Digest: 3 more alerts this run"
    assert lines[1] == f"🟡 T0 — 8-K — {LINK}"
    assert len(lines) == 4


def test_digest_truncates_to_telegram_limit():
    alerts = [make_alert(label=f"T{i}", tier=Tier.MEDIUM) for i in range(200)]
    text = format_digest(alerts)
    assert len(text) <= 4096
    shown = len(text.split("\n")) - 2
    assert text.endswith(f"…and {200 - shown} more")


def test_header_shows_items():
    assert format_alert(make_alert(items=("8.01", "9.01")), ZONES).startswith(
        "🔴 HIGH | MSTR — 8-K (Items 8.01, 9.01)\n"
    )
    assert format_alert(make_alert(items=("7.01",)), ZONES).startswith("🔴 HIGH | MSTR — 8-K (Item 7.01)\n")


def test_date_only_filing():
    assert "\nFiled: 2026-10-17\n" in format_alert(make_alert(has_time=False), ZONES)


def test_press_release_alert():
    release = parse_rss(fixture_bytes("press_releases.rss"))[5]
    text = format_alert(press_alert(release, Verdict(Tier.HIGH, ("keyword: crypto asset",))), ZONES)
    assert text == (
        "🔴 HIGH | SEC — Press release\n"
        "SEC Proposal Would Address How Investment Advisers and Funds Can Custody Crypto Assets "
        "Under the Federal Securities Laws\n"
        "Published: 2026-10-01 12:16 ET / 2026-10-02 00:16 PHT\n"
        "Matched: keyword: crypto asset\n"
        f"🔗 {release.link}"
    )


def test_federal_register_alert():
    import json

    from sec_sentinel.fedreg import parse_documents
    from sec_sentinel.formatting import fedreg_alert

    docs = {d.number: d for d in parse_documents(json.loads(fixture_bytes("fedreg_crypto.json")))}
    doc = docs["2025-18258"]
    text = format_alert(fedreg_alert(doc, Verdict(Tier.HIGH, ("listing decision", "full-text: bitcoin"))), ZONES)
    lines = text.split("\n")
    assert lines[0] == "🔴 HIGH | SEC — Federal Register notice"
    assert lines[1].endswith("To Adopt Generic Listing Standards for Commodity-Based Trust Shares")
    assert lines[2] == "Published: 2025-09-22"
    assert lines[3] == "Matched: listing decision, full-text: bitcoin"
    assert lines[4] == f"🔗 {doc.link}"
