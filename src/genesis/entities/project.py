"""Project entity model."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, field_validator

from ..tz import to_utc


class ProjectStatus(StrEnum):
    """Project status values."""

    NEEDS_ACTION = "needs-action"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    CANCELED = "canceled"


class Project(BaseModel):
    """A project entity.

    A project represents an outcome-level goal that spans multiple sessions.

    Attributes:
        uid: Unique identifier.
        name: Project name.
        description: Optional markdown content for goals and notes.
        start_date: When the project starts.
        deadline: When the project is due.
        status: Current status in the lifecycle.
        created: When the project was created.
        modified: When the project was last modified.
    """

    uid: str
    name: str
    description: str | None = None
    start_date: date | None = None
    deadline: date | None = None
    status: ProjectStatus = ProjectStatus.NEEDS_ACTION
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {
        "extra": "ignore",
    }

    @field_validator("created", "modified", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v


class ProjectCreate(BaseModel):
    """Data for creating a new project."""

    name: str
    description: str | None = None
    start_date: date | None = None
    deadline: date | None = None


class ProjectUpdate(BaseModel):
    """Data for updating a project. All fields optional."""

    name: str | None = None
    description: str | None = None
    start_date: date | None = None
    deadline: date | None = None
    status: ProjectStatus | None = None
