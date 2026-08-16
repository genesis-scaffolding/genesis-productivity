"""Facade for CalDAV operations.

Provides a single entry point for tasks, projects, and calendar access.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


class Task(BaseModel):
    """A task item."""

    uid: str
    summary: str
    description: str | None = None
    due: datetime | None = None
    completed: bool = False
    created: datetime | None = None
    modified: datetime | None = None


class Project(BaseModel):
    """A project, represented as a calendar in CalDAV."""

    uid: str
    name: str
    description: str | None = None
    created: datetime | None = None


class Event(BaseModel):
    """A calendar event."""

    uid: str
    summary: str
    description: str | None = None
    start: datetime
    end: datetime | None = None
    location: str | None = None


class CalDAVFacade:
    """Facade for CalDAV access to tasks, projects, and calendar.

    This is a placeholder. Actual implementation will connect to a
    CalDAV server and sync tasks/projects/events.
    """

    def __init__(self, server_url: str, username: str, password: str) -> None:
        self._server_url = server_url
        self._username = username
        self._password = password

    def get_tasks(self) -> list[Task]:
        """Return all tasks."""
        return []

    def create_task(self, summary: str, description: str | None = None) -> Task:
        """Create a new task."""
        return Task(uid="", summary=summary, description=description)

    def get_projects(self) -> list[Project]:
        """Return all projects (calendars)."""
        return []

    def get_events(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Event]:
        """Return calendar events in the given range."""
        return []
