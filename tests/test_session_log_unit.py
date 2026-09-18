"""Unit tests for the retrospective session-log UI helper.

The :func:`genesis.ui._render.build_retrospective_entry` helper
builds a ``TimeLogEntryCreate`` that is already ``COMPLETED`` so
the Statistics page can record past sessions without a stop action.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from genesis.category.enums import TimeLogCategory
from genesis.entities import (
    Task,
    TimeLogEntryCreate,
    TimeLogMode,
    TimeLogStatus,
)
from genesis.ui._render import build_focus_session_entry, build_retrospective_entry


def _task() -> Task:
    """Return a minimal Task instance for testing."""
    return Task(
        uid="t-uid-1",
        title="Demo task",
        status=__import__("genesis.entities", fromlist=["TaskStatus"]).TaskStatus.NEEDS_ACTION,
        priority=5,
    )


class TestBuildRetrospectiveEntry:
    """The helper must produce a COMPLETED entry with due == completed_at."""

    def test_unaligned_entry(self) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        entry = build_retrospective_entry(
            label="Gym session",
            category=TimeLogCategory.WORKOUT,
            start=start,
            duration_minutes=60,
            task=None,
        )

        assert isinstance(entry, TimeLogEntryCreate)
        assert entry.label == "Gym session"
        assert entry.task_id is None
        assert entry.start == start
        assert entry.due == start + timedelta(minutes=60)
        assert entry.completed_at == entry.due
        assert entry.status == TimeLogStatus.COMPLETED
        assert entry.mode == TimeLogMode.COUNTDOWN
        assert entry.category == TimeLogCategory.WORKOUT

    def test_aligned_entry_carries_task_id(self) -> None:
        start = datetime(2030, 3, 1, 9, 0, tzinfo=UTC)
        task = _task()
        entry = build_retrospective_entry(
            label="Deep work",
            category=TimeLogCategory.WORK,
            start=start,
            duration_minutes=45,
            task=task,
        )

        assert entry.task_id == task.uid
        assert entry.label == "Deep work"
        assert entry.category == TimeLogCategory.WORK

    def test_naive_start_is_coerced_to_utc(self) -> None:
        # Naive input is treated as local time per genesis.tz convention;
        # with TZ=UTC (set by conftest) it stays at 09:00 UTC.
        naive = datetime(2030, 3, 1, 9, 0)
        entry = build_retrospective_entry(
            label="Reading",
            category=TimeLogCategory.PERSONAL,
            start=naive,
            duration_minutes=30,
            task=None,
        )

        assert entry.start.tzinfo is not None
        assert entry.start == naive.replace(tzinfo=UTC)
        assert entry.due == entry.start + timedelta(minutes=30)

    @pytest.mark.parametrize(
        "category",
        [
            TimeLogCategory.WORK,
            TimeLogCategory.PLAY,
            TimeLogCategory.WORKOUT,
            TimeLogCategory.PERSONAL,
            TimeLogCategory.OTHER,
        ],
    )
    def test_every_category_round_trips(self, category: TimeLogCategory) -> None:
        entry = build_retrospective_entry(
            label="x",
            category=category,
            start=datetime(2030, 3, 1, 9, 0, tzinfo=UTC),
            duration_minutes=1,
            task=None,
        )
        assert entry.category == category


class TestBuildFocusSessionEntry:
    """The helper used by the new-session dialog must produce an
    IN_PROGRESS entry with the chosen category and (for countdown
    mode) a ``due`` set to start + duration."""

    def test_countdown_sets_due_and_carries_task(self) -> None:
        before = datetime.now(UTC) - timedelta(seconds=1)
        task = _task()
        entry = build_focus_session_entry(
            label="Deep work",
            task=task,
            mode=TimeLogMode.COUNTDOWN,
            countdown_minutes=25,
            category=TimeLogCategory.WORK,
        )

        assert isinstance(entry, TimeLogEntryCreate)
        assert entry.label == "Deep work"
        assert entry.task_id == task.uid
        assert entry.status == TimeLogStatus.IN_PROGRESS
        assert entry.mode == TimeLogMode.COUNTDOWN
        assert entry.category == TimeLogCategory.WORK
        assert entry.start >= before
        assert entry.due is not None
        assert entry.due - entry.start == timedelta(minutes=25)

    def test_countup_leaves_due_none(self) -> None:
        entry = build_focus_session_entry(
            label="Open ended",
            task=None,
            mode=TimeLogMode.COUNTUP,
            countdown_minutes=None,
            category=TimeLogCategory.PERSONAL,
        )

        assert entry.due is None
        assert entry.task_id is None
        assert entry.mode == TimeLogMode.COUNTUP
        assert entry.category == TimeLogCategory.PERSONAL

    def test_unaligned_records_chosen_category(self) -> None:
        entry = build_focus_session_entry(
            label="Gym",
            task=None,
            mode=TimeLogMode.COUNTUP,
            countdown_minutes=None,
            category=TimeLogCategory.WORKOUT,
        )

        assert entry.category == TimeLogCategory.WORKOUT
        assert entry.task_id is None
