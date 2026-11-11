"""Recurrent task series: rule arithmetic, materialization, scoped edits (ADR-033).

A recurrent task is a series of ordinary task records. This module
holds the *engine*: the pure logic that decides which occurrences
exist, which of them an edit touches, and what a horizon change does.
The engine talks to a :class:`RecurrenceStore`, so the same code drives
the real CalDAV backend and the in-memory test backend.

Vocabulary used throughout:

- *current occurrence*: the occurrence the series is working on. It is
  the occurrence at ``RecurrenceSeries.current_index``.
- *open future occurrence*: a non-terminal occurrence after the current
  one. There are exactly ``materialized_future_count`` of them once the
  series is reconciled.
- *history*: completed or cancelled occurrences. They keep their own
  UID and ``completed_at`` and are never edited or removed by the
  recurrence operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from typing import Any

from ..entities import (
    MAX_MATERIALIZED_FUTURE_COUNT,
    MIN_MATERIALIZED_FUTURE_COUNT,
    CancelFutureResult,
    RecurrenceAnchor,
    RecurrenceCreateResult,
    RecurrenceRule,
    RecurrenceRuleUpdate,
    RecurrenceSeries,
    RecurrenceTemplate,
    ScopedTaskUpdateResult,
    SeriesFutureOccurrence,
    SeriesResizeResult,
    SeriesState,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
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
from .tasks import apply_task_update, delete_shadow, populate_task, reconcile_shadow

SCOPE_CURRENT = "current"
"""Edit scope: only the occurrence named in the request path."""

SCOPE_ALL_FUTURE = "all-future"
"""Edit scope: every open occurrence after the selected one, plus the template."""

_TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.CANCELED)

_CONTENT_FIELDS = (
    "title",
    "description",
    "priority",
    "project_id",
    "clear_project_id",
    "scheduled_duration",
)

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
    write therefore resumes at the first missing index.
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
        uid = recurrence_occurrence_uid(series.uid, index)
        clash = store.get_task(uid)
        if clash is not None and (
            clash.recurrence_series_uid != series.uid or clash.recurrence_index != index
        ):
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


def reconcile_series(store: RecurrenceStore, series_uid: str) -> None:
    """Restore the series window after an occurrence changed state.

    Promotes the next open occurrence when the current one became
    terminal, then materializes enough tail occurrences to restore the
    configured number of open futures.
    """
    series = store.get_series(series_uid)
    if series is None or not series.active:
        return
    tasks = store.list_series_tasks(series_uid)
    current, _futures, _history = resolve_series_occurrences(series, tasks)
    now = now_utc()
    if current is None:
        # Every materialized occurrence is terminal: the series has
        # nothing left to renew.
        series.active = False
        series.modified = now
        store.save_series(series)
        return
    changed = False
    if current.recurrence_index is not None and current.recurrence_index != series.current_index:
        series.current_index = current.recurrence_index
        changed = True
    created = _materialize(store, series, tasks, target_count=series.materialized_future_count, now=now)
    if created or changed:
        series.modified = now
        store.save_series(series)


def create_recurrent_task(
    store: RecurrenceStore,
    data: TaskCreate,
    *,
    rule: RecurrenceRule,
    materialized_future_count: int,
) -> RecurrenceCreateResult:
    """Create a recurrent task: the series, the current occurrence, and N futures.

    Raises:
        ValueError: When the horizon is out of range or the anchor
            field is missing from the task.
    """
    if not (MIN_MATERIALIZED_FUTURE_COUNT <= materialized_future_count <= MAX_MATERIALIZED_FUTURE_COUNT):
        raise ValueError(
            f"materialized_future_count must be between {MIN_MATERIALIZED_FUTURE_COUNT} "
            f"and {MAX_MATERIALIZED_FUTURE_COUNT}, got {materialized_future_count}",
        )
    now = now_utc()
    anchor_date = anchor_value_date(data, rule.anchor)
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
            task = build_occurrence(series, index, now)
            store.put_task(task)
            created_uids.append(task.uid)
        series.highest_materialized_index = materialized_future_count
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


def _content_only_update(data: TaskUpdate) -> TaskUpdate:
    """Return the content part of an update (no dates, no status)."""
    dump = data.model_dump(exclude_unset=True)
    fields = {key: dump[key] for key in _CONTENT_FIELDS if key in dump}
    return TaskUpdate(**fields)


def _touched_date_fields(data: TaskUpdate) -> dict[str, str]:
    """Map every date field the update touches to ``set`` or ``clear``.

    A field the update does not mention is absent from the result: its
    template offset and the dates of the materialized occurrences stay
    as they are.
    """
    dump = data.model_dump(exclude_unset=True)
    actions: dict[str, str] = {}
    for field, clear_flag in (
        ("assigned_date", "clear_assigned_date"),
        ("hard_deadline", None),
        ("scheduled_start", "clear_scheduled_start"),
    ):
        if field in dump:
            actions[field] = "clear" if dump[field] is None else "set"
        elif clear_flag is not None and dump.get(clear_flag, False):
            actions[field] = "clear"
    return actions


def _field_date(task: Task, field: str) -> date | None:
    """Return the local calendar date of one of the task's date fields."""
    if field == "assigned_date":
        return task.assigned_date
    if field == "hard_deadline":
        return _local_date(task.hard_deadline)
    return _local_date(task.scheduled_start)


