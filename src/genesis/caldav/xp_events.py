"""XP event CRUD operations for CalDAVFacade.

An XP event is a VEVENT stored in the xp_events collection. They
are immutable facts about XP gains and losses. See ADR-026.

Reads support server-side filters by ``X-STAT-ID``, ``X-KIND``,
``X-SOURCE-TASK-ID``, ``X-SOURCE-SESSION-ID``, and
``X-IDEMPOTENCY-KEY`` (all exact match). Range filters on
``DTSTART`` (``occurred_at``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from caldav.lib import error as caldav_error

from ..entities import XpEvent, XpEventCreate, XpEventKind
from .ical import new_uid, now_utc, xp_event_from_ical, xp_event_to_ical

if TYPE_CHECKING:
    from .facade import CalDAVFacade


def _get_calendar(facade: CalDAVFacade):
    return facade._get_calendar(facade.XP_EVENTS_COLLECTION)


def create_xp_event(facade: CalDAVFacade, data: XpEventCreate) -> XpEvent:
    """Create a new XP event.

    Idempotency: the caller is expected to pass a unique
    ``idempotency_key``. The game facade checks for duplicates
    before calling this; this function does not enforce uniqueness
    (Radicale's search is the cheapest dedup mechanism for the
    read path).
    """
    now = now_utc()
    event = XpEvent(
        uid=new_uid("x"),
        stat_id=data.stat_id,
        delta=data.delta,
        kind=data.kind,
        source_task_id=data.source_task_id,
        source_session_id=data.source_session_id,
        reason=data.reason,
        occurred_at=data.occurred_at,
        idempotency_key=data.idempotency_key,
        created=now,
        modified=now,
    )
    _get_calendar(facade).save_event(xp_event_to_ical(event))
    return event


def get_xp_event(facade: CalDAVFacade, uid: str) -> XpEvent | None:
    """Get an XP event by UID, or None if not found."""
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None
    try:
        return xp_event_from_ical(obj.icalendar_component)
    except Exception:
        return None


def list_xp_events(
    facade: CalDAVFacade,
    *,
    stat_id: str | None = None,
    kind: XpEventKind | None = None,
    source_task_id: str | None = None,
    source_session_id: str | None = None,
    idempotency_key: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sort_by: list[str] | None = None,
    sort_order: str = "desc",
) -> list[XpEvent]:
    """List XP events with optional server-side filters.

    ``since`` / ``until`` filter on ``DTSTART`` (the event's
    ``occurred_at``). All other filters are exact-match on the
    custom X- properties. Default sort: newest first.
    """
    cal = _get_calendar(facade)
    searcher = cal.searcher(event=True, start=since, end=until)
    if stat_id is not None:
        searcher.add_property_filter("X-STAT-ID", stat_id, operator="==")
    if kind is not None:
        searcher.add_property_filter("X-KIND", kind.value, operator="==")
    if source_task_id is not None:
        searcher.add_property_filter("X-SOURCE-TASK-ID", source_task_id, operator="==")
    if source_session_id is not None:
        searcher.add_property_filter("X-SOURCE-SESSION-ID", source_session_id, operator="==")
    if idempotency_key is not None:
        searcher.add_property_filter("X-IDEMPOTENCY-KEY", idempotency_key, operator="==")

    try:
        objects = searcher.search()
    except caldav_error.NotFoundError:
        # The collection doesn't exist on the server yet. This
        # happens on a fresh user account that hasn't had any XP
        # events persisted yet — the lazy MKCALENDAR in
        # ``_get_calendar`` may not actually MKCALENDAR until a
        # PUT hits the collection URL. Treat as an empty result
        # so the character page renders zero-state instead of
        # crashing.
        return []

    events: list[XpEvent] = []
    for obj in objects:
        try:
            component = obj.icalendar_component
        except Exception:
            continue
        if component.name != "VEVENT":
            continue
        try:
            event = xp_event_from_ical(component)
        except Exception:
            continue
        events.append(event)

    if sort_by:
        for key in reversed(sort_by):
            events.sort(
                key=lambda e, k=key: getattr(e, k, "") or "",
                reverse=(sort_order == "desc"),
            )
    else:
        events.sort(key=lambda e: (e.occurred_at, e.uid), reverse=True)
    return events


def delete_xp_event(facade: CalDAVFacade, uid: str) -> bool:
    """Delete an XP event by UID."""
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return False
    if obj is None:
        return False
    obj.delete()
    return True
