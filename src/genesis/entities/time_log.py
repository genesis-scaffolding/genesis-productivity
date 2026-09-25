"""Time log entry entity model.

A TimeLogEntry is a VTODO stored in the time_log collection. It
covers both active focus sessions (STATUS != COMPLETED) and completed
historical records (STATUS == COMPLETED). See ADR-014.

All datetime fields hold tz-aware UTC datetimes per the project
convention documented in ``genesis.tz``. Naive input is coerced to
UTC at construction by ``_utc_field``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, field_validator

from ..category.enums import TimeLogCategory
from ..tz import to_utc


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
        category: Coarse tag for what kind of time the session is. Defaults
            to ``OTHER`` for legacy entries that predate the feature; the
            migration CLI backfills via inference.
        description: Optional session notes.
        created: When the entry was created.
        modified: When the entry was last modified.
        last_milestone_minutes: Highest elapsed minute value at which a
            30-minute milestone notification has already fired for this
            session. Maps to iCal X-LAST-MILESTONE-MINUTES. Defaults to
            ``0`` so legacy VTODOs without the property (and fresh
            sessions that have not yet crossed 30 minutes) read back as
            "no milestone notified yet". See ADR-028.
    """

    uid: str
    label: str
    start: datetime
    due: datetime | None = None
    completed_at: datetime | None = None
    status: TimeLogStatus = TimeLogStatus.NEEDS_ACTION
    mode: TimeLogMode
    task_id: str | None = None
    category: TimeLogCategory = TimeLogCategory.OTHER
    description: str | None = None
    created: datetime | None = None
    modified: datetime | None = None
    last_milestone_minutes: int = 0

    model_config = {
        "extra": "ignore",
    }

    @field_validator("start", "due", "completed_at", "created", "modified", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v: object) -> object:
        if isinstance(v, TimeLogCategory):
            return v
        if isinstance(v, str):
            return TimeLogCategory(v)
        return v


class TimeLogEntryCreate(BaseModel):
    """Data for creating a new time log entry (typically a new session).

    ``completed_at`` defaults to None; callers that want a session
    that is already finished on creation (retrospective logging, where
    the user records a session after the fact) set it to the session
    end time alongside ``status=COMPLETED``.
    """

    label: str
    start: datetime
    mode: TimeLogMode
    due: datetime | None = None
    completed_at: datetime | None = None
    status: TimeLogStatus = TimeLogStatus.NEEDS_ACTION
    task_id: str | None = None
    category: TimeLogCategory = TimeLogCategory.OTHER
    description: str | None = None
    last_milestone_minutes: int = 0

    model_config = {"extra": "ignore"}

    @field_validator("start", "due", "completed_at", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v: object) -> object:
        if isinstance(v, TimeLogCategory):
            return v
        if isinstance(v, str):
            return TimeLogCategory(v)
        return v


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
    category: TimeLogCategory | None = None
    description: str | None = None
    last_milestone_minutes: int | None = None

    model_config = {"extra": "ignore"}

    @field_validator("start", "due", "completed_at", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v: object) -> object:
        if isinstance(v, TimeLogCategory):
            return v
        if isinstance(v, str):
            return TimeLogCategory(v)
        return v