def _rebuild_dates(task: Task, series: RecurrenceSeries, index: int) -> None:
    """Recompute a future occurrence's dates from the series template."""
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


def update_task_scoped(
    store: RecurrenceStore,
    uid: str,
    data: TaskUpdate,
    *,
    scope: str = SCOPE_CURRENT,
    rule_change: RecurrenceRuleUpdate | None = None,
) -> ScopedTaskUpdateResult:
    """Update a task with an explicit edit scope.

    ``current`` touches only the selected occurrence. ``all-future``
    touches every open occurrence after the selected one and updates
    the series template so later occurrences inherit the change.

    Raises:
        ValueError: When the scope is unknown, the task is not part of
            a series (for ``all-future``), or the request tries to
            change the status in future scope.
    """
    task = store.get_task(uid)
    if task is None:
        raise ValueError(f"task {uid} not found")
    if scope not in (SCOPE_CURRENT, SCOPE_ALL_FUTURE):
        raise ValueError(f"unknown edit scope: {scope}")

    if scope == SCOPE_CURRENT:
        updated = apply_task_update(task.model_copy(deep=True), data)
        store.put_task(updated)
        if updated.recurrence_series_uid is not None:
            reconcile_series(store, updated.recurrence_series_uid)
        return ScopedTaskUpdateResult(task=updated, scope=scope, affected_uids=[updated.uid])

    if task.recurrence_series_uid is None:
        raise ValueError("task is not part of a recurrence series")
    if data.status is not None:
        raise ValueError("status changes are per-occurrence and cannot use the all-future scope")
    series = store.get_series(task.recurrence_series_uid)
    if series is None:
        raise ValueError("recurrence series not found")

    selected_after = apply_task_update(task.model_copy(deep=True), data)
    dump = data.model_dump(exclude_unset=True)
    actions = _touched_date_fields(data)
    tasks = store.list_series_tasks(series.uid)
    selected_index = _index_of(task)
    affected = [t for t in tasks if not is_terminal(t) and _index_of(t) > selected_index]
    affected.sort(key=lambda t: (_index_of(t), t.uid))

    new_rule = series.rule.model_copy()
    if rule_change is not None:
        if rule_change.frequency is not None:
            new_rule.frequency = rule_change.frequency
        if rule_change.interval is not None:
            new_rule.interval = rule_change.interval
        if rule_change.anchor is not None:
            new_rule.anchor = rule_change.anchor

    new_start_date, new_start_index = _reanchor(series, task, selected_after, new_rule, actions)

    # A re-anchor happens when the chain moves to another occurrence
    # (the selected one is not the chain anchor) or when the anchored
    # FIELD changed. The second case matters even when the selected
    # occurrence sits at the chain anchor: the template offsets are
    # relative to the anchored field, so they must be recomputed from
    # the new anchor instead of staying relative to the old one.
    anchor_field_changed = new_rule.anchor.value != series.rule.anchor.value
    reanchored = anchor_field_changed or new_start_index != series.start_index

    template = series.template.model_copy(deep=True)
    if "title" in dump:
        template.title = selected_after.title
    if "description" in dump:
        template.description = selected_after.description
    if "priority" in dump:
        template.priority = selected_after.priority
    if "project_id" in dump or "clear_project_id" in dump:
        template.project_id = selected_after.project_id
    if "scheduled_duration" in dump:
        template.scheduled_duration = selected_after.scheduled_duration
    _apply_date_offsets(
        template,
        series=series,
        selected=task,
        selected_after=selected_after,
        actions=actions,
        new_start_date=new_start_date,
        new_anchor=new_rule.anchor.value,
        reanchored=reanchored,
    )

    series.rule = new_rule
    series.start_date = new_start_date
    series.start_index = new_start_index
    series.template = template

    content_update = _content_only_update(data)
    now = now_utc()
    affected_uids: list[str] = []
    for occurrence in affected:
        updated = apply_task_update(occurrence.model_copy(deep=True), content_update)
        _rebuild_dates(updated, series, _index_of(occurrence))
        updated.modified = now
        store.put_task(updated)
        affected_uids.append(updated.uid)

    series.modified = now
    store.save_series(series)
    return ScopedTaskUpdateResult(task=task, scope=scope, affected_uids=affected_uids)


