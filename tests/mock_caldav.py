"""Mock CalDAV backend for testing.

Stores data in memory and implements the same interface as CalDAVFacade.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from genesis.entities import (
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)


class MockCalDAVFacade:
    """Mock CalDAV facade that stores data in memory."""

    def __init__(
        self,
        *,
        task_calendar_name: str = "tasks",
        project_calendar_prefix: str = "project:",
    ) -> None:
        self._task_calendar_name = task_calendar_name
        self._project_calendar_prefix = project_calendar_prefix
        self._tasks: dict[str, Task] = {}
        self._projects: dict[str, Project] = {}
        self._project_tasks: dict[str, set[str]] = {}  # project_uid -> set of task_uids

    def _now(self) -> datetime:
        return datetime.now(UTC)

    # -------------------------------------------------------------------------
    # Task operations
    # -------------------------------------------------------------------------

    def create_task(self, data: TaskCreate) -> Task:
        """Create a new task."""
        now = self._now()
        uid = f"{uuid4()}@genesis"

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

        self._tasks[uid] = task

        if data.project_id:
            if data.project_id not in self._project_tasks:
                self._project_tasks[data.project_id] = set()
            self._project_tasks[data.project_id].add(uid)

        return task

    def get_task(self, uid: str) -> Task | None:
        """Get a task by UID."""
        return self._tasks.get(uid)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        project_id: str | None = None,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ) -> list[Task]:
        """List tasks with optional filtering."""
        tasks = list(self._tasks.values())

        if status:
            tasks = [t for t in tasks if t.status == status]
        if project_id:
            tasks = [t for t in tasks if t.project_id == project_id]
        if search:
            search_lower = search.lower()
            tasks = [
                t
                for t in tasks
                if search_lower in t.summary.lower()
                or (t.description and search_lower in t.description.lower())
            ]

        if sort_by:
            for key in reversed(sort_by):
                tasks.sort(key=lambda t: getattr(t, key, ""), reverse=(sort_order == "desc"))

        return tasks

    def update_task(self, uid: str, data: TaskUpdate) -> Task | None:
        """Update a task."""
        task = self._tasks.get(uid)
        if not task:
            return None

        now = self._now()
        update_data = data.model_dump(exclude_unset=True)

        for key, value in update_data.items():
            if value is not None:
                setattr(task, key, value)

        task.modified = now

        if data.status == TaskStatus.COMPLETED and task.completed_at is None:
            task.completed_at = now
        elif data.status != TaskStatus.COMPLETED and task.completed_at is not None:
            task.completed_at = None

        return task

    def delete_task(self, uid: str) -> bool:
        """Delete a task."""
        if uid in self._tasks:
            task = self._tasks[uid]
            if task.project_id and task.project_id in self._project_tasks:
                self._project_tasks[task.project_id].discard(uid)
            del self._tasks[uid]
            return True
        return False

    # -------------------------------------------------------------------------
    # Project operations
    # -------------------------------------------------------------------------

    def create_project(self, data: ProjectCreate) -> Project:
        """Create a new project."""
        now = self._now()
        uid = f"{uuid4()}@genesis"

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

        self._projects[uid] = project
        return project

    def get_project(self, uid: str) -> Project | None:
        """Get a project by UID."""
        return self._projects.get(uid)

    def list_projects(
        self,
        status: ProjectStatus | None = None,
        deadline_before: date | None = None,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ) -> list[Project]:
        """List projects with optional filtering."""
        projects = list(self._projects.values())

        if status:
            projects = [p for p in projects if p.status == status]
        if deadline_before:
            projects = [
                p for p in projects if p.deadline and p.deadline < deadline_before
            ]
        if search:
            search_lower = search.lower()
            projects = [
                p
                for p in projects
                if search_lower in p.name.lower()
                or (p.description and search_lower in p.description.lower())
            ]

        if sort_by:
            for key in reversed(sort_by):
                projects.sort(
                    key=lambda p: getattr(p, key, ""), reverse=(sort_order == "desc")
                )

        return projects

    def update_project(self, uid: str, data: ProjectUpdate) -> Project | None:
        """Update a project."""
        project = self._projects.get(uid)
        if not project:
            return None

        now = self._now()
        update_data = data.model_dump(exclude_unset=True)

        for key, value in update_data.items():
            if value is not None:
                setattr(project, key, value)

        project.modified = now
        return project

    def delete_project(self, uid: str) -> bool:
        """Delete a project and remove project_id from linked tasks."""
        if uid not in self._projects:
            return False

        # Clear project_id from linked tasks
        for task_uid in self._project_tasks.get(uid, set()):
            task = self._tasks.get(task_uid)
            if task:
                task.project_id = None
                task.modified = self._now()

        del self._projects[uid]
        if uid in self._project_tasks:
            del self._project_tasks[uid]
        return True
