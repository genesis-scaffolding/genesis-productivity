"""Time log entry entity model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TimeLogEntry(BaseModel):
    """A time log entry.

    Represents a completed work session (e.g. a pomodoro) stored as a VEVENT
    in the time_log collection. Linked to a Task via task_id.

    Attributes:
        uid: Unique identifier.
        start: Session start time, datetime UTC.
        end: Session end time, datetime UTC.
        description: Optional session notes.
        task_id: FK to Task.UID.
        created: When the entry was created.
    """

    uid: str
    start: datetime
    end: datetime
    task_id: str
    description: str | None = None
    created: datetime | None = None

    model_config = {
        "extra": "ignore",
    }


class TimeLogEntryCreate(BaseModel):
    """Data for creating a new time log entry."""

    start: datetime
    end: datetime
    task_id: str
    description: str | None = None


class TimeLogEntryUpdate(BaseModel):
    """Data for updating a time log entry. All fields optional."""

    start: datetime | None = None
    end: datetime | None = None
    task_id: str | None = None
    description: str | None = None
