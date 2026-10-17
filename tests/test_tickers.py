import os
import time

import httpx
import respx

from conftest import fixture_bytes
from sec_sentinel.tickers import TICKERS_URL, load_ticker_index, resolve_watchlist


@respx.mock
def test_fetches_and_caches(sec, tmp_path):
    cache = tmp_path / "company_tickers.json"
    route = respx.get(TICKERS_URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("company_tickers.json"))
    )
    index = load_ticker_index(sec, cache)
    assert index.cik_by_ticker["MSTR"] == 1050446
    assert index.ticker_by_cik[1050446] == "MSTR"  # first of MSTR/STRC/STRF/STRK/STRD
    assert index.cik_by_ticker["STRK"] == 1050446
    load_ticker_index(sec, cache)
    assert route.call_count == 1  # second load served from cache


@respx.mock
def test_refreshes_stale_cache_and_falls_back_on_error(sec, tmp_path):
    cache = tmp_path / "company_tickers.json"
    cache.write_bytes(fixture_bytes("company_tickers.json"))
    old = time.time() - 25 * 3600
    os.utime(cache, (old, old))
    route = respx.get(TICKERS_URL).mock(return_value=httpx.Response(503))
    index = load_ticker_index(sec, cache)
    assert route.call_count == 3
    assert index.cik_by_ticker["COIN"] == 1679788


def test_resolve_watchlist():
    from sec_sentinel.tickers import TickerIndex

    index = TickerIndex([(1050446, "MSTR"), (1050446, "STRK"), (1679788, "COIN"), (999, "ZZZ")])
    watch = resolve_watchlist(index, ("MSTR", "COIN", "NOPE"), (999, 12345))
    assert watch == {1050446: "MSTR", 1679788: "COIN", 999: "ZZZ", 12345: ""}