def _reanchor(
    series: RecurrenceSeries,
    selected: Task,
    selected_after: Task,
    new_rule: RecurrenceRule,
    actions: dict[str, str],
) -> tuple[date, int]:
    """Return the new (anchor date, anchor index) of the future chain.

    The chain keeps its anchor date and only shifts by the day delta
    the edit applied to the anchored field, so the monthly clamping day
    of the series is preserved. Changing the anchored *field* re-anchors
    the chain at the selected occurrence.
    """
    old_anchor = series.rule.anchor.value
    new_anchor = new_rule.anchor.value
    selected_index = _index_of(selected)

    if new_anchor != old_anchor:
        base = _field_date(selected_after, new_anchor)
        if base is None:
            raise ValueError(f"cannot re-anchor on '{new_anchor}': the selected occurrence has no such date")
        return base, selected_index

    anchor_delta = 0
    if old_anchor in actions:
        if actions[old_anchor] == "clear":
            raise ValueError(f"the anchor field '{old_anchor}' of a recurrence series cannot be cleared")
        old_value = _field_date(selected, old_anchor)
        new_value = _field_date(selected_after, old_anchor)
        if old_value is not None and new_value is not None:
            anchor_delta = (new_value - old_value).days
    return series.start_date + timedelta(days=anchor_delta), series.start_index


def _local_time_for(field: str, task: Task) -> time | None:
    """Return the local wall-clock time of one of the datetime fields."""
    if field == "hard_deadline":
        return to_local(task.hard_deadline).time() if task.hard_deadline is not None else None
    if field == "scheduled_start":
        return to_local(task.scheduled_start).time() if task.scheduled_start is not None else None
    return None


