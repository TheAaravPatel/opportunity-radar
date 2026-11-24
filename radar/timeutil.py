"""Small date/time helpers used everywhere.

All timestamps stored in the database are UTC ISO strings like
"2026-11-24T06:32:06Z". "Today" for deadlines means today in Pacific time,
because that's where you are.

Tests (and digest previews) can pretend it's a different moment by setting the
environment variable RADAR_NOW, e.g. RADAR_NOW=2027-01-05T15:00:00Z.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def now_utc() -> datetime:
    """The current time in UTC (or the time in RADAR_NOW, if set)."""
    fake = os.environ.get("RADAR_NOW")
    if fake:
        return parse_datetime(fake)
    return datetime.now(UTC)


def now_iso() -> str:
    """Current UTC time as an ISO string with a trailing Z."""
    return to_iso(now_utc())


def to_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_local(tz: ZoneInfo = PACIFIC) -> date:
    """Today's date in Pacific time (used for deadline countdowns)."""
    return now_utc().astimezone(tz).date()


def parse_datetime(value: str) -> datetime:
    """Parse an ISO timestamp; naive values are treated as UTC."""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def unix_to_date(seconds: int | float | None) -> str | None:
    """Unix seconds -> 'YYYY-MM-DD' (UTC), or None for missing/zero values."""
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds, UTC).date().isoformat()


def unix_to_iso(seconds: int | float | None) -> str | None:
    if not seconds:
        return None
    return to_iso(datetime.fromtimestamp(seconds, UTC))


def to_date_str(value) -> str | None:
    """Accept a date, datetime or 'YYYY-MM-DD...' string and return 'YYYY-MM-DD'."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def days_until(day: str, today: date | None = None) -> int:
    """Whole days from today until `day` ('YYYY-MM-DD'); negative if past."""
    today = today or today_local()
    return (date.fromisoformat(day) - today).days


def days_ago_iso(days: float) -> str:
    """UTC ISO timestamp for `days` days ago (handy for 'new in last 7 days')."""
    return to_iso(now_utc() - timedelta(days=days))
