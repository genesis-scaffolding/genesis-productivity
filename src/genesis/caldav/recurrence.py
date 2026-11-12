"""Recurrent task series: rule arithmetic, materialization, series edits (ADR-033).

A recurrent task is a series of ordinary task records. This module
holds the *engine*: the pure logic that decides which occurrences
exist, what a series owns, and what a horizon change does. The engine
talks to a :class:`RecurrenceStore`, so the same code drives the real
CalDAV backend and the in-memory test backend.

Vocabulary used throughout:

- *current occurrence*: the occurrence the series is working on. It is
  the occurrence at ``RecurrenceSeries.current_index``.
- *open future occurrence*: a non-terminal occurrence after the current
  one. There are exactly ``materialized_future_count`` of them once the
  series is reconciled.
- *history*: completed or cancelled occurrences. They keep their own
  UID and ``completed_at`` and are never edited or removed by the
  recurrence operations.

Ownership rule (ADR-033, round 3): the series owns the *schedule* of
its occurrences (the three dates, the appointment duration, and the
project assignment); an occurrence owns its own content and its own
lifecycle. There is no edit scope: an occurrence edit touches one
occurrence, and the series edit is the only way to change a schedule.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from typing import Any

from ..entities import (
    MAX_MATERIALIZED_FUTURE_COUNT,
    MIN_MATERIALIZED_FUTURE_COUNT,
    SERIES_OWNED_FIELDS,
    STOP_REASON_CANCELLED,
    STOP_REASON_FINISHED,
    CancelSeriesResult,
    RecurrenceAnchor,
    RecurrenceCreateResult,
    RecurrenceRule,
    RecurrenceSeries,
    RecurrenceTemplate,
    SeriesFutureOccurrence,
    SeriesResizeResult,
    SeriesState,
    SeriesSummary,
    SeriesUpdate,
    SeriesUpdateResult,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
    occurrence_role,
)
from ..entities import occurrence_date as _occurrence_date
from ..tz import to_local, to_utc_walltime
from .ical import (
    new_uid,
    now_utc,
    populate_series,
    recurrence_occurrence_uid,
    recurrence_series_from_ical,
    recurrence_series_to_ical,
    task_from_ical,
    task_to_ical,
)
from .tasks import delete_shadow, populate_task, reconcile_shadow

_TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.CANCELED)

_DATE_FIELDS = ("assigned_date", "hard_deadline", "scheduled_start")

_DATE_FIELD_OFFSETS = {
    "assigned_date": "assigned_offset_days",
    "hard_deadline": "deadline_offset_days",
    "scheduled_start": "scheduled_offset_days",
}

_DATE_FIELD_LOCAL_TIME = {
    "hard_deadline": "deadline_local_time",
    "scheduled_start": "scheduled_local_time",
}

_CLEAR_FLAGS = {
    "assigned_date": "clear_assigned_date",
    "scheduled_start": "clear_scheduled_start",
    "project_id": "clear_project_id",
}


# -----------------------------------------------------------------------------
# Rule arithmetic
# -----------------------------------------------------------------------------


def occurrence_date(start: date, rule: RecurrenceRule, count: int) -> date:
    """Return the date ``count`` occurrences after ``start``.

    The date arithmetic itself lives in
    :mod:`genesis.entities.recurrence` so the UI can preview the next
    occurrence without importing the storage layer.
    """
    return _occurrence_date(start, rule, count)


# -----------------------------------------------------------------------------
# Template helpers
# -----------------------------------------------------------------------------


def anchor_value_date(data: TaskCreate, anchor: RecurrenceAnchor) -> date:
    """Return the local calendar date of the task's anchor field.

    Raises:
        ValueError: When the anchored field is not set on the task.
    """
    if anchor == RecurrenceAnchor.ASSIGNED_DATE:
        if data.assigned_date is None:
            raise ValueError("anchor 'assigned_date' requires the task to have an assigned date")
        return data.assigned_date
    if anchor == RecurrenceAnchor.HARD_DEADLINE:
        if data.hard_deadline is None:
            raise ValueError("anchor 'hard_deadline' requires the task to have a hard deadline")
        return to_local(data.hard_deadline).date()
    if data.scheduled_start is None:
        raise ValueError("anchor 'scheduled_start' requires the task to have a scheduled start")
    return to_local(data.scheduled_start).date()


def _local_date(value: datetime | None) -> date | None:
    """Return the local calendar date of a UTC datetime."""
    return to_local(value).date() if value is not None else None


def build_template(data: TaskCreate, anchor: RecurrenceAnchor) -> RecurrenceTemplate:
    """Build the future-occurrence template from a task.

    The date fields become offsets (in days) from the anchor date, and
    the two datetime fields keep their local wall-clock time.
    """
    anchor_date = anchor_value_date(data, anchor)
    assigned_offset = (data.assigned_date - anchor_date).days if data.assigned_date is not None else None
    deadline_offset = None
    scheduled_offset = None
    if data.hard_deadline is not None:
        deadline_offset = (_local_date(data.hard_deadline) - anchor_date).days  # type: ignore[operator]
    if data.scheduled_start is not None:
        scheduled_offset = (_local_date(data.scheduled_start) - anchor_date).days  # type: ignore[operator]
    return RecurrenceTemplate(
        title=data.title,
        description=data.description,
        project_id=data.project_id,
        priority=data.priority,
        scheduled_duration=data.scheduled_duration,
        deadline_local_time=to_local(data.hard_deadline).time() if data.hard_deadline is not None else None,
        scheduled_local_time=to_local(data.scheduled_start).time() if data.scheduled_start is not None else None,
        assigned_offset_days=assigned_offset,
        deadline_offset_days=deadline_offset,
        scheduled_offset_days=scheduled_offset,
    )


def build_first_occurrence(series: RecurrenceSeries, data: TaskCreate, now: datetime) -> Task:
    """Build the index-0 occurrence from the original task values.

    The first occurrence carries the values the user typed; the
    template only drives the occurrences after it.
    """
    return Task(
        uid=recurrence_occurrence_uid(series.uid, 0),
        title=data.title,
        description=data.description,
        hard_deadline=data.hard_deadline,
        assigned_date=data.assigned_date,
        scheduled_start=data.scheduled_start,
        scheduled_duration=data.scheduled_duration,
        priority=data.priority,
        status=TaskStatus.NEEDS_ACTION,
        project_id=data.project_id,
        created=now,
        modified=now,
        recurrence_series_uid=series.uid,
        recurrence_occurrence=series.start_date,
        recurrence_index=0,
    )


def build_occurrence(series: RecurrenceSeries, index: int, now: datetime) -> Task:
    """Build the occurrence at ``index`` from the series template."""
    template = series.template
    base = occurrence_date(series.start_date, series.rule, index - series.start_index)
    assigned_date = None
    if template.assigned_offset_days is not None:
        assigned_date = base + timedelta(days=template.assigned_offset_days)
    hard_deadline = None
    if template.deadline_offset_days is not None and template.deadline_local_time is not None:
        hard_deadline = to_utc_walltime(
            datetime.combine(base + timedelta(days=template.deadline_offset_days), template.deadline_local_time),
        )
    scheduled_start = None
    if template.scheduled_offset_days is not None and template.scheduled_local_time is not None:
        scheduled_start = to_utc_walltime(
            datetime.combine(base + timedelta(days=template.scheduled_offset_days), template.scheduled_local_time),
        )
    return Task(
        uid=recurrence_occurrence_uid(series.uid, index),
        title=template.title,
        description=template.description,
        hard_deadline=hard_deadline,
        assigned_date=assigned_date,
        scheduled_start=scheduled_start,
        scheduled_duration=template.scheduled_duration,
        priority=template.priority,
        status=TaskStatus.NEEDS_ACTION,
        project_id=template.project_id,
        created=now,
        modified=now,
        recurrence_series_uid=series.uid,
        recurrence_occurrence=base,
        recurrence_index=index,
    )


# -----------------------------------------------------------------------------
# Storage contract
# -----------------------------------------------------------------------------


class RecurrenceStore(ABC):
    """Storage operations the recurrence engine needs.

    The real implementation lives in
    :class:`genesis.caldav.recurrence.CalDAVRecurrenceStore`; the
    in-memory test backend implements the same contract.
    """

    @abstractmethod
    def list_series(self) -> list[RecurrenceSeries]:
        """Return every recurrence series."""

    @abstractmethod
    def get_series(self, uid: str) -> RecurrenceSeries | None:
        """Return one series by UID, or None."""

    @abstractmethod
    def save_series(self, series: RecurrenceSeries) -> None:
        """Create or update a series record."""

    @abstractmethod
    def delete_series(self, uid: str) -> None:
        """Delete a series record."""

    @abstractmethod
    def list_series_tasks(self, series_uid: str) -> list[Task]:
        """Return every task belonging to the given series."""

    @abstractmethod
    def get_task(self, uid: str) -> Task | None:
        """Return one task by UID, or None."""

    @abstractmethod
    def put_task(self, task: Task) -> Task:
        """Create or update a task occurrence."""

    @abstractmethod
    def delete_task(self, uid: str) -> None:
        """Delete a task occurrence."""

    @abstractmethod
    def delete_task_shadow(self, uid: str) -> None:
        """Remove the shadow calendar event of a task, if any."""


# -----------------------------------------------------------------------------
# Series state helpers
# -----------------------------------------------------------------------------


def is_terminal(task: Task) -> bool:
    """Return True when the task is completed or canceled."""
    return task.status in _TERMINAL_STATUSES


def _index_of(task: Task) -> int:
    """Return the occurrence index of a task (0 when unset)."""
    return task.recurrence_index if task.recurrence_index is not None else 0


def resolve_series_occurrences(
    series: RecurrenceSeries,
    tasks: list[Task],
) -> tuple[Task | None, list[Task], list[Task]]:
    """Split a series' tasks into (current, open futures, history).

    The current occurrence is the one at ``series.current_index``. When
    it is terminal or gone, the earliest open occurrence after it is
    promoted. The chain never rewinds: a reopened older occurrence
    stays out of the window.
    """
    by_index: dict[int, Task] = {}
    for task in tasks:
        if task.recurrence_index is not None:
            by_index.setdefault(task.recurrence_index, task)

    current: Task | None = None
    candidate = by_index.get(series.current_index)
    if candidate is not None and not is_terminal(candidate):
        current = candidate
    else:
        later = [t for t in tasks if not is_terminal(t) and _index_of(t) > series.current_index]
        later.sort(key=lambda t: (_index_of(t), t.uid))
        current = later[0] if later else None

    current_index = _index_of(current) if current is not None else series.current_index
    futures = [t for t in tasks if not is_terminal(t) and _index_of(t) > current_index]
    futures.sort(key=lambda t: (_index_of(t), t.uid))
    history = [t for t in tasks if is_terminal(t)]
    return current, futures, history


def occurrence_roles(series: RecurrenceSeries, tasks: list[Task]) -> dict[str, str]:
    """Map every occurrence UID of a series to its role."""
    return {task.uid: occurrence_role(series, task) for task in tasks}


def series_owned_changes(task: Task, data: TaskUpdate) -> list[str]:
    """Return the series-owned fields whose effective value changes.

    The comparison uses the *effective* new value (after the
    ``clear_*`` flags are applied), so a client that sends a full
    object unchanged is not reported as changing anything. An ordinary
    task owns all of its fields: the result is empty for it. The
    caller decides what to do with the result; the engine raises the
    refusal.
    """
    if task.recurrence_series_uid is None:
        return []
    dump = data.model_dump(exclude_unset=True)
    changed: list[str] = []
    for field in SERIES_OWNED_FIELDS:
        clear_flag = _CLEAR_FLAGS.get(field)
        if clear_flag is not None and dump.get(clear_flag, False):
            new_value: Any = None
        elif field in dump:
            new_value = dump[field]
        else:
            continue
        if new_value != getattr(task, field):
            changed.append(field)
    return changed


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------


def _materialize(
    store: RecurrenceStore,
    series: RecurrenceSeries,
    tasks: list[Task],
    *,
    target_count: int,
    now: datetime,
) -> list[Task]:
    """Materialize occurrences until the series has ``target_count`` open futures.

    Existing logical occurrences are never rewritten: the missing
    indexes are derived from ``highest_materialized_index`` and each
    one is written with a deterministic UID. A retry after a partial
    write therefore resumes at the first missing index. The end date
    caps the window: no occurrence is materialized past it.
    """
    current, futures, _history = resolve_series_occurrences(series, tasks)
    if current is None:
        return []
    existing_indexes = {_index_of(t) for t in tasks}
    missing = target_count - len(futures)
    created: list[Task] = []
    for _ in range(max(0, missing)):
        index = series.highest_materialized_index + 1
        while index in existing_indexes:
            index += 1
        base = occurrence_date(series.start_date, series.rule, index - series.start_index)
        if series.end_date is not None and base > series.end_date:
            # The end date caps the window.
            break
        uid = recurrence_occurrence_uid(series.uid, index)
        clash = store.get_task(uid)
        if clash is not None and (clash.recurrence_series_uid != series.uid or clash.recurrence_index != index):
            # The deterministic UID of this logical occurrence is
            # already used by a different task. Overwriting it would
            # destroy an unrelated record.
            raise ValueError(f"occurrence UID {uid} is already used by another task")
        task = build_occurrence(series, index, now)
        store.put_task(task)
        existing_indexes.add(index)
        created.append(task)
        series.highest_materialized_index = index
    return created


def _next_occurrence_date(series: RecurrenceSeries) -> date:
    """Return the date of the occurrence after the last materialized one."""
    return occurrence_date(
        series.start_date,
        series.rule,
        series.highest_materialized_index + 1 - series.start_index,
    )


def _allows_another_occurrence(series: RecurrenceSeries) -> bool:
    """Return True when the series may still materialize an occurrence.

    The end date caps the window: a series whose next occurrence
    would fall after it cannot renew any more.
    """
    if series.end_date is None:
        return True
    return _next_occurrence_date(series) <= series.end_date


def reconcile_series(store: RecurrenceStore, series_uid: str, *, refill: bool = True) -> None:
    """Restore the series window after an occurrence changed state.

    Promotes the next open occurrence when the current one became
    terminal, then materializes enough tail occurrences to restore the
    configured number of open futures.

    ``refill=False`` is the delete path: a deleted occurrence is
    skipped for good, so the window is not refilled by the delete. The
    horizon stays one smaller until the current occurrence completes
    and the natural renewal restores it.
    """
    series = store.get_series(series_uid)
    if series is None or not series.active:
        # A cancelled or deactivated series does not renew.
        return
    tasks = store.list_series_tasks(series_uid)
    current, _futures, _history = resolve_series_occurrences(series, tasks)
    now = now_utc()
    if current is None:
        # Every materialized occurrence is terminal: the series has
        # nothing left to renew.
        series.active = False
        series.stop_reason = STOP_REASON_FINISHED
        series.modified = now
        store.save_series(series)
        return
    changed = False
    if current.recurrence_index is not None and current.recurrence_index != series.current_index:
        series.current_index = current.recurrence_index
        changed = True
    if refill and series.stop_reason != STOP_REASON_FINISHED:
        created = _materialize(store, series, tasks, target_count=series.materialized_future_count, now=now)
        if created:
            changed = True
    if changed:
        series.modified = now
        store.save_series(series)


def create_recurrent_task(
    store: RecurrenceStore,
    data: TaskCreate,
    *,
    rule: RecurrenceRule,
    materialized_future_count: int,
    end_date: date | None = None,
) -> RecurrenceCreateResult:
    """Create a recurrent task: the series, the current occurrence, and N futures.

    Raises:
        ValueError: When the horizon is out of range, the anchor
            field is missing from the task, or the end date is before
            the start date.
    """
    if not (MIN_MATERIALIZED_FUTURE_COUNT <= materialized_future_count <= MAX_MATERIALIZED_FUTURE_COUNT):
        raise ValueError(
            f"materialized_future_count must be between {MIN_MATERIALIZED_FUTURE_COUNT} "
            f"and {MAX_MATERIALIZED_FUTURE_COUNT}, got {materialized_future_count}",
        )
    now = now_utc()
    anchor_date = anchor_value_date(data, rule.anchor)
    if end_date is not None and end_date < anchor_date:
        raise ValueError("the end date must not be before the start date of the series")
    template = build_template(data, rule.anchor)
    series = RecurrenceSeries(
        uid=new_uid("r"),
        rule=rule,
        start_date=anchor_date,
        start_index=0,
        template=template,
        materialized_future_count=materialized_future_count,
        highest_materialized_index=0,
        current_index=0,
        end_date=end_date,
        active=True,
        created=now,
        modified=now,
    )

    created_uids: list[str] = []
    current: Task | None = None
    try:
        store.save_series(series)
        current = build_first_occurrence(series, data, now)
        store.put_task(current)
        created_uids.append(current.uid)
        for index in range(1, materialized_future_count + 1):
            base = occurrence_date(series.start_date, series.rule, index)
            if end_date is not None and base > end_date:
                # The end date caps the window.
                break
            task = build_occurrence(series, index, now)
            store.put_task(task)
            created_uids.append(task.uid)
            series.highest_materialized_index = index
        series.modified = now
        store.save_series(series)
    except Exception:
        # Compensating cleanup: a half-written series must not leave
        # duplicate occurrences behind.
        for uid in created_uids:
            try:
                store.delete_task(uid)
                store.delete_task_shadow(uid)
            except Exception:
                pass
        try:
            store.delete_series(series.uid)
        except Exception:
            pass
        raise

    assert current is not None
    return RecurrenceCreateResult(
        task=current,
        series=series,
        created_task_uids=created_uids,
    )


def _field_date(task: Task, field: str) -> date | None:
    """Return the local calendar date of one of the task's date fields."""
    if field == "assigned_date":
        return task.assigned_date
    if field == "hard_deadline":
        return _local_date(task.hard_deadline)
    return _local_date(task.scheduled_start)


