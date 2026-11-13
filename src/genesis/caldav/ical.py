"""Shared iCalendar parsing and serialization helpers.

Each entity type gets a `*_from_ical` (ical component -> entity) and
`*_to_ical` (entity -> icalendar component) pair. The entity objects are
plain pydantic models, and the icalendar components are icalendar-library
instances that the caldav library understands.

These functions are pure: they do not talk to the network. Callers fetch
the icalendar component from a caldav object, call `*_from_ical` to get
an entity, mutate, then call `*_to_ical` to write the mutation back to
the component before calling save().

The recurrence series of ADR-033 lives here too: the series record is a
VTODO in the isolated ``recurrences`` collection, and the task
occurrences carry their series UID, occurrence date, and occurrence
index as ``X-`` properties.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from datetime import UTC, date, datetime
from typing import Any

import icalendar

from ..category.enums import TimeLogCategory
from ..entities import (
    Event,
    EventStatus,
    Project,
    ProjectStatus,
    RecurrenceAnchor,
    RecurrenceFrequency,
    RecurrenceRule,
    RecurrenceSeries,
    RecurrenceTemplate,
    Task,
    TaskStatus,
    TimeLogEntry,
    TimeLogMode,
    TimeLogStatus,
    XpEvent,
    XpEventKind,
)

# -----------------------------------------------------------------------------
# Property helpers
# -----------------------------------------------------------------------------


def _to_python(val: Any) -> Any:
    """Extract a Python value from an icalendar property wrapper."""
    if val is None:
        return None
    try:
        return val.dt
    except AttributeError:
        pass
    try:
        return val.to_ical().decode("utf-8")
    except AttributeError:
        pass
    return val


def to_str(val: Any) -> str | None:
    """Return a single-value string from an ical property, or None."""
    if val is None:
        return None
    return str(val)


def to_datetime(val: Any) -> datetime | None:
    """Return a tz-aware UTC datetime from an ical property, or None.

    Per project convention in ``genesis.tz``, all entity datetime
    fields hold UTC. Reads strip any TZID and convert to UTC. Floating
    time (no tzinfo) is treated as already-UTC for backward
    compatibility with any legacy entries; new writes always pass
    tz-aware UTC and serialize as ``Z``.
    """
    val = _to_python(val)
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            # Legacy floating-time entry: treat as UTC for safety.
            return val.replace(tzinfo=UTC)
        return val.astimezone(UTC)
    return None


def to_date(val: Any) -> date | None:
    """Return a date from an ical property, accepting date or datetime."""
    val = _to_python(val)
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val)
        except ValueError:
            return None
    return None


def to_int(val: Any) -> int | None:
    """Return an int from an ical property, or None."""
    val = _to_python(val)
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def escape_text(text: str) -> str:
    """Escape special characters in an iCal text value."""
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def now_utc() -> datetime:
    """Return the current UTC time with tzinfo set."""
    return datetime.now(UTC)


# -----------------------------------------------------------------------------
# Project
# -----------------------------------------------------------------------------


def project_from_ical(component: icalendar.Component) -> Project:
    """Build a Project entity from a VTODO ical component."""
    return Project(
        uid=to_str(component.get("uid")) or "",
        name=to_str(component.get("summary")) or "",
        description=to_str(component.get("description")),
        start_date=to_date(component.get("dtstart")),
        deadline=to_date(component.get("due")),
        status=ProjectStatus(to_str(component.get("status")) or ProjectStatus.NEEDS_ACTION.value),
        created=to_datetime(component.get("created")),
        modified=to_datetime(component.get("last-modified")),
    )


def project_to_ical(project: Project) -> icalendar.Component:
    """Build a VTODO ical component from a Project entity."""
    component = icalendar.Todo()
    component.add("uid", project.uid)
    component.add("summary", project.name)
    component.add("dtstamp", project.modified or now_utc())
    if project.description:
        component.add("description", project.description)
    if project.start_date:
        component.add("dtstart", project.start_date)
    if project.deadline:
        component.add("due", project.deadline)
    component.add("status", project.status.value)
    if project.created:
        component.add("created", project.created)
    if project.modified:
        component.add("last-modified", project.modified)
    return component


# -----------------------------------------------------------------------------
# Task
# -----------------------------------------------------------------------------


def task_from_ical(component: icalendar.Component) -> Task:
    """Build a Task entity from a VTODO ical component."""
    return Task(
        uid=to_str(component.get("uid")) or "",
        title=to_str(component.get("summary")) or "",
        description=to_str(component.get("description")),
        hard_deadline=to_datetime(component.get("due")),
        assigned_date=to_date(component.get("x-assigned-date")),
        scheduled_start=to_datetime(component.get("dtstart")),
        scheduled_duration=to_int(component.get("x-scheduled-duration")),
        priority=to_int(component.get("priority")),
        status=TaskStatus(to_str(component.get("status")) or TaskStatus.NEEDS_ACTION.value),
        project_id=to_str(component.get("x-project-id")) or None,
        completed_at=to_datetime(component.get("completed")),
        created=to_datetime(component.get("created")),
        modified=to_datetime(component.get("last-modified")),
        recurrence_series_uid=to_str(component.get("x-recurrence-series-id")) or None,
        recurrence_occurrence=to_date(component.get("x-recurrence-occurrence")),
        recurrence_index=to_int(component.get("x-recurrence-index")),
    )


def task_to_ical(task: Task) -> icalendar.Component:
    """Build a VTODO ical component from a Task entity."""
    component = icalendar.Todo()
    component.add("uid", task.uid)
    component.add("summary", task.title)
    component.add("dtstamp", task.modified or now_utc())
    if task.description:
        component.add("description", task.description)
    if task.hard_deadline:
        component.add("due", task.hard_deadline)
    if task.assigned_date:
        component.add("x-assigned-date", task.assigned_date)
    if task.scheduled_start:
        component.add("dtstart", task.scheduled_start)
    if task.scheduled_duration is not None:
        component.add("x-scheduled-duration", task.scheduled_duration)
    if task.priority is not None:
        component.add("priority", task.priority)
    component.add("status", task.status.value)
    if task.project_id:
        component.add("x-project-id", task.project_id)
    if task.completed_at:
        component.add("completed", task.completed_at)
    if task.created:
        component.add("created", task.created)
    if task.modified:
        component.add("last-modified", task.modified)
    if task.recurrence_series_uid:
        component.add("x-recurrence-series-id", task.recurrence_series_uid)
    if task.recurrence_occurrence:
        component.add("x-recurrence-occurrence", task.recurrence_occurrence)
    if task.recurrence_index is not None:
        component.add("x-recurrence-index", task.recurrence_index)
    return component


# -----------------------------------------------------------------------------
# Recurrence series (ADR-033)
# -----------------------------------------------------------------------------
#
# A series is stored as a VTODO in the isolated ``recurrences``
# collection. It is never returned as a user task. The template is
# carried as one JSON property: the template has a dozen fields and
# flattening them into individual X- properties would double the
# serialization surface for no gain.

_X_SERIES_ID = "x-recurrence-series-id"
_X_FREQUENCY = "x-recurrence-frequency"
_X_INTERVAL = "x-recurrence-interval"
_X_ANCHOR = "x-recurrence-anchor"
_X_START_DATE = "x-recurrence-start-date"
_X_START_INDEX = "x-recurrence-start-index"
_X_END_DATE = "x-recurrence-end-date"
_X_FUTURE_COUNT = "x-recurrence-future-count"
_X_HIGHEST_INDEX = "x-recurrence-highest-index"
_X_CURRENT_INDEX = "x-recurrence-current-index"
_X_ACTIVE = "x-recurrence-active"
_X_STOP_REASON = "x-recurrence-stop-reason"
_X_TEMPLATE = "x-recurrence-template"


def recurrence_series_from_ical(component: icalendar.Component) -> RecurrenceSeries:
    """Build a RecurrenceSeries entity from a VTODO ical component.

    Raises:
        ValueError: When a required recurrence property is missing or
            malformed.
    """
    uid = to_str(component.get("uid"))
    if uid is None:
        raise ValueError("recurrence series VTODO is missing UID")
    frequency_raw = to_str(component.get(_X_FREQUENCY))
    anchor_raw = to_str(component.get(_X_ANCHOR))
    start_date = to_date(component.get(_X_START_DATE))
    if frequency_raw is None:
        raise ValueError(f"recurrence series {uid} is missing {_X_FREQUENCY.upper()}")
    if anchor_raw is None:
        raise ValueError(f"recurrence series {uid} is missing {_X_ANCHOR.upper()}")
    if start_date is None:
        raise ValueError(f"recurrence series {uid} is missing {_X_START_DATE.upper()}")
    template_raw = to_str(component.get(_X_TEMPLATE))
    if template_raw is None:
        raise ValueError(f"recurrence series {uid} is missing {_X_TEMPLATE.upper()}")
    try:
        template = RecurrenceTemplate.model_validate_json(template_raw)
    except Exception as exc:
        raise ValueError(f"recurrence series {uid} has an invalid template: {exc}") from exc
    try:
        frequency = RecurrenceFrequency(frequency_raw)
        anchor = RecurrenceAnchor(anchor_raw)
    except ValueError as exc:
        raise ValueError(f"recurrence series {uid} has an invalid rule: {exc}") from exc
    return RecurrenceSeries(
        uid=uid,
        rule=RecurrenceRule(
            frequency=frequency,
            interval=to_int(component.get(_X_INTERVAL)) or 1,
            anchor=anchor,
        ),
        start_date=start_date,
        start_index=to_int(component.get(_X_START_INDEX)) or 0,
        template=template,
        materialized_future_count=to_int(component.get(_X_FUTURE_COUNT)) or 1,
        highest_materialized_index=to_int(component.get(_X_HIGHEST_INDEX)) or 0,
        current_index=to_int(component.get(_X_CURRENT_INDEX)) or 0,
        end_date=to_date(component.get(_X_END_DATE)),
        stop_reason=to_str(component.get(_X_STOP_REASON)) or None,
        active=(to_str(component.get(_X_ACTIVE)) or "true").lower() != "false",
        created=to_datetime(component.get("created")),
        modified=to_datetime(component.get("last-modified")),
    )


def recurrence_series_to_ical(series: RecurrenceSeries) -> icalendar.Component:
    """Build a VTODO ical component from a RecurrenceSeries entity."""
    component = icalendar.Todo()
    component.add("uid", series.uid)
    component.add("summary", series.template.title)
    component.add("dtstamp", series.modified or now_utc())
    component.add("status", TaskStatus.NEEDS_ACTION.value)
    component.add(_X_SERIES_ID, series.uid)
    component.add(_X_FREQUENCY, series.rule.frequency.value)
    component.add(_X_INTERVAL, series.rule.interval)
    component.add(_X_ANCHOR, series.rule.anchor.value)
    component.add(_X_START_DATE, series.start_date)
    component.add(_X_START_INDEX, series.start_index)
    if series.end_date is not None:
        component.add(_X_END_DATE, series.end_date)
    component.add(_X_FUTURE_COUNT, series.materialized_future_count)
    component.add(_X_HIGHEST_INDEX, series.highest_materialized_index)
    component.add(_X_CURRENT_INDEX, series.current_index)
    component.add(_X_ACTIVE, "true" if series.active else "false")
    if series.stop_reason is not None:
        component.add(_X_STOP_REASON, series.stop_reason)
    component.add(_X_TEMPLATE, series.template.model_dump_json())
    if series.created:
        component.add("created", series.created)
    if series.modified:
        component.add("last-modified", series.modified)
    return component


def populate_series(component: icalendar.Component, series: RecurrenceSeries) -> None:
    """Apply series fields to an existing icalendar Component in place.

    DTSTAMP, CREATED, and LAST-MODIFIED are intentionally not managed
    here; the CalDAV server is responsible for setting them on PUT.
    """
    replacement = recurrence_series_to_ical(series)

    def set_or_delete(key: str, value: object) -> None:
        if key in component:
            del component[key]
        if value is not None:
            component.add(key, value)

    for key in (
        "uid",
        "summary",
        "status",
        _X_SERIES_ID,
        _X_FREQUENCY,
        _X_INTERVAL,
        _X_ANCHOR,
        _X_START_DATE,
        _X_START_INDEX,
        _X_END_DATE,
        _X_FUTURE_COUNT,
        _X_HIGHEST_INDEX,
        _X_CURRENT_INDEX,
        _X_ACTIVE,
        _X_STOP_REASON,
        _X_TEMPLATE,
    ):
        set_or_delete(key, replacement.get(key))


# -----------------------------------------------------------------------------
# Event
# -----------------------------------------------------------------------------


def event_from_ical(component: icalendar.Component) -> Event:
    """Build an Event entity from a VEVENT ical component."""
    start = to_datetime(component.get("dtstart"))
    end = to_datetime(component.get("dtend"))
    if start is None or end is None:
        raise ValueError(f"VEVENT {component.get('uid')} is missing DTSTART or DTEND")
    return Event(
        uid=to_str(component.get("uid")) or "",
        summary=to_str(component.get("summary")) or "",
        start=start,
        end=end,
        location=to_str(component.get("location")),
        description=to_str(component.get("description")),
        status=EventStatus(to_str(component.get("status")) or EventStatus.CONFIRMED.value),
        task_id=to_str(component.get("x-task-id")) or None,
        created=to_datetime(component.get("created")),
        modified=to_datetime(component.get("last-modified")),
    )


def event_to_ical(event: Event) -> icalendar.Component:
    """Build a VEVENT ical component from an Event entity."""
    component = icalendar.Event()
    component.add("uid", event.uid)
    component.add("summary", event.summary)
    component.add("dtstart", event.start)
    component.add("dtend", event.end)
    component.add("dtstamp", event.modified or now_utc())
    if event.location:
        component.add("location", event.location)
    if event.description:
        component.add("description", event.description)
    component.add("status", event.status.value)
    if event.task_id:
        component.add("x-task-id", event.task_id)
    if event.created:
        component.add("created", event.created)
    if event.modified:
        component.add("last-modified", event.modified)
    return component


# -----------------------------------------------------------------------------
# TimeLogEntry
# -----------------------------------------------------------------------------


def time_log_from_ical(component: icalendar.Component) -> TimeLogEntry:
    """Build a TimeLogEntry from a VTODO ical component.

    Required fields: DTSTART, X-MODE. SUMMARY is required for round-tripping
    but a missing value falls back to an empty label rather than failing.
    """
    start = to_datetime(component.get("dtstart"))
    mode_str = to_str(component.get("x-mode"))
    if start is None:
        raise ValueError(f"time_log VTODO {component.get('uid')} is missing DTSTART")
    if mode_str is None:
        raise ValueError(f"time_log VTODO {component.get('uid')} is missing X-MODE")
    status_str = to_str(component.get("status"))
    status = TimeLogStatus(status_str) if status_str in {s.value for s in TimeLogStatus} else TimeLogStatus.NEEDS_ACTION
    cat_str = to_str(component.get("x-category"))
    try:
        category = TimeLogCategory(cat_str) if cat_str else TimeLogCategory.OTHER
    except ValueError:
        category = TimeLogCategory.OTHER
    return TimeLogEntry(
        uid=to_str(component.get("uid")) or "",
        label=to_str(component.get("summary")) or "",
        start=start,
        due=to_datetime(component.get("due")),
        completed_at=to_datetime(component.get("completed")),
        status=status,
        mode=TimeLogMode(mode_str),
        task_id=to_str(component.get("x-task-id")) or None,
        category=category,
        description=to_str(component.get("description")),
        created=to_datetime(component.get("created")),
        modified=to_datetime(component.get("last-modified")),
        last_milestone_minutes=to_int(component.get("x-last-milestone-minutes")) or 0,
    )


def time_log_to_ical(entry: TimeLogEntry) -> icalendar.Component:
    """Build a VTODO ical component from a TimeLogEntry."""
    component = icalendar.Todo()
    component.add("uid", entry.uid)
    component.add("summary", entry.label)
    component.add("dtstart", entry.start)
    component.add("status", entry.status.value)
    component.add("x-mode", entry.mode.value)
    if entry.due is not None:
        component.add("due", entry.due)
    if entry.completed_at is not None:
        component.add("completed", entry.completed_at)
    if entry.task_id:
        component.add("x-task-id", entry.task_id)
    if entry.category != TimeLogCategory.OTHER:
        component.add("x-category", entry.category.value)
    if entry.last_milestone_minutes > 0:
        component.add("x-last-milestone-minutes", entry.last_milestone_minutes)
    if entry.description:
        component.add("description", entry.description)
    component.add("dtstamp", entry.modified or now_utc())
    if entry.created:
        component.add("created", entry.created)
    if entry.modified:
        component.add("last-modified", entry.modified)
    return component


# -----------------------------------------------------------------------------
# XpEvent
# -----------------------------------------------------------------------------


def xp_event_from_ical(component: icalendar.Component) -> XpEvent:
    """Build an XpEvent entity from a VEVENT ical component.

    Required: UID, X-STAT-ID, X-DELTA, X-KIND, X-IDEMPOTENCY-KEY,
    DTSTART. ``reason`` falls back to an empty string if missing
    rather than failing the round trip.
    """
    uid = to_str(component.get("uid"))
    stat_id = to_str(component.get("x-stat-id"))
    delta = to_int(component.get("x-delta"))
    kind_str = to_str(component.get("x-kind"))
    idempotency_key = to_str(component.get("x-idempotency-key"))
    occurred_at = to_datetime(component.get("dtstart"))
    if uid is None:
        raise ValueError("xp_event VEVENT missing UID")
    if stat_id is None:
        raise ValueError(f"xp_event VEVENT {uid} is missing X-STAT-ID")
    if delta is None:
        raise ValueError(f"xp_event VEVENT {uid} is missing X-DELTA")
    if kind_str is None:
        raise ValueError(f"xp_event VEVENT {uid} is missing X-KIND")
    if idempotency_key is None:
        raise ValueError(f"xp_event VEVENT {uid} is missing X-IDEMPOTENCY-KEY")
    if occurred_at is None:
        raise ValueError(f"xp_event VEVENT {uid} is missing DTSTART")
    try:
        kind = XpEventKind(kind_str)
    except ValueError as e:
        raise ValueError(f"xp_event VEVENT {uid} has invalid X-KIND {kind_str!r}") from e
    return XpEvent(
        uid=uid,
        stat_id=stat_id,
        delta=delta,
        kind=kind,
        source_task_id=to_str(component.get("x-source-task-id")) or None,
        source_session_id=to_str(component.get("x-source-session-id")) or None,
        reason=to_str(component.get("x-reason")) or "",
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
        created=to_datetime(component.get("created")),
        modified=to_datetime(component.get("last-modified")),
    )


def xp_event_to_ical(event: XpEvent) -> icalendar.Component:
    """Build a VEVENT ical component from an XpEvent.

    DTSTART carries the ``occurred_at`` timestamp. SUMMARY is the
    reason string so the event is human-readable in a CalDAV
    viewer. No DTEND: events are instantaneous.
    """
    component = icalendar.Event()
    component.add("uid", event.uid)
    component.add("summary", event.reason or event.stat_id)
    component.add("dtstart", event.occurred_at)
    component.add("dtend", event.occurred_at)
    component.add("x-stat-id", event.stat_id)
    component.add("x-delta", event.delta)
    component.add("x-kind", event.kind.value)
    component.add("x-idempotency-key", event.idempotency_key)
    if event.source_task_id:
        component.add("x-source-task-id", event.source_task_id)
    if event.source_session_id:
        component.add("x-source-session-id", event.source_session_id)
    if event.reason:
        component.add("x-reason", event.reason)
    component.add("dtstamp", event.modified or now_utc())
    if event.created:
        component.add("created", event.created)
    if event.modified:
        component.add("last-modified", event.modified)
    return component


# -----------------------------------------------------------------------------
# UID generation
# -----------------------------------------------------------------------------


_ALPHABET: str = string.ascii_lowercase + string.digits
_TAIL_LENGTH: int = 6


def new_uid(prefix: str) -> str:
    """Generate a new typed short UID for a freshly-created ical object.

    Format: ``{prefix}-{tail}`` where ``tail`` is ``_TAIL_LENGTH`` chars
    from ``_ALPHABET``. The prefix encodes the entity kind (``t-`` for
    task, ``p-`` for project, etc.). Drawn from ``secrets`` for
    cryptographic strength.

    See ``docs/adr/adr-016-short-typed-uid.md``.
    """
    tail = "".join(secrets.choice(_ALPHABET) for _ in range(_TAIL_LENGTH))
    return f"{prefix}-{tail}"


def recurrence_occurrence_uid(series_uid: str, index: int) -> str:
    """Return the deterministic UID of one recurrence occurrence.

    The logical identity of an occurrence is ``(series_uid,
    recurrence_index)`` (ADR-033). Deriving the public UID from that
    identity keeps ADR-016's ``t-`` plus six-character base36 shape
    while turning the UID into a get-or-create key: a retried or
    concurrent writer addresses the same CalDAV resource instead of
    creating a second object for one logical occurrence.
    """
    digest = hashlib.sha256(f"{series_uid}:{index}".encode()).digest()
    value = int.from_bytes(digest[:8], "big")
    tail = ""
    for _ in range(_TAIL_LENGTH):
        tail = _ALPHABET[value % len(_ALPHABET)] + tail
        value //= len(_ALPHABET)
    return f"t-{tail}"
