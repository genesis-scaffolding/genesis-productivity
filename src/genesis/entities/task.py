"""Task entity model."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel


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
        hard_deadline: The firm delivery date.
        assigned_date: The calendar day the user plans to work on the task.
        scheduled_start: When the task becomes a calendar appointment.
        status: Current status in the lifecycle.
        project_id: Optional project this task belongs to.
        completed_at: When the task was completed.
        created: When the task was created.
        modified: When the task was last modified.
    """

    uid: str
    summary: str
    description: str | None = None
    hard_deadline: datetime | None = None
    assigned_date: date | None = None
    scheduled_start: datetime | None = None
    status: TaskStatus = TaskStatus.NEEDS_ACTION
    project_id: str | None = None
    completed_at: datetime | None = None
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {
        "extra": "ignore",
    }


class TaskCreate(BaseModel):
    """Data for creating a new task."""

    summary: str
    description: str | None = None
    hard_deadline: datetime | None = None
    assigned_date: date | None = None
    scheduled_start: datetime | None = None
    project_id: str | None = None


class TaskUpdate(BaseModel):
    """Data for updating a task. All fields optional."""

    summary: str | None = None
    description: str | None = None
    hard_deadline: datetime | None = None
    assigned_date: date | None = None
    scheduled_start: datetime | None = None
    status: TaskStatus | None = None
    project_id: str | None = None
    clear_project_id: bool = False  # Set to True to remove project link
