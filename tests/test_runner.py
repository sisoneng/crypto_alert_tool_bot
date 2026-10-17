import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from conftest import RecordingSender, fixture_bytes
from sec_sentinel.config import parse_config
from sec_sentinel.edgar import ATOM_URL
from sec_sentinel.fedreg import FEDREG_URL
from sec_sentinel.fulltext import FULLTEXT_URL
from sec_sentinel.press import PRESS_RSS_URL
from sec_sentinel.runner import run_once
from sec_sentinel.tickers import TICKERS_URL

NOW = datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc)
NRUCFC = "0001104659-26-116660"  # newest 8-K entry, absent from atom_8k_earlier.xml
LQR = "0001213900-26-110367"  # second-newest, also absent


def make_config(**overrides):
    data = {
        "timezone_display": ["America/New_York", "Asia/Manila"],
        "forms": ["8-K", "4"],
        "watchlist_tickers": ["MSTR"],
        "watchlist_ciks": [1843165],  # LQR House
        "keywords": ["rural"],  # matches NATIONAL RURAL UTILITIES ...
        "max_alerts_per_run": 15,
    }
    data.update(overrides)
    return parse_config(data)


@pytest.fixture
def feeds():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(TICKERS_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("company_tickers.json")))
        eight_k = mock.get(ATOM_URL, params={"type": "8-K"})
        eight_k.mock(return_value=httpx.Response(200, content=fixture_bytes("atom_8k_earlier.xml")))
        form4 = mock.get(ATOM_URL, params={"type": "4"})
        form4.mock(return_value=httpx.Response(200, content=fixture_bytes("atom_4.xml")))
        bitcoin = mock.get(FULLTEXT_URL, params={"q": '"bitcoin"'})
        bitcoin.mock(return_value=httpx.Response(200, content=fixture_bytes("fulltext_empty.json")))
        mock.get(FULLTEXT_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("fulltext_empty.json")))
        press = mock.get(PRESS_RSS_URL)
        press.mock(return_value=httpx.Response(200, content=fixture_bytes("press_releases_earlier.rss")))
        fedreg = mock.get(FEDREG_URL)
        fedreg.mock(return_value=httpx.Response(200, content=fixture_bytes("fedreg_empty.json")))
        yield {"8-K": eight_k, "4": form4, "fts:bitcoin": bitcoin, "press": press, "fedreg": fedreg}


def run(tmp_path, sec, sender, config=None, now=NOW):
    return run_once(
        config or make_config(),
        sec=sec,
        sender=sender,
        state_path=tmp_path / "state" / "seen.json",
        tickers_cache_path=tmp_path / ".cache" / "company_tickers.json",
        now=now,
    )


def seen(tmp_path):
    return json.loads((tmp_path / "state" / "seen.json").read_text())["seen"]


def use_full_8k_feed(feeds):
    feeds["8-K"].mock(return_value=httpx.Response(200, content=fixture_bytes("atom_8k.xml")))


def test_first_run_seeds_and_sends_only_armed(tmp_path, sec, feeds):
    sender = RecordingSender()
    result = run(tmp_path, sec, sender)
    assert result.armed
    assert len(sender.messages) == 1
    assert sender.messages[0].startswith("🛰️ sec-sentinel armed — seeded 12 current filings")
    assert sender.messages[0] == (
        "🛰️ sec-sentinel armed — seeded 12 current filings across 2 forms and 19 press releases. "
        "Alerts start next run."
    )
    keys = seen(tmp_path)
    assert len([k for k in keys if ":" not in k]) == 12  # 6 8-Ks + 6 Form 4s
    assert len([k for k in keys if k.startswith("pr:")]) == 19


def test_dedupe_across_runs(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())

    use_full_8k_feed(feeds)
    sender = RecordingSender()
    result = run(tmp_path, sec, sender)
    assert (result.new_filings, result.alerts) == (2, 2)
    # LQR House trades as YHC (label from company_tickers.json); a watchlist 8-K with
    # items 1.01 and 7.01 is HIGH, so it is sent first.
    assert sender.messages[0].startswith(
        "🔴 HIGH | YHC — 8-K (Items 1.01, 5.02, 7.01, 9.01)\nLQR House Inc.\n"
    )
    assert "Matched: watchlist, item 1.01, item 7.01" in sender.messages[0]
    assert sender.messages[1].startswith(
        "🟡 MEDIUM | NATIONAL RURAL UTILITIES COOPERATIVE FINANCE CORP /DC/ — 8-K (Items 8.01, 9.01)"
    )
    assert "Matched: keyword: rural" in sender.messages[1]
    assert {NRUCFC, LQR} <= set(seen(tmp_path))

    sender = RecordingSender()
    result = run(tmp_path, sec, sender)
    assert (result.new_filings, result.alerts, sender.messages) == (0, 0, [])


