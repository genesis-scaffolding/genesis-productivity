"""Time log entry CRUD operations for CalDAVFacade.

A TimeLogEntry is a VTODO stored in the time_log collection. The same
collection holds both active focus sessions (STATUS != COMPLETED) and
completed historical records (STATUS == COMPLETED). See ADR-014.

Stop flow uses ``stop_time_log_entry`` which mutates the VTODO in place
and PUTs with If-Match so a concurrent stop on another device 412s
cleanly. Discard is a hard DELETE with no audit trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from caldav.lib import error as caldav_error
from caldav.lib.error import NotFoundError

from ..category.enums import TimeLogCategory
from ..entities import (
    TimeLogEntry,
    TimeLogEntryCreate,
    TimeLogEntryUpdate,
    TimeLogStatus,
)
from ..tz import to_utc_optional
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
    set_or_delete("summary", entry.label)
    set_or_delete("dtstart", entry.start)
    set_or_delete("due", entry.due)
    set_or_delete("completed", entry.completed_at)
    set_or_delete("status", entry.status.value)
    set_or_delete("x-mode", entry.mode.value)
    set_or_delete("x-task-id", entry.task_id)
    set_or_delete("x-category", entry.category.value)
    set_or_delete("description", entry.description)


def _get_calendar(facade: CalDAVFacade):
    return facade._get_calendar(facade.TIME_LOG_COLLECTION)


def create_time_log_entry(facade: CalDAVFacade, data: TimeLogEntryCreate) -> TimeLogEntry:
    """Create a new time log entry (typically a new focus session).

    ``completed_at`` is taken from ``data`` (defaulting to None for
    active sessions) so retrospective-log callers can submit a
    session that is already finished.
    """
    now = now_utc()
    entry = TimeLogEntry(
        uid=new_uid("l"),
        label=data.label,
        start=data.start,
        due=data.due,
        completed_at=data.completed_at,
        status=data.status,
        mode=data.mode,
        task_id=data.task_id,
        category=data.category,
        description=data.description,
        created=now,
        modified=now,
    )
    _get_calendar(facade).save_todo(time_log_to_ical(entry))
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
    status: TimeLogStatus | None = None,
    active_only: bool = False,
    sort_by: list[str] | None = None,
    sort_order: str = "asc",
) -> list[TimeLogEntry]:
    """List time log entries.

    Default: completed historical records (STATUS == COMPLETED), newest
    first. Use ``active_only=True`` to get in-progress focus sessions
    (``STATUS != COMPLETED``). ``status`` filters to an exact status
    value when set; ``active_only`` and ``status`` are mutually exclusive.

    Filter applied server-side:
        - task_id -> X-TASK-ID exact match

    Time log entries have no SUMMARY in the searchable sense for our
    use cases, so no ``search`` parameter.
    """
    if active_only and status is not None:
        raise ValueError("active_only and status are mutually exclusive")

    cal = _get_calendar(facade)
    searcher = cal.searcher(todo=True, include_completed=True)
    if task_id is not None:
        searcher.add_property_filter("X-TASK-ID", task_id, operator="==")
    if status is not None:
        searcher.add_property_filter("STATUS", status.value, operator="==")

    objects = searcher.search()

    entries: list[TimeLogEntry] = []
    for obj in objects:
        try:
            component = obj.icalendar_component
        except NotFoundError:
            continue
        if component.name != "VTODO":
            continue
        try:
            entry = time_log_from_ical(component)
        except Exception:
            continue
        if active_only:
            if entry.status == TimeLogStatus.COMPLETED:
                continue
        elif status is None:
            # Default semantic: completed historical records only.
            if entry.status != TimeLogStatus.COMPLETED:
                continue
        entries.append(entry)

    if sort_by:
        for key in reversed(sort_by):
            entries.sort(
                key=lambda e, k=key: getattr(e, k, "") or "",
                reverse=(sort_order == "desc"),
            )
    elif active_only:
        entries.sort(key=lambda e: (e.start, e.uid))
    else:
        entries.sort(key=lambda e: (e.start, e.uid), reverse=True)
    return entries


def update_time_log_entry(facade: CalDAVFacade, uid: str, data: TimeLogEntryUpdate) -> TimeLogEntry | None:
    """Update an existing time log entry in place. All fields optional.

    Prefer ``stop_time_log_entry`` for the stop flow because it
    guarantees a single PUT round-trip with If-Match for race
    protection; this is for ad-hoc edits.
    """
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
        setattr(entry, key, value)
    entry.modified = now_utc()

    _populate(obj.icalendar_component, entry)
    obj.save()
    return entry


def stop_time_log_entry(
    facade: CalDAVFacade,
    uid: str,
    *,
    actual_due: datetime | None,
    completed_at: datetime | None,
    category: TimeLogCategory | None = None,
) -> TimeLogEntry | None:
    """Mark a time log entry as COMPLETED with the canonical end times.

    Issues a single PUT. The caldav library sends If-Match with the
    object etag automatically (set by ``object_by_uid``), so a
    concurrent stop on another device 412s and raises
    ``ETagMismatchError``; this function returns None in that case.
    The losing device's caller should refetch.

    ``actual_due`` is the value written to DUE: ``start + duration`` for
    countdown auto-stop, ``now`` for manual stop. ``completed_at`` is
    written to COMPLETED (usually ``now``). ``category``, when given,
    overrides whatever the entry currently has (the focus page sends
    the resolved category the user accepted).

    Returns the updated entry, or None on 404 (already gone) or 412
    (concurrent stop won).
    """
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

    entry.due = to_utc_optional(actual_due)
    entry.completed_at = to_utc_optional(completed_at)
    entry.status = TimeLogStatus.COMPLETED
    if category is not None:
        entry.category = category
    entry.modified = now_utc()

    _populate(obj.icalendar_component, entry)
    try:
        obj.save()
    except caldav_error.ETagMismatchError:
        return None
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