def _local_time_for(field: str, task: Task) -> time | None:
    """Return the local wall-clock time of one of the datetime fields."""
    if field == "hard_deadline":
        return to_local(task.hard_deadline).time() if task.hard_deadline is not None else None
    if field == "scheduled_start":
        return to_local(task.scheduled_start).time() if task.scheduled_start is not None else None
    return None


def _rebuild_dates(task: Task, series: RecurrenceSeries, index: int) -> None:
    """Recompute an open occurrence's dates from the series template."""
    template = series.template
    base = occurrence_date(series.start_date, series.rule, index - series.start_index)
    if template.assigned_offset_days is not None:
        task.assigned_date = base + timedelta(days=template.assigned_offset_days)
    else:
        task.assigned_date = None
    if template.deadline_offset_days is not None and template.deadline_local_time is not None:
        task.hard_deadline = to_utc_walltime(
            datetime.combine(base + timedelta(days=template.deadline_offset_days), template.deadline_local_time),
        )
    else:
        task.hard_deadline = None
    if template.scheduled_offset_days is not None and template.scheduled_local_time is not None:
        task.scheduled_start = to_utc_walltime(
            datetime.combine(base + timedelta(days=template.scheduled_offset_days), template.scheduled_local_time),
        )
    else:
        task.scheduled_start = None
    task.recurrence_occurrence = base


