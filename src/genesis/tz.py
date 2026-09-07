"""Timezone handling for the genesis productivity system.

Convention
----------
All entity datetime fields hold tz-aware UTC datetimes. Naive datetimes
from user input (Streamlit widgets, pandas conversions, file
timestamps) are coerced to UTC at the boundary by treating them as
local time. Display code converts UTC back to local time via
``to_local``.

This module is the single source of truth for that conversion. Both
the CalDAV layer and the UI layer use it; entity validators use it.
"""

from __future__ import annotations

from datetime import UTC, datetime


def to_utc(dt: datetime) -> datetime:
    """Coerce a datetime to tz-aware UTC.

    Naive datetimes are assumed to be in the system's local timezone;
    they are attached to local tz then converted to UTC.

    Aware datetimes are converted in place to UTC regardless of their
    original zone. This is lossy for the display layer if it forgets
    to call ``to_local`` afterwards, but it prevents the storage layer
    from accidentally writing datetimes with non-UTC tzinfo (which
    would require VTIMEZONE blocks).
    """
    if dt.tzinfo is None:
        # Find the system's local timezone via a known UTC instant.
        local_tz = datetime.now(UTC).astimezone().tzinfo
        assert local_tz is not None
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(UTC)


def to_utc_optional(dt: datetime | None) -> datetime | None:
    """Coerce an optional datetime to tz-aware UTC. Returns None as-is."""
    if dt is None:
        return None
    return to_utc(dt)


def to_local(dt: datetime) -> datetime:
    """Convert a tz-aware UTC datetime to the system's local timezone.

    Naive datetimes are assumed to already be local time and returned
    unchanged. Display code that needs to show a datetime should
    always pass a UTC datetime (entity convention) and use this
    function before formatting.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone()


def to_local_optional(dt: datetime | None) -> datetime | None:
    """Convert an optional tz-aware UTC datetime to local. None passes through."""
    if dt is None:
        return None
    return to_local(dt)


def format_local(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a tz-aware UTC datetime in the system's local timezone.

    Returns an empty string for None. Naive datetimes are formatted
    as-is (treated as already-local).
    """
    if dt is None:
        return ""
    return to_local(dt).strftime(fmt)
