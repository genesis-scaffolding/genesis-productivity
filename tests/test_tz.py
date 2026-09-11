"""Tests for ``genesis.tz``: tz-aware UTC convention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from genesis.tz import format_local, to_local, to_utc, to_utc_optional


class TestToUtc:
    """``to_utc`` coerces naive and aware datetimes to tz-aware UTC."""

    def test_naive_assumed_local_and_converted(self) -> None:
        # Build a naive local datetime matching the system clock, then
        # round-trip through to_utc should land on the same instant.
        local_now = datetime.now()
        utc = to_utc(local_now)
        assert utc.tzinfo is UTC
        # Convert back to local (naive) and compare instants.
        local_back = utc.astimezone().replace(tzinfo=None)
        assert abs((local_back - local_now).total_seconds()) < 1

    def test_aware_non_utc_converted(self) -> None:
        # Tokyo time: +09:00, no DST.
        tokyo = timezone(timedelta(hours=9))
        dt = datetime(2026, 9, 7, 14, 0, tzinfo=tokyo)
        utc = to_utc(dt)
        assert utc.tzinfo is UTC
        assert utc.hour == 5  # 14:00 JST -> 05:00 UTC

    def test_aware_utc_passes_through(self) -> None:
        dt = datetime(2026, 9, 7, 5, 0, tzinfo=UTC)
        utc = to_utc(dt)
        assert utc is dt or utc == dt
        assert utc.tzinfo is UTC

    def test_negative_offset(self) -> None:
        # New York EDT: -04:00 in September.
        ny = timezone(timedelta(hours=-4))
        dt = datetime(2026, 9, 7, 10, 0, tzinfo=ny)
        utc = to_utc(dt)
        assert utc.tzinfo is UTC
        assert utc.hour == 14  # 10:00 EDT -> 14:00 UTC


class TestToLocal:
    """``to_local`` converts UTC to system local for display."""

    def test_aware_utc_to_local(self) -> None:
        utc = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)
        local = to_local(utc)
        # The local representation equals the local clock face of the UTC
        # instant; for a non-UTC zone, the hour must differ from 14.
        local_tz = datetime.now(UTC).astimezone().tzinfo
        assert local_tz is not None
        if local_tz.utcoffset(utc) != timedelta(0):
            assert local.hour != 14 or local.day != utc.day
        # Round trip: to_local -> to_utc -> original UTC.
        assert to_utc(local) == utc

    def test_naive_passes_through(self) -> None:
        dt = datetime(2026, 9, 7, 14, 0)
        assert to_local(dt) is dt


class TestToUtcOptional:
    """``to_utc_optional`` handles None."""

    def test_none(self) -> None:
        assert to_utc_optional(None) is None

    def test_value(self) -> None:
        result = to_utc_optional(datetime(2026, 9, 7, 14, 0))
        assert result is not None
        assert result.tzinfo is UTC


class TestFormatLocal:
    """``format_local`` formats UTC as a local-time string."""

    def test_none_returns_empty(self) -> None:
        assert format_local(None) == ""

    def test_default_format(self) -> None:
        utc = datetime(2026, 9, 7, 14, 30, tzinfo=UTC)
        formatted = format_local(utc)
        # Should match YYYY-MM-DD HH:MM after local conversion.
        assert len(formatted) == 16
        assert formatted[4] == "-"
        assert formatted[7] == "-"
        assert formatted[10] == " "
        assert formatted[13] == ":"

    def test_custom_format(self) -> None:
        utc = datetime(2026, 9, 7, 14, 30, tzinfo=UTC)
        assert format_local(utc, "%Y") == str(datetime.now().year)


class TestEntityValidators:
    """Entity constructors coerce naive datetimes to tz-aware UTC."""

    def test_time_log_entry_naive_start_becomes_utc(self) -> None:
        from genesis.entities import TimeLogEntry, TimeLogMode

        naive = datetime(2026, 9, 7, 14, 0)
        entry = TimeLogEntry(
            uid="test",
            label="x",
            start=naive,
            mode=TimeLogMode.COUNTUP,
        )
        assert entry.start.tzinfo is UTC

    def test_task_naive_scheduled_becomes_utc(self) -> None:
        from genesis.entities import Task

        naive = datetime(2026, 9, 7, 14, 0)
        task = Task(uid="t", title="x", scheduled_start=naive)
        assert task.scheduled_start is not None
        assert task.scheduled_start.tzinfo is UTC

    def test_event_naive_dates_become_utc(self) -> None:
        from genesis.entities import Event, EventStatus

        naive_start = datetime(2026, 9, 7, 14, 0)
        naive_end = datetime(2026, 9, 7, 15, 0)
        event = Event(
            uid="e",
            summary="x",
            start=naive_start,
            end=naive_end,
            status=EventStatus.CONFIRMED,
        )
        assert event.start.tzinfo is UTC
        assert event.end.tzinfo is UTC

    def test_aware_input_preserved_as_utc(self) -> None:
        from genesis.entities import TimeLogEntry, TimeLogMode

        # Tokyo time, +09:00.
        tokyo = timezone(timedelta(hours=9))
        aware = datetime(2026, 9, 7, 14, 0, tzinfo=tokyo)
        entry = TimeLogEntry(
            uid="t",
            label="x",
            start=aware,
            mode=TimeLogMode.COUNTUP,
        )
        assert entry.start.tzinfo is UTC
        # Converted: 14:00 JST -> 05:00 UTC.
        assert entry.start.hour == 5
        assert entry.start.day == 7


@pytest.mark.parametrize(
    "tz_offset",
    [
        timedelta(hours=9),  # Tokyo
        timedelta(hours=-4),  # NY EDT
        timedelta(hours=5, minutes=30),  # India
        timedelta(hours=9, minutes=30),  # Australia (ACST)
    ],
)
def test_round_trip_through_entity(tz_offset: timedelta) -> None:
    """A datetime from any tz round-trips to the same UTC instant."""
    from genesis.entities import TimeLogEntry, TimeLogMode

    zone = timezone(tz_offset)
    original = datetime(2026, 9, 7, 12, 0, tzinfo=zone)
    entry = TimeLogEntry(
        uid="t",
        label="x",
        start=original,
        mode=TimeLogMode.COUNTUP,
    )
    assert entry.start.tzinfo is UTC
    # Converting back to the original zone yields the same wall clock.
    back = entry.start.astimezone(zone)
    assert back == original
