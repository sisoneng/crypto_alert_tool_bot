"""One poll cycle: fetch -> dedupe -> classify -> send -> save state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Callable, Protocol

from .config import Config
from .edgar import Filing, fetch_filings
from .formatting import Alert, filing_alert, format_alert, format_digest, press_alert
from .fulltext import EASTERN, fetch_fulltext
from .http import SecClient
from .press import fetch_press_releases
from .priority import Tier, classify, classify_press, compile_keywords
from .state import State, load_state, save_state
from .submissions import fetch_items
from .tickers import TickerIndex, load_ticker_index, resolve_watchlist

log = logging.getLogger(__name__)

FEED = "feed"
FULLTEXT = "fulltext"
PRESS = "press"
FULLTEXT_KEY_PREFIX = "fts:"
PRESS_KEY_PREFIX = "pr:"


class Sender(Protocol):
    def send(self, text: str) -> None: ...


@dataclass(frozen=True)
class RunResult:
    armed: bool
    new_filings: int
    alerts: int
    failed_forms: tuple[str, ...]
    fulltext_ok: bool = True
    press_ok: bool = True


@dataclass
class Candidate:
    filing: Filing
    fulltext_hits: list[str]
    keys: list[str]  # seen keys to record once handled


def _label(filing: Filing, watch: dict[int, str], index: TickerIndex) -> str:
    for entity in filing.entities:
        if watch.get(entity.cik):
            return watch[entity.cik]
    return index.ticker_by_cik.get(filing.primary.cik) or filing.primary.name


def _seed(state: State, detector: str, keys: list[str]) -> int:
    for key in keys:
        state.add(key)
    state.arm(detector)
    return len(keys)


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _collect(
    state: State, filings: dict[str, Filing], fulltext: dict[str, tuple[Filing, list[str]]]
) -> list[Candidate]:
    """Unseen filings from both filing detectors, merged by accession."""
    candidates: dict[str, Candidate] = {}
    for accession, filing in filings.items():
        if not state.has(accession):
            candidates[accession] = Candidate(filing, [], [accession])
    for accession, (filing, hits) in fulltext.items():
        key = FULLTEXT_KEY_PREFIX + accession
        if state.has(key):
            continue
        if accession in candidates:
            # Prefer the feed's copy (it has the acceptance time and entity roles).
            candidate = candidates[accession]
            candidate.fulltext_hits = hits
            candidate.keys.append(key)
            candidate.filing.items = candidate.filing.items or filing.items
        else:
            candidates[accession] = Candidate(filing, hits, [key])
    return sorted(candidates.values(), key=lambda c: c.filing.filed_at)


def run_once(
    config: Config,
    *,
    sec: SecClient,
    sender: Sender,
    state_path: Path,
    tickers_cache_path: Path,
    now: datetime | None = None,
) -> RunResult:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date().isoformat()
    state = load_state(state_path)
    index = load_ticker_index(sec, tickers_cache_path)
    watch = resolve_watchlist(index, config.watchlist_tickers, config.watchlist_ciks)

    filings, failed = fetch_filings(sec, config.forms)
    if len(failed) == len(config.forms):
        raise RuntimeError("all EDGAR feeds failed")

    fulltext_on = bool(config.fulltext_forms and config.keywords)
    fulltext: dict[str, tuple[Filing, list[str]]] = {}
    fulltext_ok = True
    if fulltext_on:
        fulltext, fulltext_ok = fetch_fulltext(
            sec, config.keywords, config.fulltext_forms, now.astimezone(EASTERN).date()
        )
    releases, press_ok = fetch_press_releases(sec)

    # Arming: a detector's first successful run marks everything it sees as seen,
    # so adding a detector (or starting fresh) never floods the chat.
    first_run = FEED not in state.armed
    if first_run:
        if failed:
            # Arming with a missing feed would flood alerts for that form next run.
            raise RuntimeError(f"not arming: feeds failed for {failed}")
        seeded = [f"{_seed(state, FEED, list(filings))} current filings across {len(config.forms)} forms"]
        state.heartbeat_day = today
    else:
        seeded = []
    if fulltext_on and FULLTEXT not in state.armed and fulltext_ok:
        keys = [FULLTEXT_KEY_PREFIX + acc for acc in fulltext]
        seeded.append(f"{_seed(state, FULLTEXT, keys)} full-text matches")
    if PRESS not in state.armed and press_ok:
        keys = [PRESS_KEY_PREFIX + r.key for r in releases]
        seeded.append(f"{_seed(state, PRESS, keys)} press releases")
    if FULLTEXT not in state.armed:
        fulltext = {}  # unarmed: results may be partial, never alert from them
    if PRESS not in state.armed:
        releases = []

    if first_run:
        save_state(state_path, state)
        sender.send(f"🛰️ sec-sentinel armed — seeded {_join(seeded)}. Alerts start next run.")
        log.info("armed: %s", _join(seeded))
        return RunResult(True, 0, 0, (), fulltext_ok, press_ok)
    if seeded:
        save_state(state_path, state)
        sender.send(f"🛰️ sec-sentinel armed new sources — seeded {_join(seeded)}.")

    if state.heartbeat_day != today:
        sender.send(f"✅ alive — {state.scanned} filings scanned in 24h, {state.alerts} alerts")
        state.heartbeat_day, state.scanned, state.alerts = today, 0, 0
        save_state(state_path, state)

    def mark(candidate: Candidate, alerted: bool) -> None:
        for key in candidate.keys:
            state.add(key)
        if candidate.filing.accession in candidate.keys:  # first sighting in the feed
            state.scanned += 1
        if alerted:
            state.add_alerted(candidate.filing.accession)
            state.alerts += 1

    def mark_press(key: str, alerted: bool) -> None:
        state.add(key)
        state.alerts += int(alerted)

    patterns = compile_keywords(config.keywords)
    pending: list[tuple[Alert, Callable[[], None]]] = []
    quiet: list[Callable[[], None]] = []

    candidates = _collect(state, filings, fulltext)
    for candidate in candidates:
        filing = candidate.filing
        if state.was_alerted(filing.accession):
            quiet.append(partial(mark, candidate, False))  # the other detector already alerted
            continue
        watched = filing.ciks & set(watch)
        if filing.form == "8-K" and not filing.items and watched:
            filing.items = fetch_items(sec, min(watched), filing.accession)
        verdict = classify(filing, watch, patterns, candidate.fulltext_hits)
        if verdict.tier is Tier.LOW:
            quiet.append(partial(mark, candidate, False))
            log.info("low: %s %s %s", filing.form, filing.primary.name, filing.accession)
        else:
            alert = filing_alert(filing, verdict, _label(filing, watch, index))
            pending.append((alert, partial(mark, candidate, True)))

    new_releases = [r for r in releases if not state.has(PRESS_KEY_PREFIX + r.key)]
    for release in new_releases:
        key = PRESS_KEY_PREFIX + release.key
        verdict = classify_press(release, patterns)
        if verdict.tier is Tier.LOW:
            quiet.append(partial(mark_press, key, False))
            log.info("low: press release %r", release.title)
        else:
            pending.append((press_alert(release, verdict), partial(mark_press, key, True)))

    pending.sort(key=lambda pair: (-pair[0].tier, pair[0].when))
    individual = pending[: config.max_alerts_per_run]
    overflow = pending[config.max_alerts_per_run :]

    # Mark each item seen only after its alert is delivered; if a send fails,
    # the remaining alerts are retried next run.
    try:
        for mark_quiet in quiet:
            mark_quiet()
        for alert, mark_sent in individual:
            sender.send(format_alert(alert, config.timezone_display))
            mark_sent()
        if overflow:
            sender.send(format_digest([alert for alert, _ in overflow]))
            for _, mark_sent in overflow:
                mark_sent()
    finally:
        save_state(state_path, state)

    log.info(
        "run: %d new filings, %d new press releases, %d alerts (%d in digest)",
        len(candidates), len(new_releases), len(pending), len(overflow),
    )
    return RunResult(False, len(candidates), len(pending), tuple(failed), fulltext_ok, press_ok)
