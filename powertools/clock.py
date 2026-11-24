"""UTC times as the app keeps and compares them: 2026-11-25T08:00:00Z (text order is time order)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> str:
    return iso(datetime.now(timezone.utc))


def days_ago(days: float) -> str:
    return iso(datetime.now(timezone.utc) - timedelta(days=days))