def test_low_tier_is_logged_not_sent(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())
    use_full_8k_feed(feeds)
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, make_config(watchlist_ciks=[], keywords=[]))
    assert (result.new_filings, result.alerts, sender.messages) == (2, 0, [])
    assert {NRUCFC, LQR} <= set(seen(tmp_path))


def test_overflow_goes_to_digest(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())
    use_full_8k_feed(feeds)
    sender = RecordingSender()
    run(tmp_path, sec, sender, make_config(max_alerts_per_run=1))
    assert len(sender.messages) == 2
    assert sender.messages[0].startswith("🔴 HIGH | YHC — 8-K")
    assert sender.messages[1].startswith("📦 Digest: 1 more alerts this run\n🟡 NATIONAL RURAL")
    assert {NRUCFC, LQR} <= set(seen(tmp_path))


def test_failed_send_leaves_alert_unseen(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())
    use_full_8k_feed(feeds)
    with pytest.raises(RuntimeError):
        run(tmp_path, sec, RecordingSender(fail_on=2))
    assert LQR in seen(tmp_path)
    assert NRUCFC not in seen(tmp_path)

    sender = RecordingSender()
    run(tmp_path, sec, sender)
    assert len(sender.messages) == 1
    assert "NATIONAL RURAL" in sender.messages[0]


def test_heartbeat_once_per_utc_day(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())
    use_full_8k_feed(feeds)
    run(tmp_path, sec, RecordingSender())  # same day: 2 scanned, 2 alerts

    next_day = datetime(2026, 10, 18, 0, 3, tzinfo=timezone.utc)
    sender = RecordingSender()
    run(tmp_path, sec, sender, now=next_day)
    assert sender.messages == ["✅ alive — 2 filings scanned in 24h, 2 alerts"]

    sender = RecordingSender()
    run(tmp_path, sec, sender, now=next_day.replace(minute=8))
    assert sender.messages == []


def test_first_run_refuses_to_arm_with_failed_feed(tmp_path, sec, feeds):
    feeds["4"].mock(return_value=httpx.Response(503))
    with pytest.raises(RuntimeError, match="not arming"):
        run(tmp_path, sec, RecordingSender())
    assert not (tmp_path / "state" / "seen.json").exists()


def test_partial_feed_failure_still_alerts(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())
    use_full_8k_feed(feeds)
    feeds["4"].mock(return_value=httpx.Response(503))
    sender = RecordingSender()
    result = run(tmp_path, sec, sender)
    assert result.failed_forms == ("4",)
    assert len(sender.messages) == 2


# --- full-text search (Phase 3) ---

FARMHOUSE = "0001096906-26-001568"  # in atom_8k_farmhouse.xml and in the "bitcoin" full-text results
MSTR_8K = "0001193125-26-423400"


def fulltext_config(**overrides):
    data = {"keywords": ["bitcoin", "spot ETF"], "watchlist_ciks": [], "fulltext_forms": ["8-K", "S-1"]}
    data.update(overrides)
    return make_config(**data)


def set_fulltext(feeds, fixture_name):
    feeds["fts:bitcoin"].mock(return_value=httpx.Response(200, content=fixture_bytes(fixture_name)))


def use_farmhouse_feed(feeds):
    feeds["8-K"].mock(return_value=httpx.Response(200, content=fixture_bytes("atom_8k_farmhouse.xml")))


def alerts_for(messages, needle):
    return [m for m in messages if needle in m]


def test_first_run_seeds_fulltext_too(tmp_path, sec, feeds):
    set_fulltext(feeds, "fulltext_bitcoin.json")
    sender = RecordingSender()
    run(tmp_path, sec, sender, fulltext_config())
    assert len(sender.messages) == 1
    assert "across 2 forms, 10 full-text matches and 19 press releases" in sender.messages[0]

    sender = RecordingSender()
    run(tmp_path, sec, sender, fulltext_config())
    assert sender.messages == []


