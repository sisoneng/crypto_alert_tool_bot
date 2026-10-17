"""company_tickers.json: ticker <-> CIK lookup, cached on disk for 24h.

Shape (verified, see tests/fixtures/company_tickers.json):
  {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}, ...}
One CIK can have several tickers (e.g. MSTR, STRK, STRF); rows are ordered so
the first one per CIK is the common stock.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable

import httpx

from .http import SecClient, SecError
from .state import atomic_write

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE_TTL_SECONDS = 24 * 3600


class TickerIndex:
    def __init__(self, rows: list[tuple[int, str]]) -> None:
        self.cik_by_ticker: dict[str, int] = {}
        self.ticker_by_cik: dict[int, str] = {}
        for cik, ticker in rows:
            self.cik_by_ticker.setdefault(ticker, cik)
            self.ticker_by_cik.setdefault(cik, ticker)


def _parse(content: bytes) -> list[tuple[int, str]]:
    data = json.loads(content)
    return [(int(row["cik_str"]), str(row["ticker"]).upper()) for row in data.values()]


def load_ticker_index(
    sec: SecClient, cache_path: Path, *, now: Callable[[], float] = time.time
) -> TickerIndex:
    if cache_path.exists() and now() - cache_path.stat().st_mtime < CACHE_TTL_SECONDS:
        return TickerIndex(_parse(cache_path.read_bytes()))
    try:
        content = sec.get(TICKERS_URL).content
        rows = _parse(content)
    except (SecError, httpx.HTTPError, ValueError, KeyError) as exc:
        if not cache_path.exists():
            raise
        log.warning("ticker map refresh failed (%s); using stale cache", exc)
        return TickerIndex(_parse(cache_path.read_bytes()))
    atomic_write(cache_path, content)
    return TickerIndex(rows)


def resolve_watchlist(
    index: TickerIndex, tickers: tuple[str, ...], ciks: tuple[int, ...]
) -> dict[int, str]:
    """Return {cik: display label}. Label is the watchlist ticker, else any known ticker, else ''."""
    watch: dict[int, str] = {}
    for ticker in tickers:
        cik = index.cik_by_ticker.get(ticker.upper())
        if cik is None:
            log.warning("watchlist ticker %s not found in company_tickers.json", ticker)
            continue
        watch[cik] = ticker.upper()
    for cik in ciks:
        watch.setdefault(cik, index.ticker_by_cik.get(cik, ""))
    return watch
