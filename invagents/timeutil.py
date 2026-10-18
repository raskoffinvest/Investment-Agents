"""Time helpers. All stored timestamps are UTC ISO-8601; display uses the configured timezone."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def from_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def local_day_start(now: datetime, tz: str) -> datetime:
    local = now.astimezone(ZoneInfo(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def fmt_local(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M")


def in_quiet_hours(now: datetime, tz: str, start: str, end: str) -> bool:
    local = now.astimezone(ZoneInfo(tz)).time()
    s, e = time.fromisoformat(start), time.fromisoformat(end)
    if s <= e:
        return s <= local < e
    return local >= s or local < e
