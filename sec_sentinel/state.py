"""Dedupe state in state/seen.json, written atomically.

- seen:    keys each detector has processed (feed: accession; full-text: "fts:" + accession)
- alerted: accessions already alerted by any detector, so one filing alerts once
- armed:   detectors that have been seeded; a new detector seeds silently on its first run
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_SEEN = 5000
STATE_VERSION = 2


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@dataclass
class State:
    seen: list[str] = field(default_factory=list)  # oldest first
    alerted: list[str] = field(default_factory=list)  # oldest first
    armed: list[str] = field(default_factory=list)
    heartbeat_day: str | None = None  # UTC date of the last heartbeat
    scanned: int = 0  # new filings processed since the last heartbeat
    alerts: int = 0  # alerts sent since the last heartbeat

    def __post_init__(self) -> None:
        self._seen = set(self.seen)
        self._alerted = set(self.alerted)

    def has(self, key: str) -> bool:
        return key in self._seen

    def add(self, key: str) -> None:
        if key not in self._seen:
            self._seen.add(key)
            self.seen.append(key)

    def was_alerted(self, accession: str) -> bool:
        return accession in self._alerted

    def add_alerted(self, accession: str) -> None:
        if accession not in self._alerted:
            self._alerted.add(accession)
            self.alerted.append(accession)

    def arm(self, detector: str) -> None:
        if detector not in self.armed:
            self.armed.append(detector)


def load_state(path: Path) -> State:
    if not path.exists():
        return State()
    data = json.loads(path.read_text(encoding="utf-8"))
    heartbeat = data.get("heartbeat") or {}
    seen = list(data.get("seen") or [])
    armed = data.get("armed")
    if armed is None:  # version 1: only the feed existed
        armed = ["feed"] if seen else []
    return State(
        seen=seen,
        alerted=list(data.get("alerted") or []),
        armed=list(armed),
        heartbeat_day=heartbeat.get("day"),
        scanned=int(heartbeat.get("scanned", 0)),
        alerts=int(heartbeat.get("alerts", 0)),
    )


def save_state(path: Path, state: State) -> None:
    state.seen = state.seen[-MAX_SEEN:]
    state.alerted = state.alerted[-MAX_SEEN:]
    state.__post_init__()
    doc = {
        "version": STATE_VERSION,
        "armed": state.armed,
        "heartbeat": {"day": state.heartbeat_day, "scanned": state.scanned, "alerts": state.alerts},
        "alerted": state.alerted,
        "seen": state.seen,
    }
    # One key per line keeps git diffs of the committed state small.
    atomic_write(path, (json.dumps(doc, indent=1) + "\n").encode("utf-8"))