def _apply_date_offsets(
    template: RecurrenceTemplate,
    *,
    series: RecurrenceSeries,
    selected: Task,
    selected_after: Task,
    actions: dict[str, str],
    new_start_date: date,
    new_anchor: str,
    reanchored: bool,
) -> None:
    """Update the template's date offsets after an all-future edit.

    A field the edit did not touch keeps its offset: the offsets are
    relative to the chain anchor, so a field shift is expressed as a
    day delta applied to the offset, not as an absolute date.

    Every touched datetime field also refreshes its local wall-clock
    time, including the anchored one: an edit that moves the scheduled
    start from 12:00 to 18:00 must move the future occurrences to
    18:00 as well. A cleared field clears its local time.

    A re-anchor recomputes every offset (and every local time) from
    the selected occurrence, because the anchor the offsets are
    relative to has changed.
    """
    old_anchor = series.rule.anchor.value
    for field in _DATE_FIELDS:
        offset_attr = _DATE_FIELD_OFFSETS[field]
        local_time_attr = _DATE_FIELD_LOCAL_TIME.get(field)
        if reanchored:
            value = _field_date(selected_after, field)
            setattr(template, offset_attr, None if value is None else (value - new_start_date).days)
            if local_time_attr is not None:
                setattr(template, local_time_attr, _local_time_for(field, selected_after))
            continue
        if field not in actions:
            continue
        # The field was touched. Its local time follows the edit, be
        # it the anchored field, a plain field, or a field that was
        # cleared (which clears the local time with it).
        if local_time_attr is not None:
            setattr(template, local_time_attr, _local_time_for(field, selected_after))
        if field == old_anchor:
            value = _field_date(selected_after, field)
            setattr(template, offset_attr, None if value is None else 0)
            continue
        if actions[field] == "clear":
            setattr(template, offset_attr, None)
            continue
        old_value = _field_date(selected, field)
        new_value = _field_date(selected_after, field)
        if new_value is None:
            setattr(template, offset_attr, None)
        elif old_value is None:
            setattr(template, offset_attr, (new_value - new_start_date).days)
        else:
            current = getattr(template, offset_attr)
            base = current if current is not None else 0
            setattr(template, offset_attr, base + (new_value - old_value).days)


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


def cancel_future(store: RecurrenceStore, uid: str) -> CancelFutureResult:
    """Cancel every future occurrence of the selected task's series.

    The selected occurrence and all history stay untouched. The series
    is deactivated so no replacement future is materialized. The action
    is idempotent.

    Raises:
        ValueError: When the task does not exist or is not part of a
            recurrence series.
    """
    task = store.get_task(uid)
    if task is None:
        raise ValueError(f"task {uid} not found")
    if task.recurrence_series_uid is None:
        raise ValueError("task is not part of a recurrence series")
    series = store.get_series(task.recurrence_series_uid)
    if series is None:
        raise ValueError("recurrence series not found")

    now = now_utc()
    selected_index = _index_of(task)
    tasks = store.list_series_tasks(series.uid)
    canceled: list[str] = []
    for occurrence in tasks:
        if _index_of(occurrence) <= selected_index or is_terminal(occurrence):
            continue
        occurrence.status = TaskStatus.CANCELED
        occurrence.completed_at = None
        occurrence.modified = now
        store.put_task(occurrence)
        store.delete_task_shadow(occurrence.uid)
        canceled.append(occurrence.uid)

    series.active = False
    series.modified = now
    store.save_series(series)
    return CancelFutureResult(series_uid=series.uid, canceled_uids=canceled, active=False)


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


def series_state(store: RecurrenceStore, series_uid: str) -> SeriesState | None:
    """Return the inspection state of a series, or None when unknown."""
    series = store.get_series(series_uid)
    if series is None:
        return None
    tasks = store.list_series_tasks(series_uid)
    current, futures, _history = resolve_series_occurrences(series, tasks)
    return SeriesState(
        uid=series.uid,
        frequency=series.rule.frequency,
        interval=series.rule.interval,
        anchor=series.rule.anchor,
        start_date=series.start_date,
        start_index=series.start_index,
        materialized_future_count=series.materialized_future_count,
        active=series.active,
        current_task_uid=current.uid if current is not None else None,
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
