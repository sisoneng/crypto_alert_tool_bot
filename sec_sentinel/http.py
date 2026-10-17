"""SEC HTTP client: required User-Agent, global rate limit, retry with backoff."""

from __future__ import annotations

import logging
import random
import time
from typing import Callable

import httpx

log = logging.getLogger(__name__)

SEC_MAX_RPS = 5.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class SecError(RuntimeError):
    pass


class RateLimiter:
    """Spaces calls to wait() at least 1/max_per_second apart."""

    def __init__(
        self,
        max_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._next_at: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next_at is not None and now < self._next_at:
            self._sleep(self._next_at - now)
            now = self._next_at
        self._next_at = now + self.min_interval


class SecClient:
    def __init__(
        self,
        user_agent: str,
        *,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        max_tries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("SEC user agent is required")
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=20.0,
            follow_redirects=True,
        )
        self._limiter = limiter or RateLimiter(SEC_MAX_RPS, sleep=sleep)
        self._sleep = sleep
        self._jitter = jitter
        self._max_tries = max_tries
        self._backoff_base = backoff_base

    def get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._max_tries + 1):
            self._limiter.wait()
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    return resp
                last_error = SecError(f"HTTP {resp.status_code}")
            if attempt < self._max_tries:
                delay = self._backoff_base * 2 ** (attempt - 1) + self._jitter()
                log.warning("SEC GET %s failed (%s), retry %d in %.1fs", url, last_error, attempt, delay)
                self._sleep(delay)
        raise SecError(f"GET {url} failed after {self._max_tries} tries: {last_error}") from last_error

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SecClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