def _reanchor_offsets(template: RecurrenceTemplate, current: Task, new_start_date: date) -> None:
    """Recompute every date offset of the template from the current occurrence.

    A re-anchor changes the field the offsets are relative to, so all
    of them are recomputed from the occurrence the chain is anchored
    on. The local wall-clock times follow.
    """
    for field in _DATE_FIELDS:
        value = _field_date(current, field)
        setattr(
            template,
            _DATE_FIELD_OFFSETS[field],
            None if value is None else (value - new_start_date).days,
        )
        local_time_attr = _DATE_FIELD_LOCAL_TIME.get(field)
        if local_time_attr is not None:
            setattr(template, local_time_attr, _local_time_for(field, current))


def update_series(store: RecurrenceStore, series_uid: str, changes: SeriesUpdate) -> SeriesUpdateResult:
    """Apply a series edit and re-derive the open occurrences.

    The series edit is the only way to change the schedule of a
    recurrent task. It rewrites the open occurrences (the current one
    and the materialized futures) and never touches a terminal
    occurrence: completed history is immutable.

    Raises:
        ValueError: When the series is unknown, the series is
            cancelled, the anchor cannot be re-anchored, or the end
            date is before the start date.
    """
    series = store.get_series(series_uid)
    if series is None:
        raise ValueError("recurrence series not found")
    if series.stop_reason == STOP_REASON_CANCELLED:
        raise ValueError("recurrence series is cancelled and cannot be changed")

    dump = changes.model_dump(exclude_unset=True)
    now = now_utc()
    tasks = store.list_series_tasks(series_uid)
    current, futures, _history = resolve_series_occurrences(series, tasks)

    # -- the rule ------------------------------------------------------------
    new_rule = series.rule.model_copy()
    if dump.get("frequency") is not None:
        new_rule.frequency = dump["frequency"]
    if dump.get("interval") is not None:
        new_rule.interval = dump["interval"]
    anchor_changed = False
    if dump.get("anchor") is not None and dump["anchor"] != series.rule.anchor:
        new_rule.anchor = dump["anchor"]
        anchor_changed = True

    # -- the dates -----------------------------------------------------------
    new_start_date = series.start_date
    if dump.get("start_date") is not None:
        new_start_date = dump["start_date"]

    new_end_date = series.end_date
    if dump.get("clear_end_date"):
        new_end_date = None
    elif "end_date" in dump:
        new_end_date = dump["end_date"]

    # -- the content template ------------------------------------------------
    template = series.template.model_copy(deep=True)
    content_changed = False
    if "title" in dump:
        if not (dump["title"] or "").strip():
            raise ValueError("the series title must not be empty")
        template.title = dump["title"]
        content_changed = True
    if "description" in dump:
        template.description = dump["description"]
        content_changed = True
    if "project_id" in dump:
        template.project_id = dump["project_id"]
        content_changed = True
    if "priority" in dump:
        template.priority = dump["priority"]
        content_changed = True
    if "scheduled_duration" in dump:
        template.scheduled_duration = dump["scheduled_duration"]
        content_changed = True

    # -- the horizon ---------------------------------------------------------
    old_horizon = series.materialized_future_count
    new_horizon = old_horizon
    if dump.get("materialized_future_count") is not None:
        new_horizon = dump["materialized_future_count"]

    # -- the re-anchor -------------------------------------------------------
    if anchor_changed:
        if current is None:
            raise ValueError("cannot re-anchor: the series has no current occurrence")
        base = _field_date(current, new_rule.anchor.value)
        if base is None:
            raise ValueError(
                f"cannot re-anchor on '{new_rule.anchor.value}': the current occurrence has no such date",
            )
        new_start_date = base
        _reanchor_offsets(template, current, new_start_date)

    if new_end_date is not None and new_end_date < new_start_date:
        raise ValueError("the end date must not be before the start date")

    series.rule = new_rule
    series.start_date = new_start_date
    series.template = template
    series.end_date = new_end_date
    series.materialized_future_count = new_horizon

    # -- a finished series comes back when the schedule allows another occurrence
    schedule_changed = (
        "frequency" in dump
        or "interval" in dump
        or "anchor" in dump
        or "start_date" in dump
        or "end_date" in dump
        or bool(dump.get("clear_end_date"))
    )
    created_uids: list[str] = []
    if series.stop_reason == STOP_REASON_FINISHED and schedule_changed and _allows_another_occurrence(series):
        series.active = True
        series.stop_reason = None
        if current is None:
            # The series had no open occurrence left: the next one
            # becomes the new current occurrence.
            index = series.highest_materialized_index + 1
            task = build_occurrence(series, index, now)
            store.put_task(task)
            series.highest_materialized_index = index
            series.current_index = index
            current = task
            created_uids.append(task.uid)

    # -- rewrite the open occurrences ----------------------------------------
    open_occurrences = list(futures)
    if current is not None:
        open_occurrences.insert(0, current)
    updated_uids: list[str] = []
    canceled_uids: list[str] = []
    for occurrence in open_occurrences:
        if content_changed:
            occurrence.title = template.title
            occurrence.description = template.description
            occurrence.project_id = template.project_id
            occurrence.priority = template.priority
            occurrence.scheduled_duration = template.scheduled_duration
        _rebuild_dates(occurrence, series, _index_of(occurrence))
        occurrence.modified = now
        store.put_task(occurrence)
        updated_uids.append(occurrence.uid)

    # -- the end date cancels the occurrences after it -------------------------
    if new_end_date is not None:
        for occurrence in open_occurrences:
            if occurrence.recurrence_occurrence is None:
                continue
            if occurrence.recurrence_occurrence <= new_end_date:
                continue
            occurrence.status = TaskStatus.CANCELED
            occurrence.completed_at = None
            occurrence.modified = now
            store.put_task(occurrence)
            store.delete_task_shadow(occurrence.uid)
            canceled_uids.append(occurrence.uid)

    # -- the horizon ----------------------------------------------------------
    tasks = store.list_series_tasks(series_uid)
    _current, open_futures, _history = resolve_series_occurrences(series, tasks)
    if new_horizon < old_horizon:
        for occurrence in open_futures[new_horizon:]:
            occurrence.status = TaskStatus.CANCELED
            occurrence.completed_at = None
            occurrence.modified = now
            store.put_task(occurrence)
            store.delete_task_shadow(occurrence.uid)
            canceled_uids.append(occurrence.uid)

    # -- refill the window ----------------------------------------------------
    if series.active and series.stop_reason != STOP_REASON_FINISHED:
        created = _materialize(store, series, tasks, target_count=new_horizon, now=now)
        created_uids.extend(t.uid for t in created)

    # -- a series without an open occurrence is finished ----------------------
    final_tasks = store.list_series_tasks(series_uid)
    final_current, _futures, _history = resolve_series_occurrences(series, final_tasks)
    if final_current is None:
        series.active = False
        series.stop_reason = STOP_REASON_FINISHED
    elif final_current.recurrence_index is not None and final_current.recurrence_index != series.current_index:
        series.current_index = final_current.recurrence_index

    series.modified = now
    store.save_series(series)

    summary = series_summary(store, series.uid)
    return SeriesUpdateResult(
        series_uid=series.uid,
        active=series.active,
        end_date=series.end_date,
        materialized_future_count=series.materialized_future_count,
        current_task_uid=summary.current_task_uid if summary is not None else None,
        next_occurrence_date=summary.next_occurrence_date if summary is not None else None,
        updated_task_uids=updated_uids,
        created_task_uids=created_uids,
        canceled_task_uids=canceled_uids,
    )


