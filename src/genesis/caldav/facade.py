"""CalDAV facade for tasks, projects, events, and time log.

The facade manages a CalDAV connection to a Radicale server, lazily
loads the four fixed top-level collections, and exposes 20 one-line
delegations to module-level CRUD functions. See ADR-012 for the four-
collection layout and the shadow VEVENT pattern.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..category.enums import TimeLogCategory
from . import events, projects, tasks, time_log
from .constants import (
    EVENTS_COLLECTION,
    PROJECTS_COLLECTION,
    TASKS_COLLECTION,
    TIME_LOG_COLLECTION,
)
from .ical import now_utc


class CalDAVFacade:
    """Facade for CalDAV storage of tasks, projects, events, and time log.

    Connects to a Radicale server and exposes four cohesive CRUD surfaces,
    one per entity type. Each surface is a thin delegation to a module
    function in projects.py, tasks.py, events.py, or time_log.py.
    """

    PROJECTS_COLLECTION: str = PROJECTS_COLLECTION
    TASKS_COLLECTION: str = TASKS_COLLECTION
    EVENTS_COLLECTION: str = EVENTS_COLLECTION
    TIME_LOG_COLLECTION: str = TIME_LOG_COLLECTION

    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
    ) -> None:
        self._server_url = server_url
        self._username = username
        self._password = password

        self._client: Any = None
        self._principal: Any = None
        self._calendars: dict[str, Any] = {}

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        import caldav

        return caldav.DAVClient(  # type: ignore[call-arg]
            url=self._server_url,
            username=self._username,
            password=self._password,
        )

    def _get_principal(self) -> Any:
        if self._principal is None:
            self._principal = self._get_client().principal()
        return self._principal

    def _get_calendar(self, name: str) -> Any:
        """Return the named collection, creating it if it does not exist."""
        if name not in self._calendars:
            principal = self._get_principal()
            for cal in principal.calendars():  # type: ignore[attr-defined]
                if cal.get_display_name() == name:
                    self._calendars[name] = cal
                    break
            else:
                self._calendars[name] = principal.make_calendar(name)
        return self._calendars[name]

    def now(self) -> Any:
        """Return current UTC datetime. Exposed for callers that need a clock."""
        return now_utc()

    # -------------------------------------------------------------------------
    # Project delegations
    # -------------------------------------------------------------------------

    def create_project(self, data):
        return projects.create_project(self, data)

    def get_project(self, uid: str):
        return projects.get_project(self, uid)

    def list_projects(
        self,
        status=None,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ):
        return projects.list_projects(self, status, search, sort_by, sort_order)

    def update_project(self, uid: str, data):
        return projects.update_project(self, uid, data)

    def delete_project(self, uid: str) -> bool:
        return projects.delete_project(self, uid)

    # -------------------------------------------------------------------------
    # Task delegations
    # -------------------------------------------------------------------------

    def create_task(self, data):
        return tasks.create_task(self, data)

    def get_task(self, uid: str):
        return tasks.get_task(self, uid)

    def list_tasks(
        self,
        project_id: str | None = None,
        status=None,
        search: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ):
        return tasks.list_tasks(self, project_id, status, search, start, end, sort_by, sort_order)

    def update_task(self, uid: str, data):
        return tasks.update_task(self, uid, data)

    def delete_task(self, uid: str) -> bool:
        return tasks.delete_task(self, uid)

    # -------------------------------------------------------------------------
    # Event delegations
    # -------------------------------------------------------------------------

    def create_event(self, data):
        return events.create_event(self, data)

    def get_event(self, uid: str):
        return events.get_event(self, uid)

    def list_events(
        self,
        task_id: str | None = None,
        status=None,
        search: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ):
        return events.list_events(self, task_id, status, search, start, end, sort_by, sort_order)

    def update_event(self, uid: str, data):
        return events.update_event(self, uid, data)

    def delete_event(self, uid: str) -> bool:
        return events.delete_event(self, uid)

    # -------------------------------------------------------------------------
    # Time log delegations
    # -------------------------------------------------------------------------

    def create_time_log_entry(self, data):
        return time_log.create_time_log_entry(self, data)

    def get_time_log_entry(self, uid: str):
        return time_log.get_time_log_entry(self, uid)

    def list_time_log(
        self,
        task_id: str | None = None,
        status=None,
        active_only: bool = False,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ):
        return time_log.list_time_log(self, task_id, status, active_only, sort_by, sort_order)

    def update_time_log_entry(self, uid: str, data):
        return time_log.update_time_log_entry(self, uid, data)

    def stop_time_log_entry(
        self,
        uid: str,
        *,
        actual_due: datetime | None,
        completed_at: datetime | None,
        category: TimeLogCategory | None = None,
    ):
        return time_log.stop_time_log_entry(
            self,
            uid,
            actual_due=actual_due,
            completed_at=completed_at,
            category=category,
        )

    def delete_time_log_entry(self, uid: str) -> bool:
        return time_log.delete_time_log_entry(self, uid)
