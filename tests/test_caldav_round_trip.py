"""Round-trip tests for the CalDAV iCal layer.

These pin the wire shape of each entity so an accidental drop of a
field (like ``X-CATEGORY``) is caught by the suite instead of
silently corrupting legacy data on disk.

Run via ``make test``. Integration tests against a real Radicale
live in ``tests/test_api.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from genesis.caldav.ical import time_log_from_ical, time_log_to_ical
from genesis.category.enums import TimeLogCategory
from genesis.entities import TimeLogEntry, TimeLogMode, TimeLogStatus


def _entry(*, category: TimeLogCategory) -> TimeLogEntry:
    return TimeLogEntry(
        uid="l-test1",
        label="Cooking",
        start=datetime(2026, 9, 9, 21, 55, 4, tzinfo=UTC),
        due=datetime(2026, 9, 9, 23, 29, 11, tzinfo=UTC),
        completed_at=datetime(2026, 9, 9, 23, 29, 11, tzinfo=UTC),
        status=TimeLogStatus.COMPLETED,
        mode=TimeLogMode.COUNTUP,
        task_id=None,
        category=category,
    )


def test_time_log_round_trip_preserves_category() -> None:
    """The whole point of the category feature: a written X-CATEGORY
    must come back unchanged after a round trip through the iCal
    layer. Regression guard for the bug where ``time_log_from_ical``
    silently dropped X-CATEGORY.
    """
    original = _entry(category=TimeLogCategory.PERSONAL)
    component = time_log_to_ical(original)
    restored = time_log_from_ical(component)
    assert restored.category == TimeLogCategory.PERSONAL


def test_time_log_round_trip_other_omits_x_category() -> None:
    """``OTHER`` is the legacy default. Writing it back keeps the
    VTODO lean: no ``X-CATEGORY`` line, so the file diff is empty
    against a pre-category entry.
    """
    original = _entry(category=TimeLogCategory.OTHER)
    component = time_log_to_ical(original)
    # The component is an icalendar.Todo; walking for X-CATEGORY
    # confirms it's not set.
    assert "X-CATEGORY" not in [str(p).upper() for p in component.walk()]


def test_time_log_round_trip_legacy_entry_defaults_to_other() -> None:
    """A pre-category VTODO without ``X-CATEGORY`` reads back as
    ``OTHER`` so downstream code never has to handle nulls.
    """
    legacy = time_log_from_ical(_build_legacy_vtodo())
    assert legacy.category == TimeLogCategory.OTHER


def _build_legacy_vtodo():
    """Build a VTODO component without X-CATEGORY, the way
    Radicale's storage writes it for entries created before the
    category feature landed.
    """
    import icalendar

    cal = icalendar.Todo()
    cal.add("uid", "l-legacy")
    cal.add("summary", "Old session")
    cal.add("dtstart", datetime(2026, 9, 1, 10, 0, tzinfo=UTC))
    cal.add("due", datetime(2026, 9, 1, 10, 30, tzinfo=UTC))
    cal.add("status", "completed")
    cal.add("x-mode", "countdown")
    return cal
