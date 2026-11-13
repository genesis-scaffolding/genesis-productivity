"""Recurrence domain models (ADR-033).

A recurrent task is a *series* of ordinary task records. Every
occurrence is a normal :class:`genesis.entities.task.Task` with its own
UID, status, and ``completed_at``; the series record holds the rule,
the anchor, the template used to build future occurrences, the
effective materialization horizon, and the highest materialized
occurrence index.

The materialized horizon (N) is a count of ordinary task rows kept
ready ahead of the current occurrence. It is configured per user
(``configs.yaml`` -> ``recurrence.default_materialized_future_count``,
default 1) and persisted per series
(:attr:`RecurrenceSeries.materialized_future_count`).

The series owns the *schedule* of its occurrences; an occurrence owns
its own content and lifecycle. The five fields the series owns are
listed in :data:`SERIES_OWNED_FIELDS`: the three dates, the
appointment duration, and the project assignment.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from .task import Task, TaskStatus

MIN_MATERIALIZED_FUTURE_COUNT = 1
"""Lower bound of the materialized future-occurrence horizon."""

MAX_MATERIALIZED_FUTURE_COUNT = 100
"""Upper bound of the materialized future-occurrence horizon.

The cap keeps a malformed request from asking the server for an
unbounded number of CalDAV writes.
"""

SERIES_OWNED_FIELDS = (
    "assigned_date",
    "hard_deadline",
    "scheduled_start",
    "scheduled_duration",
    "project_id",
)
"""The task fields the series owns.

A materialized occurrence may not change one of these: the series
materializes them from its own anchor, its own rule, and its own
template. Changing one on an occurrence is refused; the series edit is
the only way to change the schedule of a recurrent task (ADR-033).
"""

ROLE_CURRENT = "current"
"""Role of the occurrence the series is working on."""

ROLE_FUTURE = "future"
"""Role of an open occurrence after the current one."""

ROLE_HISTORY = "history"
"""Role of a terminal (completed or cancelled) occurrence."""

STOP_REASON_CANCELLED = "cancelled"
"""Stop reason of a series that was cancelled: it is final."""

STOP_REASON_FINISHED = "finished"
"""Stop reason of a series that reached its end date or ran out."""


class RecurrenceFrequency(StrEnum):
    """Supported recurrence frequencies (ADR-033, v1 rule set)."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


def days_in_month(year: int, month: int) -> int:
    """Return the number of days in the given month."""
    return calendar.monthrange(year, month)[1]


def add_months(value: date, months: int) -> date:
    """Return ``value`` shifted by ``months`` months, clamping the day.

    The clamp is relative to the *original* day, which is what keeps a
    January 31 monthly series on January 31 / February 28 / March 31
    instead of drifting to March 28.
    """
    total = value.month - 1 + months
    year = value.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(value.day, days_in_month(year, month)))


def add_years(value: date, years: int) -> date:
    """Return ``value`` shifted by ``years`` years, clamping the day."""
    year = value.year + years
    return date(year, value.month, min(value.day, days_in_month(year, value.month)))


def occurrence_date(start: date, rule: RecurrenceRule, count: int) -> date:
    """Return the date ``count`` occurrences after ``start``.

    Occurrences are computed from the series anchor and the occurrence
    index, never by repeatedly advancing a clamped value.
    """
    if count == 0:
        return start
    if rule.frequency == RecurrenceFrequency.DAILY:
        return start + timedelta(days=rule.interval * count)
    if rule.frequency == RecurrenceFrequency.WEEKLY:
        return start + timedelta(weeks=rule.interval * count)
    if rule.frequency == RecurrenceFrequency.MONTHLY:
        return add_months(start, rule.interval * count)
    return add_years(start, rule.interval * count)


class RecurrenceAnchor(StrEnum):
    """Task field the recurrence is anchored on.

    The anchored field must exist on the task; its value is the first
    occurrence date.
    """

    ASSIGNED_DATE = "assigned_date"
    HARD_DEADLINE = "hard_deadline"
    SCHEDULED_START = "scheduled_start"


class RecurrenceRule(BaseModel):
    """The v1 recurrence rule.

    Attributes:
        frequency: How often the series repeats.
        interval: Number of frequency units between occurrences.
        anchor: The task field the occurrence dates are derived from.
    """

    frequency: RecurrenceFrequency
    interval: int = Field(default=1, ge=1)
    anchor: RecurrenceAnchor


class RecurrenceTemplate(BaseModel):
    """Template describing the content of future occurrences.

    The template stores the *content* fields verbatim and the *date*
    fields as offsets (in days) from the series anchor date, plus the
    local wall-clock time of the two datetime fields. Storing offsets
    rather than absolute dates is what lets a monthly series keep its
    relative shape when the anchor day is clamped (January 31 ->
    February 28 -> March 31).
    """

    title: str
    description: str | None = None
    project_id: str | None = None
    priority: int | None = None
    scheduled_duration: int | None = None
    deadline_local_time: time | None = None
    scheduled_local_time: time | None = None
    assigned_offset_days: int | None = None
    deadline_offset_days: int | None = None
    scheduled_offset_days: int | None = None


