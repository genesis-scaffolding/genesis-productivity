"""Unit tests for recurrent tasks (ADR-033).

The tests drive the in-memory CalDAV backend, which runs the same
recurrence engine as the real one. They cover the rule arithmetic, the
materialized horizon (N), the rolling renewal, the ownership rule of
the series-owned fields, the series edit, the end date, the delete
that does not come back, the series cancellation, and the retry-safety
of the engine.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from genesis.caldav.ical import recurrence_occurrence_uid
from genesis.entities import (
    RecurrenceAnchor,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesUpdate,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
    add_months,
    add_years,
    occurrence_date,
)
from tests.mock_caldav import MockCalDAVFacade

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _rule(
    frequency: RecurrenceFrequency = RecurrenceFrequency.MONTHLY,
    interval: int = 1,
    anchor: RecurrenceAnchor = RecurrenceAnchor.ASSIGNED_DATE,
) -> RecurrenceRule:
    return RecurrenceRule(frequency=frequency, interval=interval, anchor=anchor)


def _facade() -> MockCalDAVFacade:
    return MockCalDAVFacade()


def _create(
    facade: MockCalDAVFacade,
    *,
    count: int = 1,
    title: str = "Pay rent",
    assigned: date = date(2026, 1, 31),
    rule: RecurrenceRule | None = None,
    scheduled_start: datetime | None = None,
    hard_deadline: datetime | None = None,
    end_date: date | None = None,
    project_id: str | None = None,
):
    return facade.create_recurrent_task(
        TaskCreate(
            title=title,
            assigned_date=assigned,
            scheduled_start=scheduled_start,
            scheduled_duration=30 if scheduled_start is not None else None,
            hard_deadline=hard_deadline,
            project_id=project_id,
        ),
        rule=rule or _rule(),
        materialized_future_count=count,
        end_date=end_date,
    )


def _by_index(facade: MockCalDAVFacade, series_uid: str) -> dict[int, Task]:
    return {t.recurrence_index or 0: t for t in facade.list_series_tasks(series_uid)}


# -----------------------------------------------------------------------------
# Rule arithmetic
# -----------------------------------------------------------------------------


class TestRuleArithmetic:
    """Date arithmetic of the v1 rule set."""

    def test_daily(self) -> None:
        rule = _rule(RecurrenceFrequency.DAILY, 3)
        assert occurrence_date(date(2026, 1, 1), rule, 1) == date(2026, 1, 4)
        assert occurrence_date(date(2026, 1, 1), rule, 0) == date(2026, 1, 1)

    def test_weekly(self) -> None:
        rule = _rule(RecurrenceFrequency.WEEKLY, 1)
        assert occurrence_date(date(2026, 1, 1), rule, 2) == date(2026, 1, 15)

    def test_weekly_interval_two_is_fortnightly(self) -> None:
        """A fortnightly series advances two weeks per occurrence."""
        rule = _rule(RecurrenceFrequency.WEEKLY, 2)
        assert occurrence_date(date(2026, 1, 1), rule, 1) == date(2026, 1, 15)
        assert occurrence_date(date(2026, 1, 1), rule, 2) == date(2026, 1, 29)

    def test_monthly(self) -> None:
        rule = _rule(RecurrenceFrequency.MONTHLY, 1)
        assert occurrence_date(date(2026, 1, 15), rule, 1) == date(2026, 2, 15)

    def test_monthly_clamps_and_returns(self) -> None:
        """January 31 clamps to February 28 and returns to March 31."""
        rule = _rule(RecurrenceFrequency.MONTHLY, 1)
        assert occurrence_date(date(2026, 1, 31), rule, 1) == date(2026, 2, 28)
        assert occurrence_date(date(2026, 1, 31), rule, 2) == date(2026, 3, 31)

    def test_monthly_leap_february(self) -> None:
        rule = _rule(RecurrenceFrequency.MONTHLY, 1)
        assert occurrence_date(date(2024, 1, 31), rule, 1) == date(2024, 2, 29)

    def test_yearly(self) -> None:
        rule = _rule(RecurrenceFrequency.YEARLY, 1)
        assert occurrence_date(date(2026, 3, 1), rule, 1) == date(2027, 3, 1)

    def test_yearly_february_29_clamps(self) -> None:
        rule = _rule(RecurrenceFrequency.YEARLY, 1)
        assert occurrence_date(date(2024, 2, 29), rule, 1) == date(2025, 2, 28)

    def test_add_months_crosses_the_year(self) -> None:
        assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)

    def test_add_years_clamps(self) -> None:
        assert add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)


# -----------------------------------------------------------------------------
# Creation
# -----------------------------------------------------------------------------


class TestCreation:
    """Creating a recurrent task."""

    def test_n1_creates_one_current_and_one_future(self) -> None:
        facade = _facade()
        created = _create(facade, count=1)
        tasks = _by_index(facade, created.series.uid)
        assert sorted(tasks) == [0, 1]
        assert created.task.recurrence_index == 0
        assert created.series.materialized_future_count == 1

    def test_n4_creates_one_current_and_four_futures(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        tasks = _by_index(facade, created.series.uid)
        assert sorted(tasks) == [0, 1, 2, 3, 4]
        assert created.series.highest_materialized_index == 4

    def test_occurrences_carry_series_metadata(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        for task in facade.list_series_tasks(created.series.uid):
            assert task.recurrence_series_uid == created.series.uid
            assert task.recurrence_occurrence is not None
            assert task.recurrence_index is not None

    def test_monthly_dates_are_clamped(self) -> None:
        facade = _facade()
        created = _create(facade, count=3, assigned=date(2026, 1, 31))
        tasks = _by_index(facade, created.series.uid)
        assert tasks[1].assigned_date == date(2026, 2, 28)
        assert tasks[2].assigned_date == date(2026, 3, 31)

    def test_missing_anchor_is_rejected(self) -> None:
        facade = _facade()
        with pytest.raises(ValueError, match="assigned_date"):
            facade.create_recurrent_task(
                TaskCreate(title="No anchor"),
                rule=_rule(),
                materialized_future_count=1,
            )

    def test_horizon_out_of_range_is_rejected(self) -> None:
        facade = _facade()
        with pytest.raises(ValueError, match="materialized_future_count"):
            _create(facade, count=0)
        with pytest.raises(ValueError, match="materialized_future_count"):
            _create(facade, count=101)

    def test_an_end_date_before_the_start_date_is_rejected(self) -> None:
        facade = _facade()
        with pytest.raises(ValueError, match="end date"):
            _create(facade, assigned=date(2026, 1, 31), end_date=date(2025, 12, 31))

    def test_failed_creation_cleans_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A half-written series leaves nothing behind."""
        facade = _facade()
        calls = {"n": 0}
        original = facade._tasks

        def _boom(task):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("boom")
            original[task.uid] = task
            return task

        monkeypatch.setattr(facade._recurrence_store().__class__, "put_task", staticmethod(_boom))
        with pytest.raises(RuntimeError):
            _create(facade, count=4)
        assert facade.list_series() == []
        assert facade.list_tasks() == []


