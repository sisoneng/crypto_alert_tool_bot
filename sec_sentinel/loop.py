"""--loop mode: run poll cycles forever, surviving errors.

- A failed cycle is logged and retried; it never ends the loop.
- After repeated failures the delay backs off (interval x2, x4, ... up to MAX_BACKOFF).
- The chat is told once when failures reach FAILURE_ALERT_AFTER, and once on recovery.
- The interval is measured from cycle start, so a slow cycle does not add drift.
- `stop` (set by SIGINT/SIGTERM) ends the loop after the current cycle or during the wait.
"""

from __future__ import annotations

import logging
import threading
import time
from html import escape
from typing import Callable

log = logging.getLogger(__name__)

FAILURE_ALERT_AFTER = 3
MAX_BACKOFF_SECONDS = 900
MIN_INTERVAL_SECONDS = 30


def next_delay(interval: float, failures: int) -> float:
    if failures == 0:
        return interval
    return min(interval * 2 ** min(failures, 10), MAX_BACKOFF_SECONDS)


def wait_or_stop(stop: threading.Event, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
    # Short slices: on Windows a signal cannot interrupt a long Event.wait(), so
    # the SIGINT/SIGBREAK handler would otherwise only run when the wait ends.
    deadline = clock() + seconds
    while not stop.is_set():
        remaining = deadline - clock()
        if remaining <= 0:
            return
        stop.wait(min(remaining, 1.0))


def _notify(notify: Callable[[str], None], text: str) -> None:
    try:
        notify(text)
    except Exception:  # the failure may be the notifier itself
        log.exception("could not send loop notification")


def run_loop(
    cycle: Callable[[], object],
    *,
    interval: float,
    stop: threading.Event,
    notify: Callable[[str], None],
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], object] | None = None,
) -> None:
    if wait is None:
        wait = lambda seconds: wait_or_stop(stop, seconds, clock=clock)  # noqa: E731
    failures = 0
    while not stop.is_set():
        started = clock()
        try:
            cycle()
        except Exception as exc:
            failures += 1
            log.exception("poll cycle failed (%d in a row)", failures)
            if failures == FAILURE_ALERT_AFTER:
                detail = escape(f"{type(exc).__name__}: {exc}"[:300])
                _notify(notify, f"⚠️ sec-sentinel: {failures} poll cycles failed in a row\n{detail}")
        else:
            if failures >= FAILURE_ALERT_AFTER:
                _notify(notify, f"✅ sec-sentinel recovered after {failures} failed cycles")
            failures = 0
        wait(max(next_delay(interval, failures) - (clock() - started), 0.0))
    log.info("loop stopped")
