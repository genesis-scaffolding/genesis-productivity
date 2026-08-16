"""CalDAV facade for tasks and projects.

Provides storage using .ics files on disk.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import icalendar
from icalendar import Calendar, Todo

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


class CalDAVFacade:
    """Facade for CalDAV storage of tasks and projects.

    Uses .ics files on disk for persistence.
    """

    def __init__(self, data_path: Path) -> None:
        """Initialize the CalDAV facade.

        Args:
            data_path: Root data directory for CalDAV storage.
                      Creates tasks/ and projects/ subdirectories if needed.
        """
        self._data_path = Path(data_path)
        self._tasks_path = self._data_path / "tasks"
        self._projects_path = self._data_path / "projects"
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create storage directories if they don't exist."""
        self._tasks_path.mkdir(parents=True, exist_ok=True)
        self._projects_path.mkdir(parents=True, exist_ok=True)

    def _now(self) -> datetime:
        """Return current UTC datetime."""
        return datetime.now(UTC)

    # -------------------------------------------------------------------------
    # Task operations
    # -------------------------------------------------------------------------

    def _task_file(self, uid: str) -> Path:
        """Return the file path for a task."""
        return self._tasks_path / f"{uid}.ics"

    def _serialize_task(self, task: Task) -> bytes:
        """Serialize a Task to .ics format."""
        todo = Todo()
        todo.add("uid", task.uid)
        todo.add("summary", task.summary)
        if task.description:
            todo.add("description", task.description)
        if task.hard_deadline:
            todo.add("due", task.hard_deadline)
        if task.assigned_date:
            todo.add("x-assigned-date", icalendar.vText(task.assigned_date.isoformat()))
        if task.scheduled_start:
            todo.add("dtstart", task.scheduled_start)
        todo.add("status", task.status.value)
        if task.project_id:
            todo.add("x-project-id", icalendar.vText(task.project_id))
        if task.completed_at:
            todo.add("completed", task.completed_at)
        if task.created:
            todo.add("created", task.created)
        if task.modified:
            todo.add("last-modified", task.modified)

        cal = Calendar()
        cal.add("version", "2.0")
        cal.add("prodid", "-//Genesis//Productivity//")
        cal.add_component(todo)
        return cal.to_ical()

    def _deserialize_task(self, data: bytes) -> Task:
        """Deserialize a Task from .ics format."""
        cal = Calendar.from_ical(data)
        todo = cal.walk("VTODO")[0]

        def extract_value(val: Any) -> Any:
            """Extract the Python value from an icalendar type."""
            if val is None:
                return None
            # Handle icalendar vDDDTypes and similar types
            try:
                return val.dt
            except AttributeError:
                pass
            # Handle vUnknown and vText (custom X-properties)
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

        uid = to_str(todo.get("uid")) or ""
        summary = to_str(todo.get("summary")) or ""
        description = to_str(todo.get("description"))
        hard_deadline = to_datetime(todo.get("due"))
        assigned_date = to_date(todo.get("x-assigned-date"))
        scheduled_start = to_datetime(todo.get("dtstart"))
        status_str = to_str(todo.get("status")) or TaskStatus.NEEDS_ACTION.value
        project_id = to_str(todo.get("x-project-id"))
        completed_at = to_datetime(todo.get("completed"))
        created = to_datetime(todo.get("created"))
        modified = to_datetime(todo.get("last-modified"))

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

    def get_task(self, uid: str) -> Task | None:
        """Get a task by UID."""
        path = self._task_file(uid)
        if not path.exists():
            return None
        return self._deserialize_task(path.read_bytes())

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
        for path in self._tasks_path.glob("*.ics"):
            try:
                task = self._deserialize_task(path.read_bytes())
                tasks.append(task)
            except Exception:
                continue

        if project_id is not None:
            tasks = [t for t in tasks if t.project_id == project_id]
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

        path = self._task_file(uid)
        path.write_bytes(self._serialize_task(task))
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

        path = self._task_file(uid)
        path.write_bytes(self._serialize_task(updated))
        return updated

    def delete_task(self, uid: str) -> bool:
        """Delete a task."""
        path = self._task_file(uid)
        if not path.exists():
            return False
        path.unlink()
        return True

    # -------------------------------------------------------------------------
    # Project operations
    # -------------------------------------------------------------------------

    def _project_file(self, uid: str) -> Path:
        """Return the file path for a project."""
        return self._projects_path / f"{uid}.ics"

    def _serialize_project(self, project: Project) -> bytes:
        """Serialize a Project to .ics format."""
        todo = Todo()
        todo.add("uid", project.uid)
        todo.add("summary", project.name)
        if project.description:
            todo.add("description", project.description)
        if project.start_date:
            todo.add("dtstart", project.start_date)
        if project.deadline:
            deadline_dt = datetime.combine(
                project.deadline, datetime.min.time().replace(tzinfo=UTC)
            )
            todo.add("due", deadline_dt)
        todo.add("status", project.status.value)
        if project.created:
            todo.add("created", project.created)
        if project.modified:
            todo.add("last-modified", project.modified)

        cal = Calendar()
        cal.add("version", "2.0")
        cal.add("prodid", "-//Genesis//Productivity//")
        cal.add_component(todo)
        return cal.to_ical()

    def _deserialize_project(self, data: bytes) -> Project:
        """Deserialize a Project from .ics format."""
        cal = Calendar.from_ical(data)
        todo = cal.walk("VTODO")[0]

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

        uid = to_str(todo.get("uid")) or ""
        name = to_str(todo.get("summary")) or ""
        description = to_str(todo.get("description"))
        start_date = to_date(todo.get("dtstart"))
        deadline = to_date(todo.get("due"))
        status_str = to_str(todo.get("status")) or ProjectStatus.NEEDS_ACTION.value
        created = to_datetime(todo.get("created"))
        modified = to_datetime(todo.get("last-modified"))

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

    def get_project(self, uid: str) -> Project | None:
        """Get a project by UID."""
        path = self._project_file(uid)
        if not path.exists():
            return None
        return self._deserialize_project(path.read_bytes())

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
        for path in self._projects_path.glob("*.ics"):
            try:
                project = self._deserialize_project(path.read_bytes())
                projects.append(project)
            except Exception:
                continue

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
            projects = sorted(projects, key=lambda p: get_sort_value(p, field), reverse=reverse)

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

        path = self._project_file(uid)
        path.write_bytes(self._serialize_project(project))
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

        path = self._project_file(uid)
        path.write_bytes(self._serialize_project(updated))
        return updated

    def delete_project(self, uid: str) -> bool:
        """Delete a project. Removes project_id from linked tasks."""
        if not self._project_file(uid).exists():
            return False

        for path in self._tasks_path.glob("*.ics"):
            try:
                task = self._deserialize_task(path.read_bytes())
                if task.project_id == uid:
                    task.project_id = None
                    task.modified = self._now()
                    path.write_bytes(self._serialize_task(task))
            except Exception:
                continue

        self._project_file(uid).unlink()
        return True
