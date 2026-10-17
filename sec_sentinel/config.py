"""Load and validate config.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    timezone_display: tuple[str, ...]
    forms: tuple[str, ...]
    watchlist_tickers: tuple[str, ...]
    watchlist_ciks: tuple[int, ...]
    keywords: tuple[str, ...]
    max_alerts_per_run: int
    fulltext_forms: tuple[str, ...] = ()  # empty disables full-text search


def _str_list(data: dict, key: str, *, required: bool) -> tuple[str, ...]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError(f"{key} must be a list of non-empty strings")
    if required and not value:
        raise ConfigError(f"{key} must not be empty")
    return tuple(v.strip() for v in value)


def parse_config(data: dict) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config must be a mapping")

    zones = _str_list(data, "timezone_display", required=True)
    for zone in zones:
        ZoneInfo(zone)  # raises if unknown

    ciks = data.get("watchlist_ciks") or []
    if not isinstance(ciks, list):
        raise ConfigError("watchlist_ciks must be a list")
    try:
        cik_values = tuple(int(c) for c in ciks)
    except (TypeError, ValueError) as exc:
        raise ConfigError("watchlist_ciks must contain integers") from exc

    max_alerts = data.get("max_alerts_per_run", 15)
    if not isinstance(max_alerts, int) or max_alerts < 1:
        raise ConfigError("max_alerts_per_run must be a positive integer")

    fulltext_forms = _str_list(data, "fulltext_forms", required=False)
    if any(f.endswith("/A") for f in fulltext_forms):
        # EDGAR full-text search ANDs amendment forms with base forms; base forms
        # already include their amendments.
        raise ConfigError("fulltext_forms must list base forms only (S-1 includes S-1/A)")

    return Config(
        timezone_display=zones,
        forms=_str_list(data, "forms", required=True),
        watchlist_tickers=tuple(t.upper() for t in _str_list(data, "watchlist_tickers", required=False)),
        watchlist_ciks=cik_values,
        keywords=_str_list(data, "keywords", required=False),
        max_alerts_per_run=max_alerts,
        fulltext_forms=fulltext_forms,
    )


def load_config(path: str | Path) -> Config:
    return parse_config(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