# -----------------------------------------------------------------------------
# Rolling renewal
# -----------------------------------------------------------------------------


class TestRollingRenewal:
    """The window rolls forward when the current occurrence is terminal."""

    def test_completing_the_current_promotes_the_next(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].status == TaskStatus.COMPLETED
        assert tasks[0].completed_at is not None
        state = facade.get_series_state(created.series.uid)
        assert state is not None
        assert state.current_task_uid == tasks[1].uid
        assert len(state.future_occurrences) == 2

    def test_completing_the_current_restores_the_window(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        tasks = _by_index(facade, created.series.uid)
        assert sorted(tasks) == [0, 1, 2, 3]
        assert tasks[3].status == TaskStatus.NEEDS_ACTION

    def test_cancelling_the_current_skips_it(self) -> None:
        facade = _facade()
        created = _create(facade, count=1)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.CANCELED))
        state = facade.get_series_state(created.series.uid)
        assert state is not None
        assert state.current_task_uid != created.task.uid
        assert len(state.future_occurrences) == 1

    def test_cancelling_one_future_fills_the_slot(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        future = _by_index(facade, created.series.uid)[1]
        facade.update_task(future.uid, TaskUpdate(status=TaskStatus.CANCELED))

        state = facade.get_series_state(created.series.uid)
        assert state is not None
        assert len(state.future_occurrences) == 2
        tasks = _by_index(facade, created.series.uid)
        assert tasks[1].status == TaskStatus.CANCELED
        assert tasks[3].status == TaskStatus.NEEDS_ACTION

    def test_reopening_does_not_rewind_the_chain(self) -> None:
        facade = _facade()
        created = _create(facade, count=1)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.NEEDS_ACTION))

        state = facade.get_series_state(created.series.uid)
        assert state is not None
        assert state.current_task_uid != created.task.uid


# -----------------------------------------------------------------------------
# Ownership: the series owns the schedule
# -----------------------------------------------------------------------------