def test_fulltext_alerts_and_merges_with_feed(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender(), fulltext_config())  # arm with no full-text matches

    set_fulltext(feeds, "fulltext_bitcoin.json")
    use_farmhouse_feed(feeds)
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, fulltext_config())
    assert result.alerts == 10  # 10 full-text filings; FARMHOUSE also new in the feed -> one alert

    farmhouse = alerts_for(sender.messages, "FARMHOUSE")
    assert len(farmhouse) == 1
    assert "Filed: 2026-10-16 17:15 ET" in farmhouse[0]  # acceptance time from the feed copy
    assert "Matched: full-text: bitcoin" in farmhouse[0]

    mstr = alerts_for(sender.messages, "Strategy Inc")
    assert mstr[0].startswith("🔴 HIGH | MSTR — 8-K (Items 7.01, 8.01)\n")
    assert "Matched: watchlist, item 7.01, item 8.01, full-text: bitcoin" in mstr[0]
    assert "\nFiled: 2026-10-16\n" in mstr[0]  # full-text gives the date only

    ipower = alerts_for(sender.messages, "iPower")
    assert ipower[0].startswith("🔴 HIGH | iPower Inc. — S-1\n")  # S-1 + keyword; not in ticker fixture

    sender = RecordingSender()
    run(tmp_path, sec, sender, fulltext_config())
    assert sender.messages == []


def test_feed_alert_suppresses_later_fulltext_alert(tmp_path, sec, feeds):
    config = fulltext_config(watchlist_ciks=[1811999])  # FARMHOUSE
    run(tmp_path, sec, RecordingSender(), config)
    use_farmhouse_feed(feeds)
    sender = RecordingSender()
    run(tmp_path, sec, sender, config)
    assert len(sender.messages) == 1 and "FARMHOUSE" in sender.messages[0]

    set_fulltext(feeds, "fulltext_bitcoin.json")
    sender = RecordingSender()
    run(tmp_path, sec, sender, config)
    assert len(sender.messages) == 9
    assert alerts_for(sender.messages, "FARMHOUSE") == []


def test_fulltext_alert_suppresses_later_feed_alert(tmp_path, sec, feeds):
    config = fulltext_config(watchlist_ciks=[1811999])
    run(tmp_path, sec, RecordingSender(), config)
    set_fulltext(feeds, "fulltext_bitcoin.json")
    sender = RecordingSender()
    run(tmp_path, sec, sender, config)
    assert len(alerts_for(sender.messages, "FARMHOUSE")) == 1

    use_farmhouse_feed(feeds)
    sender = RecordingSender()
    run(tmp_path, sec, sender, config)
    assert sender.messages == []
    assert FARMHOUSE in seen(tmp_path)


def test_existing_v1_state_arms_fulltext_silently(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())  # Phase 1 style config, no full-text
    path = tmp_path / "state" / "seen.json"
    doc = json.loads(path.read_text())
    path.write_text(json.dumps({"version": 1, "seen": doc["seen"], "heartbeat": doc["heartbeat"]}))

    set_fulltext(feeds, "fulltext_bitcoin.json")
    use_full_8k_feed(feeds)
    sender = RecordingSender()
    run(tmp_path, sec, sender, fulltext_config(keywords=["bitcoin", "rural"]))
    # Press releases re-arm too, but their keys are already in `seen`, so the zero count is omitted.
    assert sender.messages[0] == "🛰️ sec-sentinel armed new sources — seeded 10 full-text matches."
    assert len(sender.messages) == 2  # plus the new feed filing matching "rural"
    assert "NATIONAL RURAL" in sender.messages[1]


# --- config scope changes ---


def test_adding_keywords_seeds_only_their_matches(tmp_path, sec, feeds):
    set_fulltext(feeds, "fulltext_bitcoin.json")
    run(tmp_path, sec, RecordingSender(), fulltext_config(keywords=["spot ETF"]))  # "bitcoin" not watched yet

    sender = RecordingSender()
    run(tmp_path, sec, sender, fulltext_config(keywords=["spot ETF", "bitcoin"]))
    assert sender.messages == [
        "🛰️ sec-sentinel config changed — marked 10 full-text matches newly covered as seen."
    ]
    sender = RecordingSender()
    run(tmp_path, sec, sender, fulltext_config(keywords=["spot ETF", "bitcoin"]))
    assert sender.messages == []