def cancel_series(store: RecurrenceStore, series_uid: str) -> CancelSeriesResult:
    """Cancel the future window of a series and stop its renewal.

    Every open occurrence after the current one is cancelled. The
    current occurrence and the completed history stay. The action is
    idempotent: cancelling an already cancelled series returns an
    empty list of cancelled UIDs.

    Raises:
        ValueError: When the series does not exist.
    """
    series = store.get_series(series_uid)
    if series is None:
        raise ValueError("recurrence series not found")
    if series.stop_reason == STOP_REASON_CANCELLED:
        return CancelSeriesResult(series_uid=series.uid, canceled_uids=[], active=False)

    now = now_utc()
    tasks = store.list_series_tasks(series_uid)
    _current, futures, _history = resolve_series_occurrences(series, tasks)
    canceled: list[str] = []
    for occurrence in futures:
        occurrence.status = TaskStatus.CANCELED
        occurrence.completed_at = None
        occurrence.modified = now
        store.put_task(occurrence)
        store.delete_task_shadow(occurrence.uid)
        canceled.append(occurrence.uid)

    series.active = False
    series.stop_reason = STOP_REASON_CANCELLED
    series.modified = now
    store.save_series(series)
    return CancelSeriesResult(series_uid=series.uid, canceled_uids=canceled, active=False)


