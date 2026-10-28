"""UTC timestamps in one fixed-width ISO-8601 format.

All DB timestamps use ``YYYY-MM-DDTHH:MM:SS.ffffffZ``; the fixed width makes lexical comparison equal to
chronological comparison, which the lease queries rely on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def format_iso(dt: datetime) -> str:
    """Format an aware datetime as fixed-width UTC ISO-8601."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime; pass an aware datetime")
    return dt.astimezone(timezone.utc).strftime(_FMT)


def utcnow() -> datetime:
    """Current aware UTC datetime."""
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    """Current UTC time as fixed-width ISO-8601."""
    return format_iso(utcnow())


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` or offset) to an aware UTC datetime."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return dt.astimezone(timezone.utc)


def iso_after(base_iso: str, seconds: float) -> str:
    """``base_iso`` shifted by ``seconds`` in the same fixed-width format."""
    return format_iso(parse_iso(base_iso) + timedelta(seconds=seconds))