def test_adding_forms_seeds_only_those_forms(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender(), make_config(forms=["8-K"]))  # Form 4 not watched yet

    use_full_8k_feed(feeds)  # 2 genuinely new 8-Ks arrive in the same run
    sender = RecordingSender()
    run(tmp_path, sec, sender, make_config(forms=["8-K", "4"]))
    assert sender.messages[0] == "🛰️ sec-sentinel config changed — marked 6 filings newly covered as seen."
    assert len(sender.messages) == 3  # the 2 new 8-Ks in the old scope still alert
    assert "LQR House" in "".join(sender.messages)


def test_old_state_without_scopes_does_not_swallow_new_filings(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())
    path = tmp_path / "state" / "seen.json"
    doc = json.loads(path.read_text())
    del doc["scopes"]
    path.write_text(json.dumps(doc))

    use_full_8k_feed(feeds)
    sender = RecordingSender()
    run(tmp_path, sec, sender)
    assert len(sender.messages) == 2  # LQR + NATIONAL RURAL, no "config changed" message


def test_new_scope_not_applied_while_feed_fails(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender(), make_config(forms=["8-K"]))
    feeds["4"].mock(return_value=httpx.Response(503))
    with pytest.raises(RuntimeError, match="not seeding new forms"):
        run(tmp_path, sec, RecordingSender(), make_config(forms=["8-K", "4"]))
    feeds["4"].mock(return_value=httpx.Response(200, content=fixture_bytes("atom_4.xml")))
    sender = RecordingSender()
    run(tmp_path, sec, sender, make_config(forms=["8-K", "4"]))
    assert sender.messages == ["🛰️ sec-sentinel config changed — marked 6 filings newly covered as seen."]


def test_fulltext_failure_is_best_effort(tmp_path, sec, feeds):
    feeds["fts:bitcoin"].mock(return_value=httpx.Response(503))
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, fulltext_config())
    assert result.armed and not result.fulltext_ok
    assert "full-text" not in sender.messages[0]  # feed armed, full-text not yet

    set_fulltext(feeds, "fulltext_bitcoin.json")
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, fulltext_config())
    assert result.failed_forms == ()
    assert sender.messages == ["🛰️ sec-sentinel armed new sources — seeded 10 full-text matches."]


# --- SEC press releases (Phase 4) ---

CUSTODY_RELEASE = "a6418b36-75ad-41ac-8303-10a13eb4073b"  # "...Custody Crypto Assets..." (item 5)


def use_full_press_feed(feeds):
    feeds["press"].mock(return_value=httpx.Response(200, content=fixture_bytes("press_releases.rss")))


def press_config():
    return make_config(keywords=["crypto asset"], watchlist_ciks=[])


def test_new_crypto_press_release_alerts_high(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender(), press_config())
    use_full_press_feed(feeds)  # 6 new releases, one about crypto assets
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, press_config())
    assert result.alerts == 1
    assert len(sender.messages) == 1
    assert sender.messages[0].startswith("🔴 HIGH | SEC — Press release\nSEC Proposal Would Address")
    assert "Published: 2026-10-01 12:16 ET / 2026-10-02 00:16 PHT" in sender.messages[0]
    assert "Matched: keyword: crypto asset" in sender.messages[0]
    keys = seen(tmp_path)
    assert f"pr:{CUSTODY_RELEASE}" in keys
    assert len([k for k in keys if k.startswith("pr:")]) == 25  # non-crypto ones logged and marked

    sender = RecordingSender()
    run(tmp_path, sec, sender, press_config())
    assert sender.messages == []


def test_press_release_sorted_with_filing_alerts(tmp_path, sec, feeds):
    config = make_config(keywords=["crypto asset", "rural"])
    run(tmp_path, sec, RecordingSender(), config)
    use_full_press_feed(feeds)
    use_full_8k_feed(feeds)
    sender = RecordingSender()
    run(tmp_path, sec, sender, config)
    headers = [m.split("\n")[0] for m in sender.messages]
    # HIGH first (oldest first within a tier), then MEDIUM.
    assert headers == [
        "🔴 HIGH | SEC — Press release",
        "🔴 HIGH | YHC — 8-K (Items 1.01, 5.02, 7.01, 9.01)",
        "🟡 MEDIUM | NATIONAL RURAL UTILITIES COOPERATIVE FINANCE CORP /DC/ — 8-K (Items 8.01, 9.01)",
    ]


