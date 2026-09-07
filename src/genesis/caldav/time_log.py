"""Time log entry CRUD operations for CalDAVFacade.

TimeLogEntry is a VEVENT stored in the time_log collection. Each entry
records a completed work session (e.g. a pomodoro) and carries a FK back
to its source task via X-TASK-ID.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from caldav.lib.error import NotFoundError

from ..entities import TimeLogEntry, TimeLogEntryCreate, TimeLogEntryUpdate
from .ical import new_uid, now_utc, time_log_from_ical, time_log_to_ical

if TYPE_CHECKING:
    from icalendar import Component

    from .facade import CalDAVFacade


def _populate(component: Component, entry: TimeLogEntry) -> None:
    """Apply TimeLogEntry fields to an existing icalendar Component in place.

    DTSTAMP, CREATED, and LAST-MODIFIED are intentionally not managed here;
    the CalDAV server is responsible for setting them on PUT.
    """

    def set_or_delete(key: str, value: object) -> None:
        if key in component:
            del component[key]
        if value is not None:
            component.add(key, value)

    set_or_delete("uid", entry.uid)
    set_or_delete("dtstart", entry.start)
    set_or_delete("dtend", entry.end)
    set_or_delete("x-task-id", entry.task_id)
    set_or_delete("description", entry.description)


def _get_calendar(facade: CalDAVFacade):
    return facade._get_calendar(facade.TIME_LOG_COLLECTION)


def create_time_log_entry(
    facade: CalDAVFacade, data: TimeLogEntryCreate
) -> TimeLogEntry:
    """Create a new time log entry."""
    now = now_utc()
    entry = TimeLogEntry(
        uid=new_uid(),
        start=data.start,
        end=data.end,
        task_id=data.task_id,
        description=data.description,
        created=now,
    )
    _get_calendar(facade).save_event(time_log_to_ical(entry))
    return entry


def get_time_log_entry(facade: CalDAVFacade, uid: str) -> TimeLogEntry | None:
    """Get a time log entry by UID, or None if not found."""
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None
    try:
        return time_log_from_ical(obj.icalendar_component)
    except Exception:
        return None


def list_time_log(
    facade: CalDAVFacade,
    task_id: str | None = None,
    sort_by: list[str] | None = None,
    sort_order: str = "asc",
) -> list[TimeLogEntry]:
    """List time log entries, optionally filtered by task_id."""
    entries: list[TimeLogEntry] = []
    for obj in _get_calendar(facade).objects():
        try:
            component = obj.icalendar_component
        except NotFoundError:
            continue
        if component.name != "VEVENT":
            continue
        try:
            entries.append(time_log_from_ical(component))
        except Exception:
            continue

    if task_id is not None:
        entries = [e for e in entries if e.task_id == task_id]
    if sort_by:
        for key in reversed(sort_by):
            entries.sort(
                key=lambda e, k=key: getattr(e, k, "") or "",
                reverse=(sort_order == "desc"),
            )
    return entries


def update_time_log_entry(
    facade: CalDAVFacade, uid: str, data: TimeLogEntryUpdate
) -> TimeLogEntry | None:
    """Update an existing time log entry in place."""
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None

    try:
        entry = time_log_from_ical(obj.icalendar_component)
    except Exception:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            setattr(entry, key, value)

    _populate(obj.icalendar_component, entry)
    obj.save()
    return entry


def delete_time_log_entry(facade: CalDAVFacade, uid: str) -> bool:
    """Delete a time log entry."""
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return False
    if obj is None:
        return False
    obj.delete()
    return True