def deactivate_series(store: RecurrenceStore, series_uid: str) -> None:
    """Deactivate a series so it stops renewing.

    Used when the work the series describes is cancelled (a project
    cancellation cascades to the occurrences). The occurrences keep
    their records; only the renewal stops.
    """
    series = store.get_series(series_uid)
    if series is None or not series.active:
        return
    series.active = False
    series.modified = now_utc()
    store.save_series(series)


def clear_series_project(store: RecurrenceStore, project_uid: str) -> None:
    """Unlink every series template from a deleted project.

    Deleting a project clears the project link on its tasks. The
    series templates must follow, otherwise the next materialized
    occurrence is created with a dangling project id. The series
    itself stays active: the work is still recurring, it is just not
    attached to a project any more.
    """
    for series in store.list_series():
        if series.template.project_id != project_uid:
            continue
        series.template.project_id = None
        series.modified = now_utc()
        store.save_series(series)


def resize_series(store: RecurrenceStore, series_uid: str, materialized_future_count: int) -> SeriesResizeResult:
    """Change the materialized horizon of a running series.

    Growing the horizon materializes the missing tail occurrences.
    Shrinking it keeps the current occurrence and the nearest future
    occurrences and cancels the farthest extras; cancelled indexes are
    never resurrected by a later expansion.

    Raises:
        ValueError: When the series does not exist, is not active, or
            the requested count is out of range.
    """
    if not (MIN_MATERIALIZED_FUTURE_COUNT <= materialized_future_count <= MAX_MATERIALIZED_FUTURE_COUNT):
        raise ValueError(
            f"materialized_future_count must be between {MIN_MATERIALIZED_FUTURE_COUNT} "
            f"and {MAX_MATERIALIZED_FUTURE_COUNT}, got {materialized_future_count}",
        )
    series = store.get_series(series_uid)
    if series is None:
        raise ValueError("recurrence series not found")
    if not series.active:
        raise ValueError("recurrence series is not active")

    now = now_utc()
    tasks = store.list_series_tasks(series_uid)
    current, futures, _history = resolve_series_occurrences(series, tasks)
    created_uids: list[str] = []
    canceled_uids: list[str] = []

    if materialized_future_count > series.materialized_future_count:
        created = _materialize(
            store,
            series,
            tasks,
            target_count=materialized_future_count,
            now=now,
        )
        created_uids = [t.uid for t in created]
    elif materialized_future_count < series.materialized_future_count:
        drop = futures[materialized_future_count:]
        for occurrence in drop:
            occurrence.status = TaskStatus.CANCELED
            occurrence.completed_at = None
            occurrence.modified = now
            store.put_task(occurrence)
            store.delete_task_shadow(occurrence.uid)
            canceled_uids.append(occurrence.uid)

    series.materialized_future_count = materialized_future_count
    series.modified = now
    store.save_series(series)

    tasks = store.list_series_tasks(series_uid)
    current, futures, _history = resolve_series_occurrences(series, tasks)
    return SeriesResizeResult(
        series_uid=series.uid,
        materialized_future_count=materialized_future_count,
        current_task_uid=current.uid if current is not None else None,
        future_task_uids=[t.uid for t in futures],
        created_task_uids=created_uids,
        canceled_task_uids=canceled_uids,
    )


