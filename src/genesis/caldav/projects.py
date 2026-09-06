"""Project CRUD operations for CalDAVFacade.

Projects are stored as VTODOs in the projects collection (see ADR-012).
Each project is a single ical object keyed by UID.

Functions take the facade as the first argument and use its
`_get_calendar` helper to reach the right collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..entities import Project, ProjectCreate, ProjectStatus, ProjectUpdate
from .ical import new_uid, now_utc, project_from_ical, project_to_ical

if TYPE_CHECKING:
    from icalendar import Component

    from .facade import CalDAVFacade


def _populate(component: Component, project: Project) -> None:
    """Apply project fields to an existing icalendar Component.

    DTSTAMP, CREATED, and LAST-MODIFIED are intentionally not managed here;
    the CalDAV server is responsible for setting them on PUT.

    UID, SUMMARY, STATUS, etc. are managed here as content. delete-then-add
    avoids duplicate properties when the component already contains them.
    """

    def set_or_delete(key: str, value: object) -> None:
        if key in component:
            del component[key]
        if value is not None:
            component.add(key, value)

    set_or_delete("uid", project.uid)
    set_or_delete("summary", project.name)
    set_or_delete("description", project.description)
    set_or_delete("dtstart", project.start_date)
    set_or_delete("due", project.deadline)
    set_or_delete("status", project.status.value)


def _get_calendar(facade: CalDAVFacade):
    return facade._get_calendar(facade.PROJECTS_COLLECTION)


def create_project(facade: CalDAVFacade, data: ProjectCreate) -> Project:
    """Create a new project."""
    now = now_utc()
    project = Project(
        uid=new_uid(),
        name=data.name,
        description=data.description,
        start_date=data.start_date,
        deadline=data.deadline,
        status=ProjectStatus.NEEDS_ACTION,
        created=now,
        modified=now,
    )
    _get_calendar(facade).add_todo(project_to_ical(project))
    return project


def get_project(facade: CalDAVFacade, uid: str) -> Project | None:
    """Get a project by UID, or None if not found."""
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None
    try:
        return project_from_ical(obj.icalendar_component)
    except Exception:
        return None


def list_projects(
    facade: CalDAVFacade,
    status: ProjectStatus | None = None,
    search: str | None = None,
    sort_by: list[str] | None = None,
    sort_order: str = "asc",
) -> list[Project]:
    """List all projects, optionally filtered and sorted."""
    projects: list[Project] = []
    cal = _get_calendar(facade)
    for obj in cal.objects():
        component = obj.icalendar_component
        if component.name != "VTODO":
            continue
        try:
            projects.append(project_from_ical(component))
        except Exception:
            continue

    if status is not None:
        projects = [p for p in projects if p.status == status]
    if search:
        needle = search.lower()
        projects = [
            p
            for p in projects
            if needle in p.name.lower()
            or (p.description and needle in p.description.lower())
        ]
    if sort_by:
        for key in reversed(sort_by):
            projects.sort(
                key=lambda p, k=key: getattr(p, k, "") or "",
                reverse=(sort_order == "desc"),
            )
    return projects


def update_project(
    facade: CalDAVFacade, uid: str, data: ProjectUpdate
) -> Project | None:
    """Update an existing project.

    Loads the project, mutates the icalendar component in place, and
    saves. Does NOT delete-then-recreate; the caldav library handles
    ETag negotiation on save.
    """
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return None
    if obj is None:
        return None

    try:
        project = project_from_ical(obj.icalendar_component)
    except Exception:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            setattr(project, key, value)
    project.modified = now_utc()

    _populate(obj.icalendar_component, project)
    obj.save()
    return project


def delete_project(facade: CalDAVFacade, uid: str) -> bool:
    """Delete a project.

    Clears project_id from any tasks that reference this project, then
    removes the project itself.
    """
    try:
        obj = _get_calendar(facade).object_by_uid(uid)
    except Exception:
        return False
    if obj is None:
        return False

    # Clear project_id on linked tasks before removing the project itself.
    from . import tasks as _tasks

    _tasks._clear_project_id_on_tasks(facade, uid)

    obj.delete()
    return True
