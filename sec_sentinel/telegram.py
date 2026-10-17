"""Message senders: Telegram Bot API, or stdout for --dry-run."""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

from .http import RateLimiter

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramSender:
    """sendMessage with HTML parse mode, <=1 msg/sec, and 429 retry_after handling.

    Error messages never include the request URL, because it contains the bot token.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_tries: int = 3,
    ) -> None:
        self._url = f"{API_BASE}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._client = httpx.Client(timeout=20.0)
        self._limiter = RateLimiter(1.0, clock=clock, sleep=sleep)
        self._sleep = sleep
        self._max_tries = max_tries

    def send(self, text: str) -> None:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        for attempt in range(1, self._max_tries + 1):
            self._limiter.wait()
            try:
                resp = self._client.post(self._url, json=payload)
            except httpx.TransportError as exc:
                raise TelegramError(f"sendMessage transport error: {type(exc).__name__}") from None
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code == 429 and attempt < self._max_tries:
                retry_after = float((data.get("parameters") or {}).get("retry_after", 1))
                log.warning("Telegram 429, retrying in %.0fs", retry_after)
                self._sleep(retry_after)
                continue
            if resp.status_code != 200 or not data.get("ok"):
                raise TelegramError(
                    f"sendMessage failed: HTTP {resp.status_code}: {data.get('description', '')}"
                )
            return

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TelegramSender:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class StdoutSender:
    def send(self, text: str) -> None:
        print(text)
        print("-" * 40, flush=True)

    def __enter__(self) -> StdoutSender:
        return self

    def __exit__(self, *exc: object) -> None:
        pass