def series_summary(store: RecurrenceStore, series_uid: str) -> SeriesSummary | None:
    """Return the summary row of one series, or None when unknown."""
    series = store.get_series(series_uid)
    if series is None:
        return None
    tasks = store.list_series_tasks(series_uid)
    current, futures, history = resolve_series_occurrences(series, tasks)
    next_date: date | None = None
    if futures:
        next_date = futures[0].recurrence_occurrence
    elif series.active and series.stop_reason != STOP_REASON_FINISHED:
        next_date = _next_occurrence_date(series)
    return SeriesSummary(
        uid=series.uid,
        title=series.template.title,
        project_id=series.template.project_id,
        frequency=series.rule.frequency,
        interval=series.rule.interval,
        anchor=series.rule.anchor,
        start_date=series.start_date,
        end_date=series.end_date,
        materialized_future_count=series.materialized_future_count,
        active=series.active,
        stop_reason=series.stop_reason,
        current_task_uid=current.uid if current is not None else None,
        current_occurrence_date=current.recurrence_occurrence if current is not None else None,
        next_occurrence_date=next_date,
        open_occurrence_count=(1 if current is not None else 0) + len(futures),
        history_count=len(history),
        created=series.created,
        modified=series.modified,
    )


def series_summaries(store: RecurrenceStore) -> list[SeriesSummary]:
    """Return the summary row of every series."""
    summaries: list[SeriesSummary] = []
    for series in store.list_series():
        summary = series_summary(store, series.uid)
        if summary is not None:
            summaries.append(summary)
    return summaries


