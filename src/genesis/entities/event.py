"""Event entity model.

All datetime fields hold tz-aware UTC datetimes per the project
convention in ``genesis.tz``. Naive input is coerced to UTC at
construction.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, field_validator

from ..tz import to_utc


class EventStatus(StrEnum):
    """Event status values."""

    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    CANCELED = "canceled"


class Event(BaseModel):
    """An event entity.

    Events are shadow calendar entries generated from tasks with
    ``scheduled_start``. Direct creation is supported but discouraged;
    task-driven shadows are the primary path.

    Attributes:
        uid: Unique identifier.
        summary: Event title.
        start: Event start time, datetime UTC.
        end: Event end time, datetime UTC.
        location: Optional location.
        description: Optional detailed description.
        status: Current status.
        task_id: FK to Task.uid when this is a shadow event. None for
            standalone events. Shadow event update through update_event
            is not supported; manage shadows through update_task.
        created: When the event was created.
        modified: When the event was last modified.
    """

    uid: str
    summary: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    status: EventStatus = EventStatus.CONFIRMED
    task_id: str | None = None
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {"extra": "ignore"}

    @field_validator("start", "end", "created", "modified", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v


class EventCreate(BaseModel):
    """Data for creating a new event."""

    summary: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    status: EventStatus = EventStatus.CONFIRMED
    task_id: str | None = None

    model_config = {"extra": "ignore"}

    @field_validator("start", "end", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v


class EventUpdate(BaseModel):
    """Data for updating an event. All fields optional.

    Updating a shadow event through update_event is not supported;
    manage shadows through update_task on the task side.
    """

    summary: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    description: str | None = None
    status: EventStatus | None = None

    model_config = {"extra": "ignore"}

    @field_validator("start", "end", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v
