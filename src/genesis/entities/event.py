"""Event entity model."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class EventStatus(StrEnum):
    """Event status values.

    Mirrors the iCalendar VEVENT STATUS property.
    """

    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    CANCELED = "canceled"


class Event(BaseModel):
    """An event entity.

    Represents a calendar appointment stored as a VEVENT. Used for both
    standalone appointments and shadow events that mirror time-blocked tasks.

    Attributes:
        uid: Unique identifier.
        summary: Event title.
        start: Event start time, datetime UTC.
        end: Event end time, datetime UTC.
        location: Optional location string.
        description: Optional notes.
        status: Event lifecycle status. Defaults to CONFIRMED.
        task_id: Optional FK to Task.UID. Present only on shadow events
            created from a Task with scheduled_start. Direct mutation of
            task_id via update_event is not supported; manage shadows
            through update_task.
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

    model_config = {
        "extra": "ignore",
    }


class EventCreate(BaseModel):
    """Data for creating a new event."""

    summary: str
    start: datetime
    end: datetime
    location: str | None = None
    description: str | None = None
    status: EventStatus = EventStatus.CONFIRMED


class EventUpdate(BaseModel):
    """Data for updating an event. All fields optional.

    Note: task_id cannot be set or cleared via this model. Shadow events
    are managed by update_task on the task side.
    """

    summary: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    description: str | None = None
    status: EventStatus | None = None