def series_state(store: RecurrenceStore, series_uid: str) -> SeriesState | None:
    """Return the inspection state of a series, or None when unknown."""
    series = store.get_series(series_uid)
    if series is None:
        return None
    tasks = store.list_series_tasks(series_uid)
    current, futures, _history = resolve_series_occurrences(series, tasks)
    next_date: date | None = futures[0].recurrence_occurrence if futures else None
    if next_date is None and series.active and series.stop_reason != STOP_REASON_FINISHED:
        next_date = _next_occurrence_date(series)
    return SeriesState(
        uid=series.uid,
        frequency=series.rule.frequency,
        interval=series.rule.interval,
        anchor=series.rule.anchor,
        start_date=series.start_date,
        start_index=series.start_index,
        end_date=series.end_date,
        materialized_future_count=series.materialized_future_count,
        active=series.active,
        stop_reason=series.stop_reason,
        current_task_uid=current.uid if current is not None else None,
        next_occurrence_date=next_date,
        future_occurrences=[
            SeriesFutureOccurrence(
                uid=t.uid,
                index=_index_of(t),
                occurrence_date=t.recurrence_occurrence,
            )
            for t in futures
        ],
        template=series.template,
    )


# -----------------------------------------------------------------------------
# CalDAV-backed store
# -----------------------------------------------------------------------------


