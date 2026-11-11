"""CalDAV facade for tasks, projects, events, and time log.

The facade manages a CalDAV connection to a Radicale server, lazily
loads the fixed top-level collections, and exposes one-line delegations
to module-level CRUD functions. See ADR-012 for the collection layout
and the shadow VEVENT pattern, ADR-033 for the recurrence series.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from ..category.enums import TimeLogCategory
from . import events, projects, recurrence, tasks, time_log, xp_events
from .constants import (
    EVENTS_COLLECTION,
    PROJECTS_COLLECTION,
    RECURRENCES_COLLECTION,
    TASKS_COLLECTION,
    TIME_LOG_COLLECTION,
    XP_EVENTS_COLLECTION,
)
from .ical import now_utc


class CalDAVFacade:
    """Facade for CalDAV storage of tasks, projects, events, and time log.

    Connects to a Radicale server and exposes one cohesive CRUD surface
    per entity type. Each surface is a thin delegation to a module
    function in projects.py, tasks.py, events.py, time_log.py, or
    recurrence.py.
    """

    PROJECTS_COLLECTION: str = PROJECTS_COLLECTION
    TASKS_COLLECTION: str = TASKS_COLLECTION
    EVENTS_COLLECTION: str = EVENTS_COLLECTION
    TIME_LOG_COLLECTION: str = TIME_LOG_COLLECTION
    XP_EVENTS_COLLECTION: str = XP_EVENTS_COLLECTION
    RECURRENCES_COLLECTION: str = RECURRENCES_COLLECTION

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
        completed_at_start: datetime | None = None,
        completed_at_end: datetime | None = None,
        series_uid: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ):
        return tasks.list_tasks(
            self,
            project_id,
            status,
            search,
            start,
            end,
            completed_at_start,
            completed_at_end,
            series_uid,
            sort_by,
            sort_order,
        )

    def update_task(self, uid: str, data):
        task = tasks.update_task(self, uid, data)
        self._reconcile_series_of(task)
        return task

    def delete_task(self, uid: str) -> bool:
        existing = self.get_task(uid)
        deleted = tasks.delete_task(self, uid)
        if deleted and existing is not None and existing.recurrence_series_uid:
            # A deleted current occurrence is a skipped occurrence: the
            # next open occurrence is promoted and the window restored.
            recurrence.reconcile_series(self._recurrence_store(), existing.recurrence_series_uid)
        return deleted

    # -------------------------------------------------------------------------
    # Recurrence series (ADR-033)
    # -------------------------------------------------------------------------

    def _recurrence_store(self) -> recurrence.CalDAVRecurrenceStore:
        """Return the recurrence store bound to this facade."""
        return recurrence.CalDAVRecurrenceStore(self)

    def _reconcile_series_of(self, task) -> None:
        """Restore the recurrence window of the series a task belongs to.

        Called after a task write: when the current occurrence becomes
        terminal, the next open occurrence is promoted and the tail of
        the window is materialized again.
        """
        if task is None or not getattr(task, "recurrence_series_uid", None):
            return
        recurrence.reconcile_series(self._recurrence_store(), task.recurrence_series_uid)

    def create_recurrent_task(self, data, *, rule, materialized_future_count: int):
        """Create a recurrent task with its series and future occurrences."""
        return recurrence.create_recurrent_task(
            self._recurrence_store(),
            data,
            rule=rule,
            materialized_future_count=materialized_future_count,
        )

    def update_task_scoped(self, uid: str, data, *, scope: str = recurrence.SCOPE_CURRENT, rule_change=None):
        """Update a task with an explicit edit scope."""
        return recurrence.update_task_scoped(
            self._recurrence_store(),
            uid,
            data,
            scope=scope,
            rule_change=rule_change,
        )

    def cancel_future(self, uid: str):
        """Cancel the future occurrences of the task's series."""
        return recurrence.cancel_future(self._recurrence_store(), uid)

    def resize_series(self, series_uid: str, materialized_future_count: int):
        """Change the materialized horizon of a running series."""
        return recurrence.resize_series(self._recurrence_store(), series_uid, materialized_future_count)

    def get_series(self, series_uid: str):
        """Return one recurrence series, or None."""
        return self._recurrence_store().get_series(series_uid)

    def list_series(self):
        """Return every recurrence series."""
        return self._recurrence_store().list_series()

    def list_series_tasks(self, series_uid: str):
        """Return every task that belongs to the given series."""
        return self._recurrence_store().list_series_tasks(series_uid)

    def get_series_state(self, series_uid: str):
        """Return the inspection state of a series, or None."""
        return recurrence.series_state(self._recurrence_store(), series_uid)

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
        scope: Literal["all", "completed"] = "completed",
    ):
        return time_log.list_time_log(self, task_id, status, active_only, sort_by, sort_order, scope)

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

    # -------------------------------------------------------------------------
    # XP event delegations
    # -------------------------------------------------------------------------

    def create_xp_event(self, data):
        return xp_events.create_xp_event(self, data)

    def get_xp_event(self, uid: str):
        return xp_events.get_xp_event(self, uid)

    def list_xp_events(
        self,
        stat_id: str | None = None,
        kind=None,
        source_task_id: str | None = None,
        source_session_id: str | None = None,
        idempotency_key: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "desc",
    ):
        return xp_events.list_xp_events(
            self,
            stat_id=stat_id,
            kind=kind,
            source_task_id=source_task_id,
            source_session_id=source_session_id,
            idempotency_key=idempotency_key,
            since=since,
            until=until,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def delete_xp_event(self, uid: str) -> bool:
        return xp_events.delete_xp_event(self, uid)
