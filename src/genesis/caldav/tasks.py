"""Task CRUD operations for CalDAVFacade with shadow VEVENT maintenance.

Tasks are stored as VTODOs in the tasks collection. When a task has
scheduled_start set, a linked VEVENT is maintained in the events
collection (same UID, DTSTART from scheduled_start, DTEND computed from
scheduled_duration with a 30-minute default). The shadow is what makes
time-blocked tasks render as appointments in off-the-shelf CalDAV clients.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from caldav.lib.error import NotFoundError

from ..entities import (
    Event,
    EventStatus,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)
from .constants import DEFAULT_SHADOW_DURATION_MINUTES
from .ical import (
    event_to_ical,
    new_uid,
    now_utc,
    task_from_ical,
    task_to_ical,
)

if TYPE_CHECKING:
    from icalendar import Component

    from .facade import CalDAVFacade


# -----------------------------------------------------------------------------
# icalendar populate helpers
# -----------------------------------------------------------------------------


def _populate_task(component: Component, task: Task) -> None:
    """Apply task fields to an existing icalendar Component in place.

    DTSTAMP, CREATED, and LAST-MODIFIED are intentionally not managed here;
    the CalDAV server is responsible for setting them on PUT.
    """

    def set_or_delete(key: str, value: object) -> None:
        if key in component:
            del component[key]
        if value is not None:
            component.add(key, value)

    set_or_delete("uid", task.uid)
    set_or_delete("summary", task.summary)
    set_or_delete("status", task.status.value)
    set_or_delete("description", task.description)
    set_or_delete("due", task.hard_deadline)
    set_or_delete("x-assigned-date", task.assigned_date)
    set_or_delete("dtstart", task.scheduled_start)
    set_or_delete("x-scheduled-duration", task.scheduled_duration)
    set_or_delete("priority", task.priority)
    set_or_delete("x-project-id", task.project_id)
    if task.status == TaskStatus.COMPLETED:
        set_or_delete("completed", task.completed_at or now_utc())
    else:
        set_or_delete("completed", None)


def _populate_shadow(component: Component, shadow: Event) -> None:
    """Apply shadow event fields to an existing icalendar Component in place.

    DTSTAMP, CREATED, and LAST-MODIFIED are intentionally not managed here.
    """

    def set_or_delete(key: str, value: object) -> None:
        if key in component:
            del component[key]
        if value is not None:
            component.add(key, value)

    set_or_delete("uid", shadow.uid)
    set_or_delete("summary", shadow.summary)
    set_or_delete("dtstart", shadow.start)
    set_or_delete("dtend", shadow.end)
    set_or_delete("status", shadow.status.value)
    set_or_delete("location", shadow.location)
    set_or_delete("description", shadow.description)
    set_or_delete("x-task-id", shadow.task_id)


# -----------------------------------------------------------------------------
# Calendar accessors
# -----------------------------------------------------------------------------


def _get_tasks_calendar(facade: CalDAVFacade):
    return facade._get_calendar(facade.TASKS_COLLECTION)


def _get_events_calendar(facade: CalDAVFacade):
    return facade._get_calendar(facade.EVENTS_COLLECTION)


def _try_load_event(facade: CalDAVFacade, uid: str):
    """Return the event object for uid in the events calendar, or None."""
    try:
        return _get_events_calendar(facade).object_by_uid(uid)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Public CRUD
# -----------------------------------------------------------------------------


def create_task(facade: CalDAVFacade, data: TaskCreate) -> Task:
    """Create a new task. If scheduled_start is set, the matching shadow
    VEVENT is also created in the events collection."""
    now = now_utc()
    task = Task(
        uid=new_uid(),
        summary=data.summary,
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
    )

    _get_tasks_calendar(facade).add_todo(task_to_ical(task))
    _reconcile_shadow(facade, task)
    return task


def get_task(facade: CalDAVFacade, uid: str) -> Task | None:
    """Get a task by UID, or None if not found."""
    try:
        obj = _get_tasks_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None
    try:
        return task_from_ical(obj.icalendar_component)
    except Exception:
        return None


def list_tasks(
    facade: CalDAVFacade,
    project_id: str | None = None,
    status: TaskStatus | None = None,
    search: str | None = None,
    sort_by: list[str] | None = None,
    sort_order: str = "asc",
) -> list[Task]:
    """List tasks with optional filters."""
    tasks: list[Task] = []
    for obj in _get_tasks_calendar(facade).objects():
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

    if project_id is not None:
        tasks = [t for t in tasks if t.project_id == project_id]
    if status is not None:
        tasks = [t for t in tasks if t.status == status]
    if search:
        needle = search.lower()
        tasks = [
            t
            for t in tasks
            if needle in t.summary.lower()
            or (t.description and needle in t.description.lower())
        ]
    if sort_by:
        for key in reversed(sort_by):
            tasks.sort(
                key=lambda t, k=key: getattr(t, k, "") or "",
                reverse=(sort_order == "desc"),
            )
    else:
        # Default: stable order by (created, uid) so callers get a
        # deterministic result that does not shift across renders when
        # the underlying collection is updated. Radicale's PROPFIND
        # returns items in a non-deterministic order that depends on
        # file write order, which causes data_editor row indices to
        # drift between renders.
        tasks.sort(key=lambda t: (t.created, t.uid))
    return tasks


def update_task(
    facade: CalDAVFacade, uid: str, data: TaskUpdate
) -> Task | None:
    """Update an existing task.

    Loads the task, mutates the icalendar component in place, and saves.
    Does NOT delete-then-recreate. The shadow VEVENT is reconciled after
    the task write so the calendar state matches the task state.
    """
    try:
        obj = _get_tasks_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None

    try:
        task = task_from_ical(obj.icalendar_component)
    except Exception:
        return None

    update_data = data.model_dump(exclude_unset=True)
    clear_scheduled = update_data.pop("clear_scheduled_start", False)
    clear_project = update_data.pop("clear_project_id", False)

    for key, value in update_data.items():
        setattr(task, key, value)

    if clear_scheduled:
        task.scheduled_start = None
    if clear_project:
        task.project_id = None

    if (
        update_data.get("status") == TaskStatus.COMPLETED
        and task.completed_at is None
    ):
        task.completed_at = now_utc()
    elif (
        "status" in update_data
        and task.status != TaskStatus.COMPLETED
        and task.completed_at is not None
    ):
        task.completed_at = None

    task.modified = now_utc()

    _populate_task(obj.icalendar_component, task)
    obj.save()
    _reconcile_shadow(facade, task)
    return task


def delete_task(facade: CalDAVFacade, uid: str) -> bool:
    """Delete a task and its shadow VEVENT."""
    try:
        obj = _get_tasks_calendar(facade).object_by_uid(uid)
    except Exception:
        return False
    if obj is None:
        return False

    shadow = _try_load_event(facade, uid)
    if shadow is not None:
        try:
            shadow.delete()
        except Exception:
            pass

    obj.delete()
    return True


# -----------------------------------------------------------------------------
# Shadow maintenance
# -----------------------------------------------------------------------------


def _reconcile_shadow(facade: CalDAVFacade, task: Task) -> None:
    """Create, update, or delete the shadow VEVENT to match the task."""
    if task.scheduled_start is None:
        shadow = _try_load_event(facade, task.uid)
        if shadow is not None:
            shadow.delete()
        return

    duration = (
        task.scheduled_duration
        if task.scheduled_duration is not None
        else DEFAULT_SHADOW_DURATION_MINUTES
    )
    shadow_event = Event(
        uid=task.uid,
        summary=task.summary,
        start=task.scheduled_start,
        end=task.scheduled_start + timedelta(minutes=duration),
        status=EventStatus.CONFIRMED,
        task_id=task.uid,
        modified=task.modified,
    )

    existing = _try_load_event(facade, task.uid)
    if existing is not None:
        _populate_shadow(existing.icalendar_component, shadow_event)
        existing.save()
    else:
        _get_events_calendar(facade).save_event(event_to_ical(shadow_event))


# -----------------------------------------------------------------------------
# Cross-module helpers
# -----------------------------------------------------------------------------


def _clear_project_id_on_tasks(facade: CalDAVFacade, project_uid: str) -> None:
    """Clear project_id on every task that references project_uid.

    Called by projects.delete_project when a project is removed.
    """
    for task in list_tasks(facade, project_id=project_uid):
        task.project_id = None
        task.modified = now_utc()
        try:
            obj = _get_tasks_calendar(facade).object_by_uid(task.uid)
        except Exception:
            continue
        if obj is None:
            continue
        _populate_task(obj.icalendar_component, task)
        try:
            obj.save()
        except Exception:
            continue
