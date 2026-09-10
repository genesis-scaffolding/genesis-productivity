"""Statistics rollup logic.

Pure aggregation over completed time log entries. Used by the
Streamlit Statistics page and the FastAPI ``/productivity/statistics``
endpoint. No imports from the UI or the API package — those modules
are callers, not dependencies.

Duration convention
-------------------
The recorded duration for a completed session is ``due - start``
(ADR-014): countdown auto-stopped at the target keeps the target;
manually stopped countdowns and count-ups reflect actual elapsed.
None on either side contributes zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as _time
from enum import StrEnum

from ..entities import TimeLogEntry
from ..tz import to_local, to_utc


class Period(StrEnum):
    """Period selector for the rollup. Values match the wire enum."""

    THIS_WEEK = "this-week"
    THIS_MONTH = "this-month"
    LAST_30_DAYS = "last-30-days"
    ALL_TIME = "all-time"


PERIOD_DEFAULT = Period.THIS_WEEK


@dataclass(frozen=True)
class PeriodBounds:
    """UTC ``(start, end)`` for a period. ``end=None`` means open-ended.

    For non-all-time periods ``end`` is the start of *tomorrow* in the
    system's local zone, expressed in UTC. Comparison against
    ``entry.start`` uses ``>=`` so an entry exactly on the boundary is
    excluded (the boundary is the start of the next day, not the end
    of today).
    """

    start: datetime
    end: datetime | None


def period_bounds(period: Period, today: date) -> PeriodBounds:
    """Return UTC bounds for ``period`` relative to ``today``.

    Local midnight is converted to UTC via :func:`genesis.tz.to_utc`,
    so the boundary tracks the system's local timezone (which is what
    the UI does).
    """
    today_start = to_utc(_time_combine(today, _time.min))
    if period == Period.THIS_WEEK:
        week_start_date = today - timedelta(days=today.weekday())
        start = to_utc(_time_combine(week_start_date, _time.min))
        return PeriodBounds(start=start, end=today_start + timedelta(days=1))
    if period == Period.THIS_MONTH:
        month_start_date = today.replace(day=1)
        start = to_utc(_time_combine(month_start_date, _time.min))
        return PeriodBounds(start=start, end=today_start + timedelta(days=1))
    if period == Period.LAST_30_DAYS:
        start = today_start - timedelta(days=29)
        return PeriodBounds(start=start, end=today_start + timedelta(days=1))
    if period == Period.ALL_TIME:
        return PeriodBounds(start=datetime(1970, 1, 1, tzinfo=UTC), end=None)
    raise ValueError(f"Unknown period: {period}")


def _time_combine(d: date, t: _time) -> datetime:
    return datetime.combine(d, t)


def duration_seconds(entry: TimeLogEntry) -> float:
    """Recorded duration of a completed session in seconds.

    Returns ``due - start`` when both are set, ``0`` when either is
    ``None``. Per ADR-014: countdown auto-stopped at the target keeps
    the target (``due = start + duration``); manually stopped
    countdowns and count-ups reflect actual elapsed.
    """
    if entry.due is None or entry.start is None:
        return 0.0
    return (entry.due - entry.start).total_seconds()


@dataclass(frozen=True)
class Kpis:
    total_seconds: float
    session_count: int
    avg_seconds: float
    tasks_touched: int


@dataclass(frozen=True)
class DailyPoint:
    """Minutes of focus for a single local day."""

    day: date
    minutes: float


@dataclass(frozen=True)
class TaskBucket:
    """Aligned sessions grouped by task title (UI's groupby semantics)."""

    title: str
    count: int
    total_seconds: float
    avg_seconds: float


@dataclass(frozen=True)
class RecentSession:
    """One row in the recent-sessions list."""

    uid: str
    start: datetime
    label: str
    task_title: str
    mode: str
    duration_seconds: float


@dataclass(frozen=True)
class Rollup:
    """Aggregated statistics for a single period.

    Attributes:
        period: Echo of the input period so the wire response carries
            what was asked for.
        kpis: Top-line totals.
        daily: One :class:`DailyPoint` per local day in the period
            span, including zero days. For ``all-time`` the span runs
            from the earliest entry's local day to the latest entry's
            local day.
        by_task: Top-N buckets by ``total_seconds`` descending. Only
            aligned sessions (with a ``task_id``) appear here.
        recent: Most-recent N sessions in the period, newest first.
    """

    period: Period
    kpis: Kpis
    daily: list[DailyPoint]
    by_task: list[TaskBucket]
    recent: list[RecentSession]


def _in_period(entry: TimeLogEntry, bounds: PeriodBounds) -> bool:
    if entry.start < bounds.start:
        return False
    if bounds.end is not None and entry.start >= bounds.end:
        return False
    return True


def compute_rollup(
    entries: list[TimeLogEntry],
    period: Period,
    today: date,
    task_titles: dict[str, str],
    *,
    top_n: int = 10,
    recent_n: int = 20,
) -> Rollup:
    """Compute KPIs, daily, by-task, and recent sessions for ``period``.

    Args:
        entries: Completed-history time log entries. The caller is
            responsible for passing the right set (typically
            ``caldav.list_time_log()`` which defaults to COMPLETED).
        period: Which window to roll up.
        today: Reference date for the period bounds. Pass
            ``date.today()`` at the call site.
        task_titles: ``task_id`` -> ``title``. The UI/API caller
            resolves these via ``caldav.get_task()``. Missing UIDs
            render as ``"(deleted)"`` to match the UI; sessions with no
            ``task_id`` are excluded from ``by_task`` and use an empty
            title in ``recent``.
        top_n: Cap on the ``by_task`` list (default 10 to match UI).
        recent_n: Cap on the ``recent`` list (default 20 to match UI).
    """
    bounds = period_bounds(period, today)
    in_period = [e for e in entries if _in_period(e, bounds)]

    total = sum(duration_seconds(e) for e in in_period)
    count = len(in_period)
    avg = total / count if count else 0.0
    task_ids = {e.task_id for e in in_period if e.task_id is not None}
    kpis = Kpis(
        total_seconds=total,
        session_count=count,
        avg_seconds=avg,
        tasks_touched=len(task_ids),
    )

    by_day: dict[date, float] = defaultdict(float)
    for e in in_period:
        local_day = to_local(e.start).date()
        by_day[local_day] += duration_seconds(e) / 60.0
    if bounds.end is not None:
        # Fixed-window period: always span the full window (zero days
        # included) so the UI bar chart shows the empty range.
        end_day = to_local(bounds.end - timedelta(microseconds=1)).date()
        start_day = to_local(bounds.start).date()
        cursor = start_day
        daily = []
        while cursor <= end_day:
            daily.append(DailyPoint(day=cursor, minutes=round(by_day.get(cursor, 0.0), 1)))
            cursor += timedelta(days=1)
    elif by_day:
        # All-time: bound by the data span only. With no data, return
        # an empty list (the UI shows the "no sessions yet" info).
        start_day = min(by_day.keys())
        end_day = max(by_day.keys())
        daily = []
        cursor = start_day
        while cursor <= end_day:
            daily.append(DailyPoint(day=cursor, minutes=round(by_day.get(cursor, 0.0), 1)))
            cursor += timedelta(days=1)
    else:
        daily = []

    by_task: dict[str, TaskBucket] = {}
    for e in in_period:
        if e.task_id is None:
            continue
        title = task_titles.get(e.task_id, "(deleted)")
        secs = duration_seconds(e)
        existing = by_task.get(title)
        if existing is None:
            by_task[title] = TaskBucket(
                title=title,
                count=1,
                total_seconds=secs,
                avg_seconds=secs,
            )
        else:
            new_count = existing.count + 1
            new_total = existing.total_seconds + secs
            by_task[title] = TaskBucket(
                title=title,
                count=new_count,
                total_seconds=new_total,
                avg_seconds=new_total / new_count,
            )
    ranked = sorted(by_task.values(), key=lambda b: b.total_seconds, reverse=True)[:top_n]

    recent_sorted = sorted(in_period, key=lambda e: e.start, reverse=True)[:recent_n]
    recent = [
        RecentSession(
            uid=e.uid,
            start=e.start,
            label=e.label,
            task_title=(task_titles.get(e.task_id, "(deleted)") if e.task_id is not None else ""),
            mode=e.mode.value,
            duration_seconds=duration_seconds(e),
        )
        for e in recent_sorted
    ]

    return Rollup(
        period=period,
        kpis=kpis,
        daily=daily,
        by_task=ranked,
        recent=recent,
    )