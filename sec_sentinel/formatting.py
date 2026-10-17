"""Telegram HTML message bodies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .edgar import Filing
from .press import PressRelease
from .priority import Tier, Verdict

TIER_EMOJI = {Tier.HIGH: "🔴", Tier.MEDIUM: "🟡"}
# tzdata abbreviates Asia/Manila as "PST"; use the familiar labels instead.
TZ_LABELS = {"America/New_York": "ET", "Asia/Manila": "PHT"}
TELEGRAM_MAX_CHARS = 4096


@dataclass(frozen=True)
class Alert:
    tier: Tier
    reasons: tuple[str, ...]
    label: str  # ticker or company name; "SEC" for press releases
    kind: str  # "8-K (Items 7.01, 8.01)", "Press release"
    title: str  # company name or press release headline
    when: datetime
    has_time: bool
    when_label: str  # "Filed" or "Published"
    link: str


def format_form(filing: Filing) -> str:
    if not filing.items:
        return filing.form
    noun = "Item" if len(filing.items) == 1 else "Items"
    return f"{filing.form} ({noun} {', '.join(filing.items)})"


def filing_alert(filing: Filing, verdict: Verdict, label: str) -> Alert:
    return Alert(
        verdict.tier, verdict.reasons, label, format_form(filing), filing.primary.name,
        filing.filed_at, filing.has_time, "Filed", filing.link,
    )


def press_alert(release: PressRelease, verdict: Verdict) -> Alert:
    return Alert(
        verdict.tier, verdict.reasons, "SEC", "Press release", release.title,
        release.published, True, "Published", release.link,
    )


def format_time(dt: datetime, zones: tuple[str, ...]) -> str:
    parts: list[str] = []
    first_date: str | None = None
    for zone in zones:
        local = dt.astimezone(ZoneInfo(zone))
        label = TZ_LABELS.get(zone) or local.tzname() or zone
        date = local.strftime("%Y-%m-%d")
        clock = local.strftime("%H:%M")
        if first_date is None or date != first_date:
            parts.append(f"{date} {clock} {label}")
        else:
            parts.append(f"{clock} {label}")
        first_date = first_date or date
    return " / ".join(parts)


def format_alert(alert: Alert, zones: tuple[str, ...]) -> str:
    when = format_time(alert.when, zones) if alert.has_time else alert.when.strftime("%Y-%m-%d")
    return "\n".join(
        [
            f"{TIER_EMOJI[alert.tier]} {alert.tier.name} | {escape(alert.label)} — {escape(alert.kind)}",
            escape(alert.title),
            f"{alert.when_label}: {when}",
            f"Matched: {escape(', '.join(alert.reasons))}",
            f"🔗 {escape(alert.link)}",
        ]
    )


def format_digest(alerts: list[Alert], max_chars: int = TELEGRAM_MAX_CHARS) -> str:
    lines = [f"📦 Digest: {len(alerts)} more alerts this run"]
    for i, a in enumerate(alerts):
        line = f"{TIER_EMOJI[a.tier]} {escape(a.label)} — {escape(a.kind)} — {escape(a.link)}"
        footer = f"…and {len(alerts) - i} more"
        if len("\n".join([*lines, line, footer])) > max_chars:
            lines.append(footer)
            break
        lines.append(line)
    return "\n".join(lines)