class RecurrenceSeries(BaseModel):
    """The series record of a recurrent task.

    Attributes:
        uid: Series UID. Uses the ``r-`` typed prefix (ADR-016).
        rule: The recurrence rule.
        start_date: The anchor date of the occurrence at
            ``start_index``.
        start_index: Index of the occurrence ``start_date`` belongs to.
            Normally 0: the chain is anchored on the first
            occurrence, and the anchor moves only when the anchored
            field of the series changes.
        template: Content template for future occurrences.
        materialized_future_count: Effective horizon N. The number of
            open future occurrences kept materialized ahead of the
            current occurrence.
        highest_materialized_index: Highest occurrence index ever
            materialized for this series.
        current_index: Index of the occurrence that currently acts as
            the series' current occurrence. It moves forward when the
            current occurrence becomes terminal; it never moves back,
            so reopening an old occurrence does not rewind the chain.
        end_date: The last date an occurrence may start on, or None
            for an open-ended series. It caps the materialized window.
        stop_reason: Why the series stopped: ``"cancelled"`` (final),
            ``"finished"`` (it reached its end date or ran out of
            occurrences), or None while the series runs.
        active: False once the series is cancelled or finished.
        created: Creation timestamp (UTC).
        modified: Last modification timestamp (UTC).
    """

    uid: str
    rule: RecurrenceRule
    start_date: date
    start_index: int = Field(default=0, ge=0)
    template: RecurrenceTemplate
    materialized_future_count: int = Field(
        default=1,
        ge=MIN_MATERIALIZED_FUTURE_COUNT,
        le=MAX_MATERIALIZED_FUTURE_COUNT,
    )
    highest_materialized_index: int = Field(default=0, ge=0)
    current_index: int = Field(default=0, ge=0)
    end_date: date | None = None
    stop_reason: str | None = None
    active: bool = True
    created: datetime | None = None
    modified: datetime | None = None


class SeriesFutureOccurrence(BaseModel):
    """One materialized future occurrence of a series."""

    uid: str
    index: int
    occurrence_date: date | None = None


class SeriesState(BaseModel):
    """Inspection state of a recurrence series."""

    uid: str
    frequency: RecurrenceFrequency
    interval: int
    anchor: RecurrenceAnchor
    start_date: date
    start_index: int = 0
    end_date: date | None = None
    materialized_future_count: int
    active: bool
    stop_reason: str | None = None
    current_task_uid: str | None = None
    next_occurrence_date: date | None = None
    future_occurrences: list[SeriesFutureOccurrence] = Field(default_factory=list)
    template: RecurrenceTemplate


class RecurrenceCreateResult(BaseModel):
    """Result of creating a recurrent task."""

    task: Task
    series: RecurrenceSeries
    created_task_uids: list[str] = Field(default_factory=list)


def occurrence_role(series: RecurrenceSeries, task: Task) -> str:
    """Return the role of one occurrence in its series.

    The role is derived from the task's status and the series'
    ``current_index``, so it costs no extra fetch: a terminal task is
    history, an open task at the current index is the current
    occurrence, anything else is a future occurrence.
    """
    if task.recurrence_series_uid is None:
        return ""
    if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELED):
        return ROLE_HISTORY
    if task.recurrence_index is not None and task.recurrence_index == series.current_index:
        return ROLE_CURRENT
    return ROLE_FUTURE


class SeriesSummary(BaseModel):
    """One row of the recurrence series table.

    The summary is the discovery surface of a series: an agent that
    knows nothing about the series can find it here, read its rule,
    its dates, its horizon, its status, and where the next occurrence
    falls.
    """

    uid: str
    title: str
    project_id: str | None = None
    frequency: RecurrenceFrequency
    interval: int
    anchor: RecurrenceAnchor
    start_date: date
    end_date: date | None = None
    materialized_future_count: int
    active: bool = True
    stop_reason: str | None = None
    current_task_uid: str | None = None
    current_occurrence_date: date | None = None
    next_occurrence_date: date | None = None
    open_occurrence_count: int = 0
    history_count: int = 0
    created: datetime | None = None
    modified: datetime | None = None


def series_status(summary: SeriesSummary) -> str:
    """Return the human-readable status of a series.

    A cancelled series is final; a finished series reached its end
    date or ran out of occurrences; an inactive series was stopped by
    something else (a cancelled project, for instance).
    """
    if summary.stop_reason == STOP_REASON_CANCELLED:
        return "Cancelled"
    if summary.stop_reason == STOP_REASON_FINISHED:
        return "Finished"
    if summary.active:
        return "Active"
    return "Inactive"


class SeriesUpdate(BaseModel):
    """The change payload of a series edit.

    Every field is optional. A field that is not set keeps the
    series' current value; a nullable field that is explicitly set to
    ``None`` clears it.
    """

    frequency: RecurrenceFrequency | None = None
    interval: int | None = Field(default=None, ge=1)
    anchor: RecurrenceAnchor | None = None
    start_date: date | None = None
    end_date: date | None = None
    clear_end_date: bool = False
    title: str | None = None
    description: str | None = None
    project_id: str | None = None
    priority: int | None = None
    scheduled_duration: int | None = None
    materialized_future_count: int | None = Field(
        default=None,
        ge=MIN_MATERIALIZED_FUTURE_COUNT,
        le=MAX_MATERIALIZED_FUTURE_COUNT,
    )


class SeriesUpdateResult(BaseModel):
    """Result of a series edit."""

    series_uid: str
    active: bool = True
    end_date: date | None = None
    materialized_future_count: int
    current_task_uid: str | None = None
    next_occurrence_date: date | None = None
    updated_task_uids: list[str] = Field(default_factory=list)
    created_task_uids: list[str] = Field(default_factory=list)
    canceled_task_uids: list[str] = Field(default_factory=list)


class CancelSeriesResult(BaseModel):
    """Result of cancelling a series."""

    series_uid: str
    canceled_uids: list[str] = Field(default_factory=list)
    active: bool = False