class CalDAVRecurrenceStore(RecurrenceStore):
    """Recurrence store backed by the CalDAV collections.

    The series live in the isolated ``recurrences`` collection; the
    occurrences live in the ordinary ``tasks`` collection. Task writes
    are get-or-create on the deterministic occurrence UID, so a retry
    after a partial write updates the same resource instead of
    creating a second object for one logical occurrence.
    """

    def __init__(self, facade: Any) -> None:
        self._facade = facade

    # -- calendars ----------------------------------------------------------

    def _recurrences_calendar(self) -> Any:
        return self._facade._get_calendar(self._facade.RECURRENCES_COLLECTION)

    def _tasks_calendar(self) -> Any:
        return self._facade._get_calendar(self._facade.TASKS_COLLECTION)

    @staticmethod
    def _find_object(calendar: Any, uid: str) -> Any:
        """Return the object with the given UID, or None."""
        try:
            obj = calendar.object_by_uid(uid)
        except Exception:
            return None
        return obj

    # -- series -------------------------------------------------------------

    def list_series(self) -> list[RecurrenceSeries]:
        from caldav.lib.error import NotFoundError

        searcher = self._recurrences_calendar().searcher(todo=True, include_completed=True)
        series: list[RecurrenceSeries] = []
        for obj in searcher.search():
            try:
                component = obj.icalendar_component
            except NotFoundError:
                continue
            if component.name != "VTODO":
                continue
            try:
                series.append(recurrence_series_from_ical(component))
            except Exception:
                continue
        return series

    def get_series(self, uid: str) -> RecurrenceSeries | None:
        obj = self._find_object(self._recurrences_calendar(), uid)
        if obj is None:
            return None
        try:
            return recurrence_series_from_ical(obj.icalendar_component)
        except Exception:
            return None

    def save_series(self, series: RecurrenceSeries) -> None:
        calendar = self._recurrences_calendar()
        obj = self._find_object(calendar, series.uid)
        if obj is None:
            calendar.add_todo(recurrence_series_to_ical(series))
            return
        populate_series(obj.icalendar_component, series)
        obj.save()

    def delete_series(self, uid: str) -> None:
        obj = self._find_object(self._recurrences_calendar(), uid)
        if obj is None:
            return
        try:
            obj.delete()
        except Exception:
            pass

    # -- occurrences --------------------------------------------------------

    def list_series_tasks(self, series_uid: str) -> list[Task]:
        from caldav.lib.error import NotFoundError

        searcher = self._tasks_calendar().searcher(todo=True, include_completed=True)
        searcher.add_property_filter("X-RECURRENCE-SERIES-ID", series_uid, operator="==")
        tasks: list[Task] = []
        for obj in searcher.search():
            try:
                component = obj.icalendar_component
            except NotFoundError:
                continue
            if component.name != "VTODO":
                continue
            try:
                tasks.append(task_from_ical(component))
            except Exception:
                continue
        return tasks

    def get_task(self, uid: str) -> Task | None:
        obj = self._find_object(self._tasks_calendar(), uid)
        if obj is None:
            return None
        try:
            return task_from_ical(obj.icalendar_component)
        except Exception:
            return None

    def put_task(self, task: Task) -> Task:
        calendar = self._tasks_calendar()
        obj = self._find_object(calendar, task.uid)
        if obj is None:
            calendar.add_todo(task_to_ical(task))
        else:
            populate_task(obj.icalendar_component, task)
            obj.save()
        reconcile_shadow(self._facade, task)
        return task

    def delete_task(self, uid: str) -> None:
        obj = self._find_object(self._tasks_calendar(), uid)
        if obj is None:
            return
        try:
            obj.delete()
        except Exception:
            pass

    def delete_task_shadow(self, uid: str) -> None:
        delete_shadow(self._facade, uid)
