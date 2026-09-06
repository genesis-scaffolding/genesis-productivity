"""Shared iCalendar parsing and serialization helpers.

Each entity type gets a `*_from_ical` (ical component -> entity) and
`*_to_ical` (entity -> icalendar component) pair. The entity objects are
plain pydantic models, and the icalendar components are icalendar-library
instances that the caldav library understands.

These functions are pure: they do not talk to the network. Callers fetch
the icalendar component from a caldav object, call `*_from_ical` to get
an entity, mutate, then call `*_to_ical` to write the mutation back to
the component before calling save().
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import icalendar

from ..entities import (
    Event,
    EventStatus,
    Project,
    ProjectStatus,
    Task,
    TaskStatus,
    TimeLogEntry,
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
    """Return a datetime from an ical property, or None."""
    val = _to_python(val)
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
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
    return (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


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
        status=ProjectStatus(
            to_str(component.get("status")) or ProjectStatus.NEEDS_ACTION.value
        ),
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
        summary=to_str(component.get("summary")) or "",
        description=to_str(component.get("description")),
        hard_deadline=to_datetime(component.get("due")),
        assigned_date=to_date(component.get("x-assigned-date")),
        scheduled_start=to_datetime(component.get("dtstart")),
        scheduled_duration=to_int(component.get("x-scheduled-duration")),
        priority=to_int(component.get("priority")),
        status=TaskStatus(
            to_str(component.get("status")) or TaskStatus.NEEDS_ACTION.value
        ),
        project_id=to_str(component.get("x-project-id")) or None,
        completed_at=to_datetime(component.get("completed")),
        created=to_datetime(component.get("created")),
        modified=to_datetime(component.get("last-modified")),
    )


def task_to_ical(task: Task) -> icalendar.Component:
    """Build a VTODO ical component from a Task entity."""
    component = icalendar.Todo()
    component.add("uid", task.uid)
    component.add("summary", task.summary)
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
    return component


# -----------------------------------------------------------------------------
# Event
# -----------------------------------------------------------------------------


def event_from_ical(component: icalendar.Component) -> Event:
    """Build an Event entity from a VEVENT ical component."""
    start = to_datetime(component.get("dtstart"))
    end = to_datetime(component.get("dtend"))
    if start is None or end is None:
        raise ValueError(
            f"VEVENT {component.get('uid')} is missing DTSTART or DTEND"
        )
    return Event(
        uid=to_str(component.get("uid")) or "",
        summary=to_str(component.get("summary")) or "",
        start=start,
        end=end,
        location=to_str(component.get("location")),
        description=to_str(component.get("description")),
        status=EventStatus(
            to_str(component.get("status")) or EventStatus.CONFIRMED.value
        ),
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
    """Build a TimeLogEntry from a VEVENT ical component."""
    start = to_datetime(component.get("dtstart"))
    end = to_datetime(component.get("dtend"))
    task_id = to_str(component.get("x-task-id")) or ""
    if start is None or end is None:
        raise ValueError(
            f"time_log VEVENT {component.get('uid')} is missing DTSTART or DTEND"
        )
    if not task_id:
        raise ValueError(
            f"time_log VEVENT {component.get('uid')} is missing X-TASK-ID"
        )
    return TimeLogEntry(
        uid=to_str(component.get("uid")) or "",
        start=start,
        end=end,
        description=to_str(component.get("description")),
        task_id=task_id,
        created=to_datetime(component.get("created")),
    )


def time_log_to_ical(entry: TimeLogEntry) -> icalendar.Component:
    """Build a VEVENT ical component from a TimeLogEntry."""
    component = icalendar.Event()
    component.add("uid", entry.uid)
    component.add("summary", "")  # VTODO-equivalent summary; required by some clients
    component.add("dtstart", entry.start)
    component.add("dtend", entry.end)
    component.add("dtstamp", entry.created or now_utc())
    if entry.description:
        component.add("description", entry.description)
    component.add("x-task-id", entry.task_id)
    if entry.created:
        component.add("created", entry.created)
    return component


# -----------------------------------------------------------------------------
# UID generation
# -----------------------------------------------------------------------------


def new_uid() -> str:
    """Generate a new UID for a freshly-created ical object."""
    return f"{uuid.uuid4()}@genesis"