class TestSeriesOwnedFields:
    """The five series-owned fields are read-only on an occurrence."""

    def test_a_change_of_the_assigned_date_is_refused(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        with pytest.raises(ValueError, match="assigned_date"):
            facade.update_task(created.task.uid, TaskUpdate(assigned_date=date(2026, 3, 5)))
        stored = facade.get_task(created.task.uid)
        assert stored is not None
        assert stored.assigned_date == date(2026, 1, 31)

    def test_a_change_of_the_hard_deadline_is_refused(self) -> None:
        facade = _facade()
        created = _create(facade, count=2, hard_deadline=datetime(2026, 1, 31, 12, 0, tzinfo=UTC))
        with pytest.raises(ValueError, match="hard_deadline"):
            facade.update_task(
                created.task.uid,
                TaskUpdate(hard_deadline=datetime(2026, 2, 5, 12, 0, tzinfo=UTC)),
            )

    def test_a_change_of_the_scheduled_start_is_refused(self) -> None:
        facade = _facade()
        created = _create(facade, count=2, scheduled_start=datetime(2026, 1, 31, 9, 0, tzinfo=UTC))
        with pytest.raises(ValueError, match="scheduled_start"):
            facade.update_task(
                created.task.uid,
                TaskUpdate(scheduled_start=datetime(2026, 2, 5, 9, 0, tzinfo=UTC)),
            )

    def test_a_change_of_the_duration_is_refused(self) -> None:
        facade = _facade()
        created = _create(facade, count=2, scheduled_start=datetime(2026, 1, 31, 9, 0, tzinfo=UTC))
        with pytest.raises(ValueError, match="scheduled_duration"):
            facade.update_task(created.task.uid, TaskUpdate(scheduled_duration=60))

    def test_a_change_of_the_project_is_refused(self) -> None:
        from genesis.entities import ProjectCreate

        facade = _facade()
        project = facade.create_project(ProjectCreate(name="Rent"))
        created = _create(facade, count=2, project_id=project.uid)
        with pytest.raises(ValueError, match="project_id"):
            facade.update_task(created.task.uid, TaskUpdate(project_id="p-other"))

    def test_the_error_names_the_series(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        with pytest.raises(ValueError) as excinfo:
            facade.update_task(created.task.uid, TaskUpdate(assigned_date=date(2026, 3, 5)))
        message = str(excinfo.value)
        assert created.series.uid in message
        assert "/productivity/recurrences/" in message

    def test_an_unchanged_write_is_accepted(self) -> None:
        """A client that sends the full object unchanged is not rejected."""
        facade = _facade()
        created = _create(facade, count=2, scheduled_start=datetime(2026, 1, 31, 9, 0, tzinfo=UTC))
        stored = facade.get_task(created.task.uid)
        assert stored is not None
        updated = facade.update_task(
            created.task.uid,
            TaskUpdate(
                title="Pay rent",
                assigned_date=stored.assigned_date,
                scheduled_start=stored.scheduled_start,
                scheduled_duration=stored.scheduled_duration,
                project_id=stored.project_id,
            ),
        )
        assert updated is not None

    def test_an_ordinary_task_keeps_its_dates(self) -> None:
        facade = _facade()
        task = facade.create_task(TaskCreate(title="Plain", assigned_date=date(2026, 1, 31)))
        updated = facade.update_task(task.uid, TaskUpdate(assigned_date=date(2026, 2, 5)))
        assert updated is not None
        assert updated.assigned_date == date(2026, 2, 5)


class TestInstanceEdit:
    """An occurrence edit changes the occurrence and nothing else."""

    def test_the_content_change_stays_on_the_occurrence(self) -> None:
        facade = _facade()
        created = _create(facade, count=3)
        facade.update_task(created.task.uid, TaskUpdate(title="Pay rent (January)"))

        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].title == "Pay rent (January)"
        assert tasks[1].title == "Pay rent"
        assert tasks[2].title == "Pay rent"
        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.template.title == "Pay rent"

    def test_the_priority_change_stays_on_the_occurrence(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.update_task(created.task.uid, TaskUpdate(priority=9))
        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].priority == 9
        assert tasks[1].priority is None

    def test_the_status_change_stays_on_the_occurrence(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].status == TaskStatus.COMPLETED
        assert tasks[1].status == TaskStatus.NEEDS_ACTION


# -----------------------------------------------------------------------------
# Series edit
# -----------------------------------------------------------------------------


class TestSeriesEdit:
    """The series edit is the only way to change a schedule."""

    def test_the_rule_change_re_derives_the_open_occurrences(self) -> None:
        facade = _facade()
        created = _create(facade, count=3)
        facade.update_series(created.series.uid, SeriesUpdate(interval=2))

        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].assigned_date == date(2026, 1, 31)
        assert tasks[1].assigned_date == date(2026, 3, 31)
        assert tasks[2].assigned_date == date(2026, 5, 31)
        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.rule.interval == 2

    def test_the_start_date_change_moves_the_open_occurrences(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.update_series(created.series.uid, SeriesUpdate(start_date=date(2026, 2, 5)))

        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].assigned_date == date(2026, 2, 5)
        assert tasks[1].assigned_date == date(2026, 3, 5)
        assert tasks[2].assigned_date == date(2026, 4, 5)

    def test_the_anchor_change_re_anchors_on_the_current_occurrence(self) -> None:
        facade = _facade()
        created = _create(
            facade,
            count=2,
            hard_deadline=datetime(2026, 2, 5, 12, 0, tzinfo=UTC),
        )
        facade.update_series(created.series.uid, SeriesUpdate(anchor=RecurrenceAnchor.HARD_DEADLINE))

        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.rule.anchor == RecurrenceAnchor.HARD_DEADLINE
        assert series.start_date == date(2026, 2, 5)
        assert series.template.deadline_offset_days == 0
        assert series.template.assigned_offset_days == (date(2026, 1, 31) - date(2026, 2, 5)).days

        tasks = _by_index(facade, created.series.uid)
        assert tasks[1].assigned_date == date(2026, 3, 5) + timedelta(days=-5)
        assert tasks[2].assigned_date == date(2026, 4, 5) + timedelta(days=-5)

    def test_the_anchor_change_is_refused_without_the_field(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        with pytest.raises(ValueError, match="hard_deadline"):
            facade.update_series(created.series.uid, SeriesUpdate(anchor=RecurrenceAnchor.HARD_DEADLINE))

    def test_the_template_change_rewrites_the_open_occurrences(self) -> None:
        facade = _facade()
        created = _create(facade, count=3)
        result = facade.update_series(created.series.uid, SeriesUpdate(title="Pay the rent"))

        assert len(result.updated_task_uids) == 4
        tasks = _by_index(facade, created.series.uid)
        assert all(t.title == "Pay the rent" for t in tasks.values())
        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.template.title == "Pay the rent"

    def test_the_history_is_never_rewritten(self) -> None:
        facade = _facade()
        created = _create(facade, count=3)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        facade.update_series(created.series.uid, SeriesUpdate(title="Pay the rent"))

        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].title == "Pay rent"
        assert tasks[0].status == TaskStatus.COMPLETED
        assert tasks[1].title == "Pay the rent"

    def test_the_project_change_rewrites_the_open_occurrences(self) -> None:
        from genesis.entities import ProjectCreate

        facade = _facade()
        project = facade.create_project(ProjectCreate(name="Rent"))
        created = _create(facade, count=2)
        facade.update_series(created.series.uid, SeriesUpdate(project_id=project.uid))

        tasks = _by_index(facade, created.series.uid)
        assert all(t.project_id == project.uid for t in tasks.values())

    def test_a_cancelled_series_cannot_be_edited(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.cancel_series(created.series.uid)
        with pytest.raises(ValueError, match="cancelled"):
            facade.update_series(created.series.uid, SeriesUpdate(title="Nope"))

    def test_an_unknown_series_is_rejected(self) -> None:
        facade = _facade()
        with pytest.raises(ValueError, match="not found"):
            facade.update_series("r-missing", SeriesUpdate(title="Nope"))

    def test_the_horizon_change_grows_and_shrinks_the_window(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        grown = facade.update_series(created.series.uid, SeriesUpdate(materialized_future_count=4))
        assert len(grown.created_task_uids) == 2
        assert len(facade.list_series_tasks(created.series.uid)) == 5

        shrunk = facade.update_series(created.series.uid, SeriesUpdate(materialized_future_count=1))
        assert len(shrunk.canceled_task_uids) == 3
        tasks = _by_index(facade, created.series.uid)
        assert tasks[2].status == TaskStatus.CANCELED

    def test_the_cancelled_indexes_are_not_resurrected(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        facade.update_series(created.series.uid, SeriesUpdate(materialized_future_count=1))
        facade.update_series(created.series.uid, SeriesUpdate(materialized_future_count=3))
        tasks = _by_index(facade, created.series.uid)
        assert tasks[2].status == TaskStatus.CANCELED
        assert tasks[3].status == TaskStatus.CANCELED
        assert tasks[4].status == TaskStatus.CANCELED
        assert tasks[5].status == TaskStatus.NEEDS_ACTION


# -----------------------------------------------------------------------------
# End date
# -----------------------------------------------------------------------------


class TestEndDate:
    """The optional end date caps the window."""

    def test_no_occurrence_is_materialized_past_the_end_date(self) -> None:
        facade = _facade()
        created = _create(facade, count=4, end_date=date(2026, 3, 31))
        tasks = _by_index(facade, created.series.uid)
        assert sorted(tasks) == [0, 1, 2]
        assert tasks[2].assigned_date == date(2026, 3, 31)

    def test_a_series_that_cannot_renew_finishes(self) -> None:
        """The series finishes when its last occurrence is done."""
        facade = _facade()
        created = _create(facade, count=4, end_date=date(2026, 3, 31))
        for task in list(facade.list_series_tasks(created.series.uid)):
            facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.stop_reason == "finished"
        assert series.active is False

    def test_extending_the_end_date_reactivates_the_series(self) -> None:
        facade = _facade()
        created = _create(facade, count=4, end_date=date(2026, 3, 31))
        for task in list(facade.list_series_tasks(created.series.uid)):
            facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        result = facade.update_series(created.series.uid, SeriesUpdate(end_date=date(2026, 5, 31)))

        assert result.active is True
        # The next occurrence becomes the new current one, and the
        # window is filled again.
        assert len(result.created_task_uids) == 2
        tasks = _by_index(facade, created.series.uid)
        assert tasks[3].assigned_date == date(2026, 4, 30)
        assert tasks[4].assigned_date == date(2026, 5, 31)
        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.end_date == date(2026, 5, 31)
        assert series.stop_reason is None

    def test_clearing_the_end_date_reactivates_the_series(self) -> None:
        facade = _facade()
        created = _create(facade, count=4, end_date=date(2026, 3, 31))
        for task in list(facade.list_series_tasks(created.series.uid)):
            facade.update_task(task.uid, TaskUpdate(status=TaskStatus.COMPLETED))

        result = facade.update_series(created.series.uid, SeriesUpdate(clear_end_date=True))

        assert result.active is True
        assert result.end_date is None
        # The next occurrence plus the tail of the window.
        assert len(result.created_task_uids) == 5

    def test_an_end_date_before_the_start_date_is_refused(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        with pytest.raises(ValueError, match="end date"):
            facade.update_series(created.series.uid, SeriesUpdate(end_date=date(2025, 12, 31)))

    def test_a_shortened_end_date_cancels_the_occurrences_after_it(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        result = facade.update_series(created.series.uid, SeriesUpdate(end_date=date(2026, 2, 28)))

        assert len(result.canceled_task_uids) == 3
        tasks = _by_index(facade, created.series.uid)
        # The end date is inclusive: the occurrence of the 28th stays.
        assert tasks[1].status == TaskStatus.NEEDS_ACTION
        assert tasks[2].status == TaskStatus.CANCELED
        assert tasks[3].status == TaskStatus.CANCELED
        assert tasks[4].status == TaskStatus.CANCELED


# -----------------------------------------------------------------------------
# Deleting an occurrence
# -----------------------------------------------------------------------------


class TestDeleteOccurrence:
    """A deleted occurrence does not come back."""

    def test_a_deleted_future_occurrence_is_not_refilled(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        future = _by_index(facade, created.series.uid)[2]
        facade.delete_task(future.uid)

        tasks = _by_index(facade, created.series.uid)
        assert 2 not in tasks
        # The window is one smaller: the delete does not refill it.
        state = facade.get_series_state(created.series.uid)
        assert state is not None
        assert len(state.future_occurrences) == 3

    def test_the_deleted_index_is_not_reused(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        future = _by_index(facade, created.series.uid)[2]
        facade.delete_task(future.uid)

        # The natural renewal materializes the next index, never the
        # deleted one.
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        tasks = _by_index(facade, created.series.uid)
        assert 2 not in tasks
        assert 5 in tasks

    def test_deleting_the_current_occurrence_promotes_the_next(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        facade.delete_task(created.task.uid)

        state = facade.get_series_state(created.series.uid)
        assert state is not None
        assert state.current_task_uid is not None
        assert state.current_task_uid != created.task.uid
        tasks = _by_index(facade, created.series.uid)
        assert tasks[1].recurrence_index == 1

    def test_the_series_keeps_running_after_a_delete(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        future = _by_index(facade, created.series.uid)[2]
        facade.delete_task(future.uid)

        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.active is True
        assert series.stop_reason is None


# -----------------------------------------------------------------------------
# Horizon resizing
# -----------------------------------------------------------------------------


class TestHorizonResizing:
    """Changing the materialized horizon of a running series."""

    def test_growing_from_one_to_four_creates_three(self) -> None:
        facade = _facade()
        created = _create(facade, count=1)
        result = facade.resize_series(created.series.uid, 4)

        assert len(result.created_task_uids) == 3
        assert result.materialized_future_count == 4
        assert len(result.future_task_uids) == 4
        assert sorted(_by_index(facade, created.series.uid)) == [0, 1, 2, 3, 4]

    def test_growing_from_two_to_four_creates_two(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        result = facade.resize_series(created.series.uid, 4)

        assert len(result.created_task_uids) == 2
        assert len(result.future_task_uids) == 4

    def test_shrinking_from_four_to_one_cancels_the_farthest_three(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        result = facade.resize_series(created.series.uid, 1)

        assert len(result.canceled_task_uids) == 3
        assert len(result.future_task_uids) == 1
        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].status == TaskStatus.NEEDS_ACTION
        assert tasks[1].status == TaskStatus.NEEDS_ACTION
        for index in (2, 3, 4):
            assert tasks[index].status == TaskStatus.CANCELED

    def test_shrinking_keeps_the_history(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        facade.resize_series(created.series.uid, 1)
        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].status == TaskStatus.COMPLETED
        assert tasks[0].completed_at is not None

    def test_expanding_after_a_reduction_does_not_resurrect(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        facade.resize_series(created.series.uid, 1)
        facade.resize_series(created.series.uid, 3)
        tasks = _by_index(facade, created.series.uid)
        assert tasks[2].status == TaskStatus.CANCELED
        assert tasks[3].status == TaskStatus.CANCELED
        assert tasks[4].status == TaskStatus.CANCELED
        assert tasks[5].status == TaskStatus.NEEDS_ACTION

    def test_resizing_to_the_same_count_is_a_noop(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        result = facade.resize_series(created.series.uid, 2)
        assert result.created_task_uids == []
        assert result.canceled_task_uids == []

    def test_resizing_an_inactive_series_is_rejected(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.cancel_series(created.series.uid)
        with pytest.raises(ValueError, match="not active"):
            facade.resize_series(created.series.uid, 4)

    def test_reduction_removes_the_shadow_event(self) -> None:
        facade = _facade()
        created = _create(
            facade,
            count=2,
            scheduled_start=datetime(2026, 1, 31, 9, 0, tzinfo=UTC),
        )
        shadow_uids = {event.uid for event in facade.list_events()}
        assert len(shadow_uids) == 3

        facade.resize_series(created.series.uid, 1)
        remaining = {event.uid for event in facade.list_events()}
        assert len(remaining) == 2


# -----------------------------------------------------------------------------
# Cancellation
# -----------------------------------------------------------------------------


class TestCancelSeries:
    """Cancelling a series."""

    def test_cancelling_cancels_every_future(self) -> None:
        facade = _facade()
        created = _create(facade, count=3)
        result = facade.cancel_series(created.series.uid)

        assert len(result.canceled_uids) == 3
        assert result.active is False
        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].status == TaskStatus.NEEDS_ACTION
        for index in (1, 2, 3):
            assert tasks[index].status == TaskStatus.CANCELED

    def test_cancelling_keeps_the_history(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        facade.cancel_series(created.series.uid)
        tasks = _by_index(facade, created.series.uid)
        assert tasks[0].status == TaskStatus.COMPLETED
        assert tasks[0].completed_at is not None

    def test_cancelling_is_idempotent(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        first = facade.cancel_series(created.series.uid)
        second = facade.cancel_series(created.series.uid)
        assert len(first.canceled_uids) == 2
        assert second.canceled_uids == []
        assert second.active is False

    def test_cancelling_an_unknown_series_is_rejected(self) -> None:
        facade = _facade()
        with pytest.raises(ValueError, match="not found"):
            facade.cancel_series("r-missing")

    def test_a_cancelled_series_does_not_renew(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.cancel_series(created.series.uid)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        assert sorted(_by_index(facade, created.series.uid)) == [0, 1, 2]

    def test_a_cancelled_series_keeps_its_stop_reason(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.cancel_series(created.series.uid)
        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.stop_reason == "cancelled"
        assert series.active is False


# -----------------------------------------------------------------------------
# Series summary
# -----------------------------------------------------------------------------


class TestSeriesSummary:
    """The row model of the series table."""

    def test_the_summary_reports_the_rule_and_the_next_occurrence(self) -> None:
        facade = _facade()
        created = _create(facade, count=4)
        summaries = facade.list_series_summaries()
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.uid == created.series.uid
        assert summary.title == "Pay rent"
        assert summary.frequency == RecurrenceFrequency.MONTHLY
        assert summary.interval == 1
        assert summary.anchor == RecurrenceAnchor.ASSIGNED_DATE
        assert summary.start_date == date(2026, 1, 31)
        assert summary.end_date is None
        assert summary.materialized_future_count == 4
        assert summary.current_task_uid == created.task.uid
        assert summary.current_occurrence_date == date(2026, 1, 31)
        assert summary.next_occurrence_date == date(2026, 2, 28)
        assert summary.open_occurrence_count == 5
        assert summary.history_count == 0
        assert summary.active is True
        assert summary.stop_reason is None

    def test_the_summary_counts_the_history(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        summary = facade.list_series_summaries()[0]
        assert summary.history_count == 1
        assert summary.open_occurrence_count == 3

    def test_the_summary_reports_a_cancelled_series(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.cancel_series(created.series.uid)
        summary = facade.list_series_summaries()[0]
        assert summary.active is False
        assert summary.stop_reason == "cancelled"


# -----------------------------------------------------------------------------
# Identity and retry safety
# -----------------------------------------------------------------------------


class TestIdentity:
    """Deterministic occurrence identity."""

    def test_uid_is_stable_for_the_same_logical_occurrence(self) -> None:
        first = recurrence_occurrence_uid("r-abc123", 3)
        second = recurrence_occurrence_uid("r-abc123", 3)
        assert first == second
        assert first.startswith("t-")
        assert len(first) == 8

    def test_uid_differs_per_series_and_index(self) -> None:
        assert recurrence_occurrence_uid("r-abc123", 3) != recurrence_occurrence_uid("r-abc123", 4)
        assert recurrence_occurrence_uid("r-abc123", 3) != recurrence_occurrence_uid("r-def456", 3)

    def test_retry_after_a_partial_write_creates_no_duplicates(self) -> None:
        """A retry resumes at the first missing index."""
        facade = _facade()
        created = _create(facade, count=3)
        store = facade._recurrence_store()
        series = store.get_series(created.series.uid)

        # Simulate a partial write: drop the tail occurrence.
        dropped = max(t.recurrence_index or 0 for t in facade.list_series_tasks(created.series.uid))
        facade._tasks.pop(recurrence_occurrence_uid(created.series.uid, dropped))

        from genesis.caldav import recurrence as recurrence_module

        recurrence_module.reconcile_series(store, created.series.uid)
        tasks = facade.list_series_tasks(created.series.uid)
        indexes = sorted(t.recurrence_index or 0 for t in tasks)
        assert indexes == sorted(set(indexes))
        assert len(indexes) == len(set(indexes))
        assert series is not None


class TestShadows:
    """Shadow calendar events of the occurrences."""

    def test_scheduled_occurrences_get_a_shadow(self) -> None:
        facade = _facade()
        created = _create(
            facade,
            count=2,
            scheduled_start=datetime(2026, 1, 31, 9, 0, tzinfo=UTC),
        )
        events = {event.uid for event in facade.list_events()}
        for task in facade.list_series_tasks(created.series.uid):
            assert task.uid in events

    def test_cancelling_the_series_removes_the_future_shadows(self) -> None:
        facade = _facade()
        created = _create(
            facade,
            count=2,
            scheduled_start=datetime(2026, 1, 31, 9, 0, tzinfo=UTC),
        )
        facade.cancel_series(created.series.uid)
        events = {event.uid for event in facade.list_events()}
        assert created.task.uid in events
        assert len(events) == 1


class TestScheduledTime:
    """The local wall-clock time of a scheduled occurrence."""

    def test_scheduled_time_survives_a_dst_transition(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The occurrence keeps its local time across a DST change.

        The series starts in January (CET, UTC+1) and continues into
        July (CEST, UTC+2). The scheduled time stays 09:00 local, so
        the stored UTC instant moves by an hour.
        """
        import time as _time

        monkeypatch.setenv("TZ", "Europe/Berlin")
        _time.tzset()
        try:
            facade = _facade()
            created = _create(
                facade,
                count=6,
                scheduled_start=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
                rule=_rule(RecurrenceFrequency.MONTHLY, 1, RecurrenceAnchor.SCHEDULED_START),
            )
            tasks = _by_index(facade, created.series.uid)
            first = tasks[0].scheduled_start
            assert first is not None
            expected_local = (first.astimezone().hour, first.astimezone().minute)
            for index in range(0, 7):
                start = tasks[index].scheduled_start
                assert start is not None
                local = start.astimezone()
                assert (local.hour, local.minute) == expected_local, f"index {index} moved to {local}"
            # January runs on CET (UTC+1) and July on CEST (UTC+2).
            # The local time is 10:00 in both, so the July occurrence
            # is stored an hour earlier in UTC than the January one.
            july = tasks[6].scheduled_start
            assert july is not None
            assert july == datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
        finally:
            monkeypatch.undo()
            _time.tzset()


class TestSeriesListing:
    """Listing the tasks of a series."""

    def test_list_by_series_uid(self) -> None:
        facade = _facade()
        created = _create(facade, count=2)
        facade.create_task(TaskCreate(title="Unrelated"))
        listed = facade.list_tasks(series_uid=created.series.uid)
        assert len(listed) == 3
        assert all(t.recurrence_series_uid == created.series.uid for t in listed)

    def test_list_series(self) -> None:
        facade = _facade()
        created = _create(facade, count=1)
        series = facade.list_series()
        assert len(series) == 1
        assert series[0].uid == created.series.uid


def test_daily_series_with_two_days_elapsed() -> None:
    """A daily series advances one day per occurrence."""
    facade = _facade()
    created = _create(
        facade,
        count=2,
        assigned=date(2026, 1, 1),
        rule=_rule(RecurrenceFrequency.DAILY, 1),
    )
    tasks = _by_index(facade, created.series.uid)
    assert tasks[1].assigned_date == date(2026, 1, 2)
    assert tasks[2].assigned_date == date(2026, 1, 3)


def test_weekly_series_with_fortnight_interval() -> None:
    """A fortnightly series advances two weeks per occurrence."""
    facade = _facade()
    _create(
        facade,
        count=2,
        assigned=date(2026, 1, 1),
        rule=_rule(RecurrenceFrequency.WEEKLY, 2),
    )
    series = facade.list_series()[0]
    tasks = _by_index(facade, series.uid)
    assert tasks[1].assigned_date == date(2026, 1, 15)


def test_timedelta_import_is_used() -> None:
    """Guard against an unused import drifting in."""
    assert timedelta(days=1) == timedelta(hours=24)


class TestProjectCascade:
    """A cancelled project must not leave an active series behind."""

    def test_cancelling_a_project_deactivates_its_series(self) -> None:
        from genesis.entities import ProjectCreate, ProjectStatus, ProjectUpdate

        facade = _facade()
        project = facade.create_project(ProjectCreate(name="Rent"))
        created = facade.create_recurrent_task(
            TaskCreate(title="Pay rent", assigned_date=date(2026, 1, 31), project_id=project.uid),
            rule=_rule(),
            materialized_future_count=2,
        )
        facade.update_project(project.uid, ProjectUpdate(status=ProjectStatus.CANCELED))

        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.active is False
        tasks = facade.list_series_tasks(created.series.uid)
        assert all(t.status == TaskStatus.CANCELED for t in tasks)
        # No replacement occurrence was materialized over cancelled work.
        assert len(tasks) == 3

    def test_deleting_a_project_keeps_the_series_alive(self) -> None:
        from genesis.entities import ProjectCreate

        facade = _facade()
        project = facade.create_project(ProjectCreate(name="Rent"))
        created = facade.create_recurrent_task(
            TaskCreate(title="Pay rent", assigned_date=date(2026, 1, 31), project_id=project.uid),
            rule=_rule(),
            materialized_future_count=2,
        )
        facade.delete_project(project.uid)

        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.active is True
        tasks = facade.list_series_tasks(created.series.uid)
        assert all(t.project_id is None for t in tasks)

    def test_deleting_a_project_clears_the_series_template(self) -> None:
        """The next materialized occurrence must not re-attach.

        The series stays active, but its template must not point at
        the deleted project any more.
        """
        from genesis.entities import ProjectCreate

        facade = _facade()
        project = facade.create_project(ProjectCreate(name="Rent"))
        created = facade.create_recurrent_task(
            TaskCreate(title="Pay rent", assigned_date=date(2026, 1, 31), project_id=project.uid),
            rule=_rule(),
            materialized_future_count=1,
        )
        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.template.project_id == project.uid

        facade.delete_project(project.uid)

        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.active is True
        assert series.template.project_id is None

        # Completing the current materializes a new tail occurrence.
        # It must not carry the deleted project.
        facade.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
        tasks = facade.list_series_tasks(created.series.uid)
        assert len(tasks) == 3
        assert all(t.project_id is None for t in tasks)
        newest = max(tasks, key=lambda t: t.recurrence_index or 0)
        assert newest.project_id != project.uid


class TestUidCollision:
    """A deterministic occurrence UID must not overwrite another task."""

    def test_a_uid_used_by_another_task_is_rejected(self) -> None:
        facade = _facade()
        created = _create(facade, count=1)
        store = facade._recurrence_store()
        series = store.get_series(created.series.uid)
        assert series is not None

        # Occupy the UID the next occurrence would use with a task
        # that belongs to a different series.
        clash_uid = recurrence_occurrence_uid(created.series.uid, 2)
        facade._tasks[clash_uid] = Task(
            uid=clash_uid,
            title="Somebody else",
            recurrence_series_uid="r-other",
            recurrence_index=7,
        )

        with pytest.raises(ValueError, match="already used"):
            facade.resize_series(created.series.uid, 3)


class TestLocalTimeAfterEdit:
    """The local clock time of a touched datetime field follows the edit."""

    def _scheduled_series(self, facade: MockCalDAVFacade, hour: int = 12, count: int = 2):
        """Create a series anchored on the scheduled start."""
        return facade.create_recurrent_task(
            TaskCreate(
                title="Pay rent",
                assigned_date=date(2026, 1, 15),
                scheduled_start=datetime(2026, 1, 15, hour, 0, tzinfo=UTC),
                scheduled_duration=30,
            ),
            rule=_rule(RecurrenceFrequency.MONTHLY, 1, RecurrenceAnchor.SCHEDULED_START),
            materialized_future_count=count,
        )

    def test_the_local_time_of_the_series_template_is_kept(self) -> None:
        """The template keeps the local wall-clock time of the series."""
        facade = _facade()
        created = self._scheduled_series(facade, hour=12, count=2)
        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.template.scheduled_local_time == time(12, 0)

    def test_a_re_anchor_moves_the_local_time(self) -> None:
        """Re-anchoring on another field recomputes the local times."""
        facade = _facade()
        created = self._scheduled_series(facade, hour=12, count=2)
        facade.update_series(created.series.uid, SeriesUpdate(anchor=RecurrenceAnchor.ASSIGNED_DATE))

        series = facade.get_series(created.series.uid)
        assert series is not None
        assert series.template.scheduled_local_time == time(12, 0)
        tasks = _by_index(facade, created.series.uid)
        for index in (1, 2):
            start = tasks[index].scheduled_start
            assert start is not None
            assert start.astimezone().timetz().replace(tzinfo=None) == time(12, 0)
