"""Unit tests for :mod:`genesis.stats`.

Pure-function tests over synthetic ``TimeLogEntry`` lists. No CalDAV,
no FastAPI. The API router has its own coverage in
``tests/test_api_unit.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from genesis.category.enums import TimeLogCategory
from genesis.entities import TimeLogEntry, TimeLogMode, TimeLogStatus
from genesis.stats import (
    DailyPoint,
    Kpis,
    Period,
    RecentSession,
    Rollup,
    compute_rollup,
    duration_seconds,
    period_bounds,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _entry(
    *,
    uid: str,
    start: datetime,
    due: datetime | None,
    label: str = "session",
    task_id: str | None = None,
    mode: TimeLogMode = TimeLogMode.COUNTDOWN,
    status: TimeLogStatus = TimeLogStatus.COMPLETED,
    category: TimeLogCategory = TimeLogCategory.OTHER,
) -> TimeLogEntry:
    return TimeLogEntry(
        uid=uid,
        label=label,
        start=start,
        due=due,
        completed_at=due,
        status=status,
        mode=mode,
        task_id=task_id,
        category=category,
    )


# ---------------------------------------------------------------------------
# period_bounds
# ---------------------------------------------------------------------------


def test_period_bounds_this_week_starts_monday() -> None:
    # Wed 2026-09-09 -> week starts Mon 2026-09-07, end is start of Thu 2026-09-10.
    bounds = period_bounds(Period.THIS_WEEK, date(2026, 9, 9))
    assert bounds.start == datetime(2026, 9, 7, tzinfo=UTC)
    assert bounds.end == datetime(2026, 9, 10, tzinfo=UTC)


def test_period_bounds_this_week_starts_today_when_monday() -> None:
    # Mon 2026-09-07 -> week starts same day.
    bounds = period_bounds(Period.THIS_WEEK, date(2026, 9, 7))
    assert bounds.start == datetime(2026, 9, 7, tzinfo=UTC)
    assert bounds.end == datetime(2026, 9, 8, tzinfo=UTC)


def test_period_bounds_this_month_starts_first() -> None:
    bounds = period_bounds(Period.THIS_MONTH, date(2026, 9, 9))
    assert bounds.start == datetime(2026, 9, 1, tzinfo=UTC)
    assert bounds.end == datetime(2026, 9, 10, tzinfo=UTC)


def test_period_bounds_last_30_days_is_29_back() -> None:
    # 2026-09-09 - 29 days = 2026-08-11.
    bounds = period_bounds(Period.LAST_30_DAYS, date(2026, 9, 9))
    assert bounds.start == datetime(2026, 8, 11, tzinfo=UTC)
    assert bounds.end == datetime(2026, 9, 10, tzinfo=UTC)


def test_period_bounds_all_time_is_open_ended() -> None:
    bounds = period_bounds(Period.ALL_TIME, date(2026, 9, 9))
    assert bounds.start == datetime(1970, 1, 1, tzinfo=UTC)
    assert bounds.end is None


# ---------------------------------------------------------------------------
# duration_seconds
# ---------------------------------------------------------------------------


def test_duration_seconds_subtracts_due_minus_start() -> None:
    e = _entry(
        uid="l1",
        start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
        due=datetime(2026, 9, 7, 10, 25, tzinfo=UTC),
    )
    assert duration_seconds(e) == 1500.0


def test_duration_seconds_zero_when_due_missing() -> None:
    e = _entry(
        uid="l1",
        start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
        due=None,
    )
    assert duration_seconds(e) == 0.0


def test_duration_seconds_zero_when_start_missing() -> None:
    # ``start`` is required at the Pydantic layer; use ``model_construct``
    # to fabricate an entry where it's missing without validation.
    e = TimeLogEntry.model_construct(
        uid="l1",
        label="x",
        start=None,
        due=None,
        mode=TimeLogMode.COUNTDOWN,
    )
    assert duration_seconds(e) == 0.0


# ---------------------------------------------------------------------------
# compute_rollup: KPIs
# ---------------------------------------------------------------------------


def test_kpis_sum_total_and_count() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
        ),
        _entry(
            uid="l2",
            start=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 12, 15, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.kpis.total_minutes == 45.0
    assert rollup.kpis.session_count == 2
    assert rollup.kpis.avg_minutes == 22.5


def test_kpis_avg_zero_when_no_sessions() -> None:
    rollup = compute_rollup(
        [],
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.kpis.session_count == 0
    assert rollup.kpis.total_minutes == 0.0
    assert rollup.kpis.avg_minutes == 0.0
    assert rollup.kpis.tasks_touched == 0


def test_kpis_tasks_touched_counts_unique_task_ids() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
            task_id="t1",
        ),
        _entry(
            uid="l2",
            start=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 12, 15, tzinfo=UTC),
            task_id="t1",
        ),
        _entry(
            uid="l3",
            start=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 10, 45, tzinfo=UTC),
            task_id="t2",
        ),
        _entry(
            uid="l4",
            start=datetime(2026, 9, 8, 11, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 11, 15, tzinfo=UTC),
            task_id=None,
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={"t1": "Task 1", "t2": "Task 2"},
    )
    assert rollup.kpis.session_count == 4
    assert rollup.kpis.tasks_touched == 2


# ---------------------------------------------------------------------------
# compute_rollup: period filtering
# ---------------------------------------------------------------------------


def test_entries_outside_period_are_excluded() -> None:
    entries = [
        # Last week — outside this-week window.
        _entry(
            uid="old",
            start=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
        ),
        # This week.
        _entry(
            uid="new",
            start=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.kpis.session_count == 1
    assert rollup.kpis.total_minutes == 30.0


def test_all_time_has_no_upper_bound() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2024, 1, 5, 10, 0, tzinfo=UTC),
            due=datetime(2024, 1, 5, 10, 30, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.ALL_TIME,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.kpis.session_count == 1


# ---------------------------------------------------------------------------
# compute_rollup: daily bucketing
# ---------------------------------------------------------------------------


def test_daily_buckets_one_point_per_day_in_period() -> None:
    # THIS_WEEK as of 2026-09-09 covers 2026-09-07..2026-09-09 (3 days).
    rollup = compute_rollup(
        [],
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    days = [p.day for p in rollup.daily]
    assert days == [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]


def test_daily_zero_days_present_in_response() -> None:
    # 30 days with only one session should still produce 30 points.
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 8, 11, 10, 0, tzinfo=UTC),
            due=datetime(2026, 8, 11, 10, 30, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.LAST_30_DAYS,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert len(rollup.daily) == 30
    total_minutes = sum(p.minutes for p in rollup.daily)
    assert total_minutes == 30.0


def test_daily_bucketing_sums_minutes_per_local_day() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
        ),
        _entry(
            uid="l2",
            start=datetime(2026, 9, 7, 14, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 14, 45, tzinfo=UTC),
        ),
        _entry(
            uid="l3",
            start=datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 9, 15, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    by_day = {p.day: p.minutes for p in rollup.daily}
    assert by_day[date(2026, 9, 7)] == 75.0
    assert by_day[date(2026, 9, 8)] == 15.0
    assert by_day[date(2026, 9, 9)] == 0.0


# ---------------------------------------------------------------------------
# compute_rollup: by-task grouping
# ---------------------------------------------------------------------------


def test_by_task_groups_by_title_and_orders_by_total_desc() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 11, 0, tzinfo=UTC),  # 1h
            task_id="t1",
        ),
        _entry(
            uid="l2",
            start=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 12, 30, tzinfo=UTC),  # 30m
            task_id="t2",
        ),
        _entry(
            uid="l3",
            start=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),  # 30m
            task_id="t2",
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={"t1": "Big Task", "t2": "Small Task"},
    )
    assert [b.title for b in rollup.by_task] == ["Big Task", "Small Task"]
    big = rollup.by_task[0]
    assert big.count == 1
    assert big.total_seconds == 3600.0
    small = rollup.by_task[1]
    assert small.count == 2
    assert small.total_seconds == 3600.0
    assert small.avg_seconds == 1800.0


def test_by_task_excludes_unaligned_sessions() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
            task_id=None,
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.by_task == []
    # The session still counts in KPIs.
    assert rollup.kpis.session_count == 1
    assert rollup.kpis.tasks_touched == 0


def test_by_task_marks_deleted_tasks() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
            task_id="t-deleted",
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},  # no entry for t-deleted
    )
    assert [b.title for b in rollup.by_task] == ["(deleted)"]


def test_by_task_caps_at_top_n() -> None:
    entries = []
    for i in range(15):
        # 15 sessions, one per hour across a 15-hour span — start
        # varies so the sort is unambiguous.
        day_offset, hour = divmod(10 + i, 24)
        entries.append(
            _entry(
                uid=f"l{i}",
                start=datetime(2026, 9, 7 + day_offset, hour, 0, tzinfo=UTC),
                due=datetime(2026, 9, 7 + day_offset, hour, 1, tzinfo=UTC),
                task_id=f"t{i}",
            )
        )
    task_titles = {f"t{i}": f"Task {i}" for i in range(15)}
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles=task_titles,
        top_n=5,
    )
    assert len(rollup.by_task) == 5


def test_by_task_collides_on_same_title() -> None:
    """Two tasks sharing a title merge into a single group (UI's
    groupby semantics)."""
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
            task_id="t1",
        ),
        _entry(
            uid="l2",
            start=datetime(2026, 9, 7, 11, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 11, 15, tzinfo=UTC),
            task_id="t2",
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={"t1": "Same", "t2": "Same"},
    )
    assert len(rollup.by_task) == 1
    assert rollup.by_task[0].count == 2
    assert rollup.by_task[0].total_seconds == 2700.0


# ---------------------------------------------------------------------------
# compute_rollup: recent sessions
# ---------------------------------------------------------------------------


def test_recent_sessions_newest_first_capped() -> None:
    entries = []
    for i in range(25):
        # Each session starts on a unique day so the desc sort is
        # unambiguous (no ties on ``start``).
        entries.append(
            _entry(
                uid=f"l{i:02d}",
                start=datetime(2026, 9, 1 + i, 10, 0, tzinfo=UTC),
                due=datetime(2026, 9, 1 + i, 10, 30, tzinfo=UTC),
                label=f"session-{i:02d}",
            )
        )
    rollup = compute_rollup(
        entries,
        period=Period.ALL_TIME,
        today=date(2026, 9, 30),
        task_titles={},
        recent_n=5,
    )
    assert len(rollup.recent) == 5
    # Newest first.
    assert rollup.recent[0].uid == "l24"
    assert rollup.recent[-1].uid == "l20"


def test_recent_session_task_title_empty_for_unaligned() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
            task_id=None,
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.recent[0].task_title == ""


def test_recent_session_task_title_resolved_via_lookup() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
            task_id="t1",
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={"t1": "Foo"},
    )
    assert rollup.recent[0].task_title == "Foo"


def test_recent_respects_period_filter() -> None:
    """Lock in the contract: ``recent`` is filtered to the period.

    There is no period-independent mode; pick ``ALL_TIME`` to widen
    the window. If a future change reintroduces a "recent ignores
    period" path, this test will fail.
    """
    entries = [
        _entry(
            uid="old",
            start=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
        ),
        _entry(
            uid="new",
            start=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
        recent_n=20,
    )
    assert [s.uid for s in rollup.recent] == ["new"]


def test_recent_includes_only_in_period_even_when_window_is_small() -> None:
    """Cross-section sanity: KPIs, recent, and by-task all use the
    same period filter. Counts must agree."""
    entries = [
        # In this-week window.
        _entry(
            uid="in",
            start=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
        ),
        # Out of this-week window.
        _entry(
            uid="out",
            start=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
        recent_n=20,
    )
    assert rollup.kpis.session_count == 1
    assert [s.uid for s in rollup.recent] == ["in"]
    assert rollup.by_task == []  # unaligned


# ---------------------------------------------------------------------------
# compute_rollup: shape
# ---------------------------------------------------------------------------


def test_rollup_period_is_echoed() -> None:
    rollup = compute_rollup(
        [],
        period=Period.ALL_TIME,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.period == Period.ALL_TIME


def test_rollup_is_a_dataclass() -> None:
    entries = [
        _entry(
            uid="l1",
            start=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 7, 10, 30, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert isinstance(rollup, Rollup)
    assert isinstance(rollup.kpis, Kpis)
    assert isinstance(rollup.daily, list)
    assert isinstance(rollup.daily[0], DailyPoint)
    assert isinstance(rollup.by_task, list)
    assert isinstance(rollup.recent, list)
    assert isinstance(rollup.recent[0], RecentSession)
    assert isinstance(rollup.by_task, list) or rollup.by_task == []


def test_rollup_with_no_entries_returns_empty_lists() -> None:
    rollup = compute_rollup(
        [],
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.by_task == []
    assert rollup.recent == []
    # Daily still spans the period.
    assert len(rollup.daily) > 0


def test_all_time_with_no_data_returns_empty_daily() -> None:
    """All-time with no entries must not emit a 20K-point zero series."""
    rollup = compute_rollup(
        [],
        period=Period.ALL_TIME,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert rollup.daily == []
    assert rollup.by_task == []
    assert rollup.recent == []


def test_all_time_daily_bounds_to_data_span() -> None:
    """All-time with data bounds daily by earliest/latest data day."""
    entries = [
        _entry(
            uid="l1",
            start=datetime(2024, 1, 5, 10, 0, tzinfo=UTC),
            due=datetime(2024, 1, 5, 10, 30, tzinfo=UTC),
        ),
        _entry(
            uid="l2",
            start=datetime(2024, 1, 8, 10, 0, tzinfo=UTC),
            due=datetime(2024, 1, 8, 11, 0, tzinfo=UTC),
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.ALL_TIME,
        today=date(2026, 9, 9),
        task_titles={},
    )
    days = [p.day for p in rollup.daily]
    assert days[0] == date(2024, 1, 5)
    assert days[-1] == date(2024, 1, 8)
    assert len(rollup.daily) == 4


# ---------------------------------------------------------------------------
# Category breakdown
# ---------------------------------------------------------------------------


def test_kpis_minutes_by_category_sums_to_total() -> None:
    entries = [
        _entry(
            uid="w",
            start=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
            category=TimeLogCategory.WORK,
        ),
        _entry(
            uid="p",
            start=datetime(2026, 9, 8, 11, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 11, 15, tzinfo=UTC),
            category=TimeLogCategory.PERSONAL,
        ),
        _entry(
            uid="x",
            start=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 13, 0, tzinfo=UTC),
            category=TimeLogCategory.WORKOUT,
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    secs = rollup.kpis.minutes_by_category
    assert sum(secs.values()) == rollup.kpis.total_minutes
    assert secs[TimeLogCategory.WORK] == 30.0
    assert secs[TimeLogCategory.PERSONAL] == 15.0
    assert secs[TimeLogCategory.WORKOUT] == 60.0
    assert secs[TimeLogCategory.PLAY] == 0.0
    assert secs[TimeLogCategory.OTHER] == 0.0


def test_kpis_minutes_by_category_includes_zero_entries() -> None:
    """Every category is a key, even if no sessions had that category."""
    rollup = compute_rollup(
        [],
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    assert set(rollup.kpis.minutes_by_category) == set(TimeLogCategory)
    assert all(v == 0.0 for v in rollup.kpis.minutes_by_category.values())


def test_daily_carries_per_category_minutes() -> None:
    entries = [
        _entry(
            uid="w1",
            start=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
            category=TimeLogCategory.WORK,
        ),
        _entry(
            uid="x1",
            start=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 12, 30, tzinfo=UTC),
            category=TimeLogCategory.WORKOUT,
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    by_day = {p.day: p for p in rollup.daily}
    day = date(2026, 9, 8)
    assert day in by_day
    mins = by_day[day].minutes_by_category
    assert mins[TimeLogCategory.WORK] == 30.0
    assert mins[TimeLogCategory.WORKOUT] == 30.0
    assert mins[TimeLogCategory.PERSONAL] == 0.0
    assert by_day[day].minutes == sum(mins.values())


def test_by_category_sorts_descending_and_includes_all_categories() -> None:
    entries = [
        _entry(
            uid="w",
            start=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
            category=TimeLogCategory.WORK,
        ),
        _entry(
            uid="x",
            start=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
            due=datetime(2026, 9, 8, 13, 0, tzinfo=UTC),
            category=TimeLogCategory.WORKOUT,
        ),
    ]
    rollup = compute_rollup(
        entries,
        period=Period.THIS_WEEK,
        today=date(2026, 9, 9),
        task_titles={},
    )
    by_cat = rollup.by_category
    assert isinstance(by_cat, list)
    assert len(by_cat) == len(list(TimeLogCategory))
    totals = [b.total_seconds for b in by_cat]
    assert totals == sorted(totals, reverse=True)
    assert by_cat[0].category == TimeLogCategory.WORKOUT  # 3600s > 1800s
    assert by_cat[1].category == TimeLogCategory.WORK
    # Categories with zero time still appear, just at the tail.
    zeros = {b.category for b in by_cat if b.total_seconds == 0}
    assert TimeLogCategory.PLAY in zeros
    assert TimeLogCategory.PERSONAL in zeros
    assert TimeLogCategory.OTHER in zeros
