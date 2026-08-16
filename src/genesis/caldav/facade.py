"""CalDAV facade for tasks and projects.

Provides storage using Radicale via the caldav library.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import caldav
from caldav import Calendar, Event  # type: ignore

from ..entities import (
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)

if TYPE_CHECKING:
    from caldav.objects import Todo as CalDAVTodo  # type: ignore


class CalDAVFacade:
    """Facade for CalDAV storage of tasks and projects.

    Connects to a Radicale server and manages tasks and projects
    as separate calendars.
    """

    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
        task_calendar_name: str = "tasks",
        project_calendar_prefix: str = "project:",
    ) -> None:
        """Initialize the CalDAV facade.

        Args:
            server_url: Base URL of the DAV server (e.g., "http://127.0.0.1:5232").
            username: Username for authentication.
            password: Password for authentication.
            task_calendar_name: Name of the calendar for tasks.
            project_calendar_prefix: Prefix for project calendar names.
        """
        self._server_url = server_url
        self._username = username
        self._password = password
        self._task_calendar_name = task_calendar_name
        self._project_calendar_prefix = project_calendar_prefix

        # Lazily initialized
        self._client: caldav.DAVClient | None = None  # type: ignore[name-defined]
        self._principal: caldav.Principal | None = None  # type: ignore[name-defined]
        self._task_calendar: Calendar | None = None
        self._project_calendars: dict[str, Calendar] = {}

    def _get_client(self) -> caldav.DAVClient:  # type: ignore[name-defined]
        """Get or create the DAV client."""
        if self._client is None:
            self._client = caldav.DAVClient(  # type: ignore[assignment]
                self._server_url,
                username=self._username,
                password=self._password,
            )
        return self._client

    def _get_principal(self) -> caldav.Principal:  # type: ignore[name-defined]
        """Get or create the principal."""
        if self._principal is None:
            self._principal = self._get_client().principal()  # type: ignore[assignment]
        return self._principal

    def _get_task_calendar(self) -> Calendar:
        """Get or create the tasks calendar."""
        if self._task_calendar is None:
            self._task_calendar = self._get_or_create_calendar(self._task_calendar_name)
        return self._task_calendar

    def _get_project_calendar(self, project_uid: str) -> Calendar:
        """Get or create a calendar for a specific project."""
        if project_uid not in self._project_calendars:
            calendar_name = f"{self._project_calendar_prefix}{project_uid}"
            self._project_calendars[project_uid] = self._get_or_create_calendar(
                calendar_name
            )
        return self._project_calendars[project_uid]

    def _get_or_create_calendar(self, name: str) -> Calendar:
        """Get or create a calendar by name."""
        principal = self._get_principal()
        for cal in principal.calendars():
            if cal.get_display_name() == name:
                return cal
        # Create new calendar
        return principal.make_calendar(name)

    def _now(self) -> datetime:
        """Return current UTC datetime."""
        return datetime.now(UTC)

    # -------------------------------------------------------------------------
    # CalDAV object conversion
    # -------------------------------------------------------------------------

    def _icalendar_event_to_task(self, event: CalDAVTodo) -> Task:
        """Convert a CalDAV Todo object to a Task entity."""
        ical = event.icalendar_component

        def get_val(key: str) -> Any:
            """Get a value from the iCalendar component."""
            return ical.get(key)

        def extract_value(val: Any) -> Any:
            """Extract the Python value from an icalendar type."""
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

        def to_datetime(val: Any) -> datetime | None:
            val = extract_value(val)
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return None

        def to_date(val: Any) -> date | None:
            val = extract_value(val)
            if val is None:
                return None
            if isinstance(val, date) and not isinstance(val, datetime):
                return val
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, str):
                try:
                    return date.fromisoformat(val)
                except ValueError:
                    return None
            return None

        def to_str(val: Any) -> str | None:
            if val is None:
                return None
            return str(val)

        uid = to_str(get_val("uid")) or ""
        summary = to_str(get_val("summary")) or ""
        description = to_str(get_val("description"))
        hard_deadline = to_datetime(get_val("due"))
        assigned_date = to_date(get_val("x-assigned-date"))
        scheduled_start = to_datetime(get_val("dtstart"))
        status_str = to_str(get_val("status")) or TaskStatus.NEEDS_ACTION.value
        project_id = to_str(get_val("x-project-id"))
        completed_at = to_datetime(get_val("completed"))
        created = to_datetime(get_val("created"))
        modified = to_datetime(get_val("last-modified"))

        return Task(
            uid=uid,
            summary=summary,
            description=description,
            hard_deadline=hard_deadline,
            assigned_date=assigned_date,
            scheduled_start=scheduled_start,
            status=TaskStatus(status_str),
            project_id=project_id if project_id else None,
            completed_at=completed_at,
            created=created,
            modified=modified,
        )

    def _task_to_icalendar(self, task: Task) -> str:
        """Convert a Task entity to iCalendar format."""
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Genesis//Productivity//",
            "CALSCALE:GREGORIAN",
            "BEGIN:VTODO",
            f"UID:{task.uid}",
            f"SUMMARY:{self._escape_ical(task.summary)}",
        ]

        if task.description:
            lines.append(f"DESCRIPTION:{self._escape_ical(task.description)}")
        if task.hard_deadline:
            lines.append(f"DUE:{task.hard_deadline.strftime('%Y%m%dT%H%M%S')}")
        if task.assigned_date:
            lines.append(f"X-ASSIGNED-DATE:{task.assigned_date.isoformat()}")
        if task.scheduled_start:
            lines.append(f"DTSTART:{task.scheduled_start.strftime('%Y%m%dT%H%M%S')}")
        lines.append(f"STATUS:{task.status.value}")
        if task.project_id:
            lines.append(f"X-PROJECT-ID:{task.project_id}")
        if task.completed_at:
            lines.append(f"COMPLETED:{task.completed_at.strftime('%Y%m%dT%H%M%S')}")
        if task.created:
            lines.append(f"CREATED:{task.created.strftime('%Y%m%dT%H%M%S')}")
        if task.modified:
            lines.append(f"LAST-MODIFIED:{task.modified.strftime('%Y%m%dT%H%M%S')}")

        lines.extend(["END:VTODO", "END:VCALENDAR"])
        return "\r\n".join(lines)

    def _escape_ical(self, text: str) -> str:
        """Escape special characters for iCalendar."""
        return text.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,")

    def _icalendar_event_to_project(self, event: Event) -> Project:
        """Convert a CalDAV Event object to a Project entity."""
        ical = event.icalendar_component

        def get_val(key: str) -> Any:
            return ical.get(key)

        def extract_value(val: Any) -> Any:
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

        def to_datetime(val: Any) -> datetime | None:
            val = extract_value(val)
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return None

        def to_date(val: Any) -> date | None:
            val = extract_value(val)
            if val is None:
                return None
            if isinstance(val, date) and not isinstance(val, datetime):
                return val
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, str):
                try:
                    return date.fromisoformat(val)
                except ValueError:
                    return None
            return None

        def to_str(val: Any) -> str | None:
            if val is None:
                return None
            return str(val)

        uid = to_str(get_val("uid")) or ""
        name = to_str(get_val("summary")) or ""
        description = to_str(get_val("description"))
        start_date = to_date(get_val("dtstart"))
        deadline = to_date(get_val("due"))
        status_str = to_str(get_val("status")) or ProjectStatus.NEEDS_ACTION.value
        created = to_datetime(get_val("created"))
        modified = to_datetime(get_val("last-modified"))

        return Project(
            uid=uid,
            name=name,
            description=description,
            start_date=start_date,
            deadline=deadline,
            status=ProjectStatus(status_str),
            created=created,
            modified=modified,
        )

    def _project_to_icalendar(self, project: Project) -> str:
        """Convert a Project entity to iCalendar format."""
        import icalendar

        cal = icalendar.Calendar()
        cal.add("version", "2.0")
        cal.add("prodid", "-//Genesis//Productivity//")

        event = icalendar.Event()
        event.add("uid", project.uid)
        event.add("summary", project.name)
        event.add("dtstamp", project.modified or self._now())

        if project.description:
            event.add("description", project.description)
        # VEVENT requires DTSTART; use start_date or deadline or created or now
        if project.start_date:
            event.add("dtstart", project.start_date)
        elif project.deadline:
            event.add("dtstart", project.deadline)
        elif project.created:
            event.add("dtstart", project.created.date())
        else:
            event.add("dtstart", self._now().date())
        if project.deadline:
            event.add("due", project.deadline)
        event.add("status", project.status.value)
        if project.created:
            event.add("created", project.created)
        if project.modified:
            event.add("last-modified", project.modified)

        cal.add_component(event)
        return cal.to_ical().decode("utf-8")

    # -------------------------------------------------------------------------
    # Task operations
    # -------------------------------------------------------------------------

    def get_task(self, uid: str) -> Task | None:
        """Get a task by UID.

        Searches all calendars for a task with the given UID.
        """
        # Search in tasks calendar using object_by_uid
        try:
            task_cal = self._get_task_calendar()
            obj = task_cal.object_by_uid(uid)
            return self._icalendar_event_to_task(obj)
        except Exception:
            pass

        # Search in project calendars
        for project_cal in self._project_calendars.values():
            try:
                obj = project_cal.object_by_uid(uid)
                return self._icalendar_event_to_task(obj)
            except Exception:
                continue

        return None

    def list_tasks(
        self,
        project_id: str | None = None,
        status: TaskStatus | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        assigned_before: date | None = None,
        assigned_after: date | None = None,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "desc",
    ) -> list[Task]:
        """List tasks with optional filters."""
        tasks: list[Task] = []

        # Collect tasks from appropriate calendars
        # Note: Using objects() instead of todos() due to caldav library bug
        if project_id is not None:
            # Get tasks from specific project
            try:
                project_cal = self._get_project_calendar(project_id)
                for obj in project_cal.objects():
                    if "VTODO" in obj.icalendar_component.name:
                        tasks.append(self._icalendar_event_to_task(obj))
            except Exception:
                pass
        else:
            # Get tasks from tasks calendar
            try:
                task_cal = self._get_task_calendar()
                for obj in task_cal.objects():
                    if "VTODO" in obj.icalendar_component.name:
                        tasks.append(self._icalendar_event_to_task(obj))
            except Exception:
                pass

        # Apply filters
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if due_before is not None:
            tasks = [t for t in tasks if t.hard_deadline and t.hard_deadline <= due_before]
        if due_after is not None:
            tasks = [t for t in tasks if t.hard_deadline and t.hard_deadline >= due_after]
        if assigned_before is not None:
            tasks = [t for t in tasks if t.assigned_date and t.assigned_date <= assigned_before]
        if assigned_after is not None:
            tasks = [t for t in tasks if t.assigned_date and t.assigned_date >= assigned_after]
        if search:
            search_lower = search.lower()
            tasks = [
                t
                for t in tasks
                if search_lower in t.summary.lower()
                or (t.description and search_lower in t.description.lower())
            ]

        # Apply sorting
        if sort_by is None:
            sort_by = ["created"]

        def get_sort_value(task: Task, field: str) -> tuple:
            if field == "created":
                return (task.created or datetime.min.replace(tzinfo=UTC),)
            elif field == "modified":
                return (task.modified or datetime.min.replace(tzinfo=UTC),)
            elif field == "hard_deadline":
                return (task.hard_deadline or datetime.max.replace(tzinfo=UTC),)
            elif field == "assigned_date":
                return (task.assigned_date or date.max,)
            elif field == "summary":
                return (task.summary.lower(),)
            else:
                return (task.created or datetime.min.replace(tzinfo=UTC),)

        reverse = sort_order == "desc"
        for field in reversed(sort_by):
            tasks = sorted(tasks, key=lambda t: get_sort_value(t, field), reverse=reverse)

        return tasks

    def create_task(self, data: TaskCreate) -> Task:
        """Create a new task."""
        now = self._now()
        uid = f"{uuid.uuid4()}@genesis"

        task = Task(
            uid=uid,
            summary=data.summary,
            description=data.description,
            hard_deadline=data.hard_deadline,
            assigned_date=data.assigned_date,
            scheduled_start=data.scheduled_start,
            status=TaskStatus.NEEDS_ACTION,
            project_id=data.project_id,
            created=now,
            modified=now,
        )

        # Choose calendar based on project_id
        if data.project_id:
            calendar = self._get_project_calendar(data.project_id)
        else:
            calendar = self._get_task_calendar()

        calendar.add_todo(self._task_to_icalendar(task))
        return task

    def update_task(self, uid: str, data: TaskUpdate) -> Task | None:
        """Update an existing task."""
        existing = self.get_task(uid)
        if not existing:
            return None

        now = self._now()
        update_dict = data.model_dump(exclude_unset=True, exclude_none=True)
        update_dict.pop("clear_project_id", None)

        if data.clear_project_id:
            update_dict["project_id"] = None
        if data.status == TaskStatus.COMPLETED and existing.status != TaskStatus.COMPLETED:
            update_dict["completed_at"] = now
        elif data.status and data.status != TaskStatus.COMPLETED:
            update_dict["completed_at"] = None

        updated = existing.model_copy(update=update_dict)
        updated.modified = now

        # Find and delete the old task, then create a new one
        # This is simpler than trying to update in place
        task_cal = self._get_task_calendar()
        for obj in task_cal.objects():
            if "VTODO" in obj.icalendar_component.name:
                if obj.icalendar_component.get("uid") == uid:
                    # Delete old task
                    obj.delete()
                    break

        # Create new task with same UID
        task_cal.add_todo(self._task_to_icalendar(updated))
        return updated

    def delete_task(self, uid: str) -> bool:
        """Delete a task."""
        task_cal = self._get_task_calendar()
        for obj in task_cal.objects():
            if "VTODO" in obj.icalendar_component.name:
                if obj.icalendar_component.get("uid") == uid:
                    obj.delete()
                    return True
        return False

    # -------------------------------------------------------------------------
    # Project operations
    # -------------------------------------------------------------------------

    def get_project(self, uid: str) -> Project | None:
        """Get a project by UID.

        Projects are stored in a dedicated calendar.
        """
        principal = self._get_principal()
        calendar_name = f"{self._project_calendar_prefix}{uid}"

        for cal in principal.calendars():
            if cal.get_display_name() == calendar_name:
                for event in cal.events():
                    return self._icalendar_event_to_project(event)
        return None

    def list_projects(
        self,
        status: ProjectStatus | None = None,
        deadline_before: date | None = None,
        deadline_after: date | None = None,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "desc",
    ) -> list[Project]:
        """List projects with optional filters."""
        projects: list[Project] = []
        principal = self._get_principal()

        # Find all project calendars
        for cal in principal.calendars():
            if cal.get_display_name().startswith(self._project_calendar_prefix):
                for event in cal.events():
                    projects.append(self._icalendar_event_to_project(event))

        # Apply filters
        if status is not None:
            projects = [p for p in projects if p.status == status]
        if deadline_before is not None:
            projects = [p for p in projects if p.deadline and p.deadline <= deadline_before]
        if deadline_after is not None:
            projects = [p for p in projects if p.deadline and p.deadline >= deadline_after]
        if search:
            search_lower = search.lower()
            projects = [
                p
                for p in projects
                if search_lower in p.name.lower()
                or (p.description and search_lower in p.description.lower())
            ]

        # Apply sorting
        if sort_by is None:
            sort_by = ["created"]

        def get_sort_value(project: Project, field: str) -> tuple:
            if field == "created":
                return (project.created or datetime.min.replace(tzinfo=UTC),)
            elif field == "modified":
                return (project.modified or datetime.min.replace(tzinfo=UTC),)
            elif field == "deadline":
                return (project.deadline or date.max,)
            elif field == "name":
                return (project.name.lower(),)
            else:
                return (project.created or datetime.min.replace(tzinfo=UTC),)

        reverse = sort_order == "desc"
        for field in reversed(sort_by):
            projects = sorted(
                projects, key=lambda p: get_sort_value(p, field), reverse=reverse
            )

        return projects

    def create_project(self, data: ProjectCreate) -> Project:
        """Create a new project."""
        now = self._now()
        uid = f"{uuid.uuid4()}@genesis"

        project = Project(
            uid=uid,
            name=data.name,
            description=data.description,
            start_date=data.start_date,
            deadline=data.deadline,
            status=ProjectStatus.NEEDS_ACTION,
            created=now,
            modified=now,
        )

        calendar_name = f"{self._project_calendar_prefix}{uid}"
        calendar = self._get_or_create_calendar(calendar_name)
        calendar.save_event(self._project_to_icalendar(project))

        return project

    def update_project(self, uid: str, data: ProjectUpdate) -> Project | None:
        """Update an existing project."""
        existing = self.get_project(uid)
        if not existing:
            return None

        now = self._now()
        update_dict = data.model_dump(exclude_unset=True, exclude_none=True)

        updated = existing.model_copy(update=update_dict)
        updated.modified = now

        # Find and delete the old project, then create a new one
        calendar_name = f"{self._project_calendar_prefix}{uid}"
        principal = self._get_principal()

        for cal in principal.calendars():
            if cal.get_display_name() == calendar_name:
                for event in cal.events():
                    # Delete old event
                    event.delete()
                    # Create new event
                    cal.save_event(self._project_to_icalendar(updated))
                    return updated

        return None

    def delete_project(self, uid: str) -> bool:
        """Delete a project and remove project_id from linked tasks."""
        calendar_name = f"{self._project_calendar_prefix}{uid}"
        principal = self._get_principal()

        # Find the project calendar
        project_cal = None
        for cal in principal.calendars():
            if cal.get_display_name() == calendar_name:
                project_cal = cal
                break

        if project_cal is None:
            return False

        # Move tasks from project calendar to tasks calendar and clear project_id
        task_cal = self._get_task_calendar()
        for obj in list(project_cal.objects()):
            if "VTODO" in obj.icalendar_component.name:
                task = self._icalendar_event_to_task(obj)
                if task.project_id == uid:
                    # Clear project_id and move to tasks calendar
                    task.project_id = None
                    task.modified = self._now()
                    # Delete from project calendar
                    obj.delete()
                    # Add to tasks calendar
                    task_cal.add_todo(self._task_to_icalendar(task))

        # Now delete the project calendar
        project_cal.delete()
        return True
