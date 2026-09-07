"""Task entity model.

All datetime fields hold tz-aware UTC datetimes per the project
convention in ``genesis.tz``. Naive input is coerced to UTC at
construction.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, field_validator

from ..tz import to_utc


class TaskStatus(StrEnum):
    """Task status values."""

    NEEDS_ACTION = "needs-action"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    CANCELED = "canceled"


class Task(BaseModel):
    """A task entity.

    Attributes:
        uid: Unique identifier.
        summary: Task title.
        description: Optional detailed description.
        hard_deadline: The firm delivery date (datetime UTC; stored as DUE).
        assigned_date: The calendar day the user plans to work on the task
            (date, no tz needed).
        scheduled_start: When the task becomes a calendar appointment (UTC).
        scheduled_duration: Appointment length in minutes. Used to compute the
            shadow VEVENT's DTEND. Ignored when scheduled_start is None.
        priority: 1-9 where 1 is highest. Mirrors iCalendar PRIORITY.
        status: Current status in the lifecycle.
        project_id: Optional project this task belongs to.
        completed_at: When the task was completed (UTC).
        created: When the task was created.
        modified: When the task was last modified.
    """

    uid: str
    summary: str
    description: str | None = None
    hard_deadline: datetime | None = None
    assigned_date: date | None = None
    scheduled_start: datetime | None = None
    scheduled_duration: int | None = None
    priority: int | None = None
    status: TaskStatus = TaskStatus.NEEDS_ACTION
    project_id: str | None = None
    completed_at: datetime | None = None
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {"extra": "ignore"}

    @field_validator(
        "hard_deadline",
        "scheduled_start",
        "completed_at",
        "created",
        "modified",
        mode="before",
    )
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v


class TaskCreate(BaseModel):
    """Data for creating a new task."""

    summary: str
    description: str | None = None
    hard_deadline: datetime | None = None
    assigned_date: date | None = None
    scheduled_start: datetime | None = None
    scheduled_duration: int | None = None
    priority: int | None = None
    project_id: str | None = None

    model_config = {"extra": "ignore"}

    @field_validator("hard_deadline", "scheduled_start", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v


class TaskUpdate(BaseModel):
    """Data for updating a task. All fields optional.

    Set scheduled_start to None to clear it. Set clear_scheduled_start to True
    when you need to distinguish "do not touch scheduled_start" from "clear
    scheduled_start" in an update call.
    """

    summary: str | None = None
    description: str | None = None
    hard_deadline: datetime | None = None
    assigned_date: date | None = None
    scheduled_start: datetime | None = None
    clear_scheduled_start: bool = False
    scheduled_duration: int | None = None
    priority: int | None = None
    status: TaskStatus | None = None
    project_id: str | None = None
    clear_project_id: bool = False

    model_config = {"extra": "ignore"}

    @field_validator("hard_deadline", "scheduled_start", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v
