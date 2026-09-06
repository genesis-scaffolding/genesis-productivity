"""Mock CalDAV backend for testing.

Stores data in memory and implements the same interface as CalDAVFacade.
Mirrors the four-collection model from ADR-012 and maintains shadow VEVENTs
for tasks with scheduled_start.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from genesis.caldav.constants import DEFAULT_SHADOW_DURATION_MINUTES
from genesis.entities import (
    Event,
    EventCreate,
    EventStatus,
    EventUpdate,
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
    TimeLogEntry,
    TimeLogEntryCreate,
    TimeLogEntryUpdate,
)


class MockCalDAVFacade:
    """Mock CalDAV facade that stores data in memory.

    Mirrors the four-collection model and the in-place-save pattern. No
    network, no subprocess. Each mutation that the real facade performs
    (project delete clearing task.project_id, shadow VEVENT lifecycle)
    is also performed here so unit tests see the same observable state.
    """

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._tasks: dict[str, Task] = {}
        self._events: dict[str, Event] = {}
        self._time_log: dict[str, TimeLogEntry] = {}

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _now(self) -> Any:
        from datetime import UTC, datetime

        return datetime.now(UTC)

    def _new_uid(self) -> str:
        return f"{uuid4()}@genesis"

    # -------------------------------------------------------------------------
    # Projects
    # -------------------------------------------------------------------------

    def create_project(self, data: ProjectCreate) -> Project:
        now = self._now()
        project = Project(
            uid=self._new_uid(),
            name=data.name,
            description=data.description,
            start_date=data.start_date,
            deadline=data.deadline,
            status=ProjectStatus.NEEDS_ACTION,
            created=now,
            modified=now,
        )
        self._projects[project.uid] = project
        return project

    def get_project(self, uid: str) -> Project | None:
        return self._projects.get(uid)

    def list_projects(
        self,
        status: ProjectStatus | None = None,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ) -> list[Project]:
        projects = list(self._projects.values())
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

    def update_project(self, uid: str, data: ProjectUpdate) -> Project | None:
        project = self._projects.get(uid)
        if not project:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(project, key, value)
        project.modified = self._now()
        return project

    def delete_project(self, uid: str) -> bool:
        if uid not in self._projects:
            return False
        # Clear project_id on linked tasks (mirrors real facade behavior)
        for task in list(self._tasks.values()):
            if task.project_id == uid:
                task.project_id = None
                task.modified = self._now()
        del self._projects[uid]
        return True

    # -------------------------------------------------------------------------
    # Tasks
    # -------------------------------------------------------------------------

    def create_task(self, data: TaskCreate) -> Task:
        now = self._now()
        task = Task(
            uid=self._new_uid(),
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
        self._tasks[task.uid] = task
        self._reconcile_shadow(task)
        return task

    def get_task(self, uid: str) -> Task | None:
        return self._tasks.get(uid)

    def list_tasks(
        self,
        project_id: str | None = None,
        status: TaskStatus | None = None,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ) -> list[Task]:
        tasks = list(self._tasks.values())
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
        return tasks

    def update_task(self, uid: str, data: TaskUpdate) -> Task | None:
        task = self._tasks.get(uid)
        if not task:
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
            "status" in update_data
            and task.status == TaskStatus.COMPLETED
            and task.completed_at is None
        ):
            task.completed_at = self._now()
        elif (
            "status" in update_data
            and task.status != TaskStatus.COMPLETED
            and task.completed_at is not None
        ):
            task.completed_at = None

        task.modified = self._now()
        self._reconcile_shadow(task)
        return task

    def delete_task(self, uid: str) -> bool:
        if uid not in self._tasks:
            return False
        # Remove shadow event if present
        self._events.pop(uid, None)
        del self._tasks[uid]
        return True

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def create_event(self, data: EventCreate) -> Event:
        now = self._now()
        event = Event(
            uid=self._new_uid(),
            summary=data.summary,
            start=data.start,
            end=data.end,
            location=data.location,
            description=data.description,
            status=data.status,
            created=now,
            modified=now,
        )
        self._events[event.uid] = event
        return event

    def get_event(self, uid: str) -> Event | None:
        return self._events.get(uid)

    def list_events(
        self,
        task_id: str | None = None,
        status: EventStatus | None = None,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ) -> list[Event]:
        events = list(self._events.values())
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

    def update_event(self, uid: str, data: EventUpdate) -> Event | None:
        event = self._events.get(uid)
        if event is None:
            return None
        if event.task_id is not None:
            raise ValueError(
                f"Event {uid} is a shadow event owned by task {event.task_id}; "
                "update via update_task instead"
            )
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(event, key, value)
        event.modified = self._now()
        return event

    def delete_event(self, uid: str) -> bool:
        event = self._events.get(uid)
        if event is None:
            return False
        if event.task_id is not None:
            raise ValueError(
                f"Event {uid} is a shadow event; clear scheduled_start on "
                "its source task to remove it"
            )
        del self._events[uid]
        return True

    # -------------------------------------------------------------------------
    # Time log
    # -------------------------------------------------------------------------

    def create_time_log_entry(self, data: TimeLogEntryCreate) -> TimeLogEntry:
        now = self._now()
        entry = TimeLogEntry(
            uid=self._new_uid(),
            start=data.start,
            end=data.end,
            task_id=data.task_id,
            description=data.description,
            created=now,
        )
        self._time_log[entry.uid] = entry
        return entry

    def get_time_log_entry(self, uid: str) -> TimeLogEntry | None:
        return self._time_log.get(uid)

    def list_time_log(
        self,
        task_id: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ) -> list[TimeLogEntry]:
        entries = list(self._time_log.values())
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
        self, uid: str, data: TimeLogEntryUpdate
    ) -> TimeLogEntry | None:
        entry = self._time_log.get(uid)
        if entry is None:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(entry, key, value)
        return entry

    def delete_time_log_entry(self, uid: str) -> bool:
        if uid not in self._time_log:
            return False
        del self._time_log[uid]
        return True

    # -------------------------------------------------------------------------
    # Shadow maintenance (mirrors TasksMixin._reconcile_shadow)
    # -------------------------------------------------------------------------

    def _reconcile_shadow(self, task: Task) -> None:
        if task.scheduled_start is None:
            self._events.pop(task.uid, None)
            return

        duration = (
            task.scheduled_duration
            if task.scheduled_duration is not None
            else DEFAULT_SHADOW_DURATION_MINUTES
        )
        self._events[task.uid] = Event(
            uid=task.uid,
            summary=task.summary,
            start=task.scheduled_start,
            end=task.scheduled_start + timedelta(minutes=duration),
            status=EventStatus.CONFIRMED,
            task_id=task.uid,
            modified=task.modified,
        )
