"""Event CRUD operations for CalDAVFacade.

Events are stored as VEVENTs in the events collection. The collection
holds both standalone appointments and shadow events that mirror
time-blocked tasks. Shadow events have task_id set; callers manage them
through update_task, not via direct event writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from caldav.lib.error import NotFoundError

from ..entities import Event, EventCreate, EventStatus, EventUpdate
from .ical import event_from_ical, event_to_ical, new_uid, now_utc

if TYPE_CHECKING:
    from icalendar import Component

    from .facade import CalDAVFacade


def _populate(component: Component, event: Event) -> None:
    """Apply event fields to an existing icalendar Component in place.

    DTSTAMP, CREATED, and LAST-MODIFIED are intentionally not managed here;
    the CalDAV server is responsible for setting them on PUT.
    """

    def set_or_delete(key: str, value: object) -> None:
        if key in component:
            del component[key]
        if value is not None:
            component.add(key, value)

    set_or_delete("uid", event.uid)
    set_or_delete("summary", event.summary)
    set_or_delete("dtstart", event.start)
    set_or_delete("dtend", event.end)
    set_or_delete("status", event.status.value)
    set_or_delete("location", event.location)
    set_or_delete("description", event.description)


def _get_calendar(facade: CalDAVFacade):
    return facade._get_calendar(facade.EVENTS_COLLECTION)


def create_event(facade: CalDAVFacade, data: EventCreate) -> Event:
    """Create a new standalone event (not a shadow)."""
    now = now_utc()
    event = Event(
        uid=new_uid(),
        summary=data.summary,
        start=data.start,
        end=data.end,
        location=data.location,
        description=data.description,
        status=data.status,
        created=now,
        modified=now,
    )
    _get_calendar(facade).save_event(event_to_ical(event))
    return event


def get_event(facade: CalDAVFacade, uid: str) -> Event | None:
    """Get an event by UID, or None if not found."""
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None
    try:
        return event_from_ical(obj.icalendar_component)
    except Exception:
        return None


def list_events(
    facade: CalDAVFacade,
    task_id: str | None = None,
    status: EventStatus | None = None,
    search: str | None = None,
    sort_by: list[str] | None = None,
    sort_order: str = "asc",
) -> list[Event]:
    """List events with optional filters."""
    events: list[Event] = []
    for obj in _get_calendar(facade).objects():
        try:
            component = obj.icalendar_component
        except NotFoundError:
            # The server's object index has a stale entry that no longer
            # resolves to a real resource. Skip it instead of failing
            # the entire list.
            continue
        if component.name != "VEVENT":
            continue
        try:
            events.append(event_from_ical(component))
        except Exception:
            continue

    if task_id is not None:
        events = [e for e in events if e.task_id == task_id]
    if status is not None:
        events = [e for e in events if e.status == status]
    if search:
        needle = search.lower()
        events = [
            e
            for e in events
            if needle in e.summary.lower()
            or (e.description and needle in e.description.lower())
        ]
    if sort_by:
        for key in reversed(sort_by):
            events.sort(
                key=lambda e, k=key: getattr(e, k, "") or "",
                reverse=(sort_order == "desc"),
            )
    return events


def update_event(
    facade: CalDAVFacade, uid: str, data: EventUpdate
) -> Event | None:
    """Update an existing event.

    Mutates the icalendar component in place and saves. Refuses to
    modify events that have task_id set (those are shadow events;
    manage them via update_task on the source task).
    """
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None

    try:
        existing = event_from_ical(obj.icalendar_component)
    except Exception:
        return None

    if existing.task_id is not None:
        raise ValueError(
            f"Event {uid} is a shadow event owned by task {existing.task_id}; "
            "update via update_task instead"
        )

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            setattr(existing, key, value)
    existing.modified = now_utc()

    _populate(obj.icalendar_component, existing)
    obj.save()
    return existing


def delete_event(facade: CalDAVFacade, uid: str) -> bool:
    """Delete an event.

    Refuses to delete shadow events; clear scheduled_start on the source
    task to remove its shadow.
    """
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return False
    if obj is None:
        return False

    component = obj.icalendar_component
    if component.get("x-task-id") is not None:
        raise ValueError(
            f"Event {uid} is a shadow event; clear scheduled_start on "
            "its source task to remove it"
        )

    obj.delete()
    return True