def test_press_feed_failure_is_best_effort(tmp_path, sec, feeds):
    feeds["press"].mock(return_value=httpx.Response(503))
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, press_config())
    assert result.armed and not result.press_ok
    assert "press releases" not in sender.messages[0]

    use_full_press_feed(feeds)
    sender = RecordingSender()
    run(tmp_path, sec, sender, press_config())
    assert sender.messages == ["🛰️ sec-sentinel armed new sources — seeded 25 press releases."]


def test_failed_press_send_is_retried(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender(), press_config())
    use_full_press_feed(feeds)
    with pytest.raises(RuntimeError):
        run(tmp_path, sec, RecordingSender(fail_on=1), press_config())
    assert f"pr:{CUSTODY_RELEASE}" not in seen(tmp_path)

    sender = RecordingSender()
    run(tmp_path, sec, sender, press_config())
    assert len(sender.messages) == 1 and "Custody Crypto Assets" in sender.messages[0]


# --- Federal Register / 19b-4 ---


def fedreg_config(**overrides):
    data = {"keywords": ["bitcoin", "solana", "digital asset"], "watchlist_ciks": [], "federal_register": True}
    data.update(overrides)
    return make_config(**data)


def set_fedreg(feeds, fixture_name):
    feeds["fedreg"].mock(return_value=httpx.Response(200, content=fixture_bytes(fixture_name)))


def test_first_run_seeds_federal_register(tmp_path, sec, feeds):
    set_fedreg(feeds, "fedreg_window.json")
    sender = RecordingSender()
    run(tmp_path, sec, sender, fedreg_config())
    assert "19 press releases and 3 Federal Register documents" in sender.messages[0]
    assert "fr:2026-21128" in seen(tmp_path)

    sender = RecordingSender()
    run(tmp_path, sec, sender, fedreg_config())
    assert sender.messages == []


def test_new_federal_register_documents_alert(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender(), fedreg_config())  # armed with no documents
    set_fedreg(feeds, "fedreg_window.json")
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, fedreg_config())
    assert result.alerts == 3
    headers = sorted(m.split("\n")[0] for m in sender.messages)
    assert headers == [
        "🟡 MEDIUM | SEC — Federal Register notice",
        "🟡 MEDIUM | SEC — Federal Register notice",
        "🟡 MEDIUM | SEC — Federal Register proposed rule",
    ]
    assert all("\nPublished: 2026-10-1" in m for m in sender.messages)

    sender = RecordingSender()
    run(tmp_path, sec, sender, fedreg_config())
    assert sender.messages == []


def test_listing_decisions_sent_first_and_overflow_digested(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender(), fedreg_config())
    set_fedreg(feeds, "fedreg_crypto.json")  # 25 documents, 2 listing decisions
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, fedreg_config())
    assert result.alerts == 25
    assert len(sender.messages) == 16  # 15 individual + 1 digest
    assert [m.split("\n")[0] for m in sender.messages[:3]] == [
        "🔴 HIGH | SEC — Federal Register notice",
        "🔴 HIGH | SEC — Federal Register notice",
        "🟡 MEDIUM | SEC — Federal Register notice",
    ]
    assert "Generic Listing Standards for Commodity-Based Trust Shares" in "".join(sender.messages[:2])
    assert sender.messages[-1].startswith("📦 Digest: 10 more alerts this run")


def test_federal_register_armed_on_upgrade_and_best_effort(tmp_path, sec, feeds):
    run(tmp_path, sec, RecordingSender())  # older config: Federal Register off
    feeds["fedreg"].mock(return_value=httpx.Response(503))
    sender = RecordingSender()
    result = run(tmp_path, sec, sender, fedreg_config())
    assert not result.fedreg_ok and sender.messages == []  # not armed yet, no error alert

    set_fedreg(feeds, "fedreg_window.json")
    sender = RecordingSender()
    run(tmp_path, sec, sender, fedreg_config())
    assert sender.messages == ["🛰️ sec-sentinel armed new sources — seeded 3 Federal Register documents."]
