"""Time log entry entity model.

A TimeLogEntry is a VTODO stored in the time_log collection. It
covers both active focus sessions (STATUS != COMPLETED) and completed
historical records (STATUS == COMPLETED). See ADR-014.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class TimeLogStatus(StrEnum):
    """VTODO lifecycle status.

    Mirrors the iCalendar values for VTODO STATUS.
    """

    NEEDS_ACTION = "needs-action"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    CANCELED = "canceled"


class TimeLogMode(StrEnum):
    """Whether the session is bounded by a target duration or unbounded."""

    COUNTDOWN = "countdown"
    COUNTUP = "countup"


class TimeLogEntry(BaseModel):
    """A time log entry (focus session, active or completed).

    Attributes:
        uid: Unique identifier.
        label: Human-readable name. Task summary when aligned, user-supplied
            otherwise. Maps to iCal SUMMARY.
        start: Session start time, datetime UTC. Maps to DTSTART.
        due: Countdown target end when active; actual end when stopped.
            Absent for count-up until stopped. Maps to DUE.
        completed_at: When STATUS transitioned to COMPLETED. Maps to
            iCal COMPLETED.
        status: Current lifecycle status.
        mode: Whether the session is bounded (countdown) or unbounded (count-up).
        task_id: FK to Task.uid when aligned, None for unaligned sessions.
        description: Optional session notes.
        created: When the entry was created.
        modified: When the entry was last modified.
    """

    uid: str
    label: str
    start: datetime
    due: datetime | None = None
    completed_at: datetime | None = None
    status: TimeLogStatus = TimeLogStatus.NEEDS_ACTION
    mode: TimeLogMode
    task_id: str | None = None
    description: str | None = None
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {
        "extra": "ignore",
    }


class TimeLogEntryCreate(BaseModel):
    """Data for creating a new time log entry (typically a new session)."""

    label: str
    start: datetime
    mode: TimeLogMode
    due: datetime | None = None
    status: TimeLogStatus = TimeLogStatus.NEEDS_ACTION
    task_id: str | None = None
    description: str | None = None


class TimeLogEntryUpdate(BaseModel):
    """Data for updating a time log entry. All fields optional.

    The stop flow uses status, due, and completed_at; everything else is
    for ad-hoc edits.
    """

    label: str | None = None
    start: datetime | None = None
    due: datetime | None = None
    completed_at: datetime | None = None
    status: TimeLogStatus | None = None
    mode: TimeLogMode | None = None
    task_id: str | None = None
    description: str | None = None
