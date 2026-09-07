"""Wire-shape Pydantic models for the agent API.

The agent extension speaks these names over HTTP. Internally, the
caller translates to/from the genesis entity models in
``genesis.entities``.

Datetime format: ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (3-decimal, Z suffix).
Implemented as a shared Pydantic annotation so wire datetimes
serialise the same way everywhere.

Note on the journal wire model: the entity calls the calendar day
field ``date`` (see :class:`genesis.journal.facade.Entry`). The wire
model also uses ``date`` for clarity. Because the field name
collides with the ``datetime.date`` class, we annotate with the
fully-qualified ``datetime.date`` everywhere.
"""

import datetime
from datetime import UTC
from datetime import datetime as _datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from ..entities import ProjectStatus, TaskStatus
from ..journal.facade import EntryType

# ---------------------------------------------------------------------------
# Datetime serialization
# ---------------------------------------------------------------------------


def _format_utc_z(dt: _datetime) -> str:
    """Render a tz-aware datetime as ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
    utc = dt.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


UtcDateTime = Annotated[
    _datetime,
    PlainSerializer(lambda dt: _format_utc_z(dt), when_used="json"),
]


# ---------------------------------------------------------------------------
# Re-exports / wire enums
# ---------------------------------------------------------------------------


class Status(StrEnum):
    """Wire-side mirror of ``TaskStatus``. Values match the iCal form
    so storage and wire stay aligned.
    """

    NEEDS_ACTION = "needs-action"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    CANCELED = "canceled"


# Map wire enum to entity enum.
STATUS_TO_TASK_ENTITY = {
    Status.NEEDS_ACTION: TaskStatus.NEEDS_ACTION,
    Status.IN_PROGRESS: TaskStatus.IN_PROGRESS,
    Status.COMPLETED: TaskStatus.COMPLETED,
    Status.CANCELED: TaskStatus.CANCELED,
}
STATUS_TO_PROJECT_ENTITY = {
    Status.NEEDS_ACTION: ProjectStatus.NEEDS_ACTION,
    Status.IN_PROGRESS: ProjectStatus.IN_PROGRESS,
    Status.COMPLETED: ProjectStatus.COMPLETED,
    Status.CANCELED: ProjectStatus.CANCELED,
}
TASK_STATUS_TO_WIRE = {v: k for k, v in STATUS_TO_TASK_ENTITY.items()}
PROJECT_STATUS_TO_WIRE = {v: k for k, v in STATUS_TO_PROJECT_ENTITY.items()}


def wire_to_task_status(s: Status) -> TaskStatus:
    return STATUS_TO_TASK_ENTITY[s]


def wire_to_project_status(s: Status) -> ProjectStatus:
    return STATUS_TO_PROJECT_ENTITY[s]

# Wire-side journal type re-exported as a string-valued alias.
# The entity's EntryType values (``daily | weekly | monthly | yearly |
# project | misc``) are the wire values; no mapping is required.
JournalWireType = EntryType


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class ProjectCreateIn(BaseModel):
    name: str
    description: str | None = None
    status: Status = Status.NEEDS_ACTION
    start_date: datetime.date | None = None
    deadline: datetime.date | None = None


class ProjectUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    status: Status | None = None
    start_date: datetime.date | None = None
    deadline: datetime.date | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: str
    name: str
    description: str | None
    status: Status
    start_date: datetime.date | None
    deadline: datetime.date | None
    created: UtcDateTime | None
    modified: UtcDateTime | None


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class TaskCreateIn(BaseModel):
    title: str
    description: str | None = None
    status: Status = Status.NEEDS_ACTION
    assigned_date: datetime.date | None = None
    hard_deadline: UtcDateTime | None = None
    scheduled_start: UtcDateTime | None = None
    duration_minutes: int | None = None
    priority: int | None = None
    project_id: str | None = None


class TaskUpdateIn(BaseModel):
    title: str | None = None
    description: str | None = None
    status: Status | None = None
    assigned_date: datetime.date | None = None
    hard_deadline: UtcDateTime | None = None
    scheduled_start: UtcDateTime | None = None
    clear_scheduled_start: bool = False
    duration_minutes: int | None = None
    priority: int | None = None
    project_id: str | None = None
    clear_project_id: bool = False


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: str
    title: str
    description: str | None
    status: Status
    assigned_date: datetime.date | None
    hard_deadline: UtcDateTime | None
    scheduled_start: UtcDateTime | None
    duration_minutes: int | None
    priority: int | None
    project_id: str | None
    completed_at: UtcDateTime | None
    created: UtcDateTime | None
    modified: UtcDateTime | None


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


class JournalEntryCreateIn(BaseModel):
    type: JournalWireType
    date: datetime.date
    title: str | None = None
    content: str
    project_id: str | None = None


class JournalEntryUpdateIn(BaseModel):
    title: str | None = None
    content: str | None = None
    date: datetime.date | None = None
    project_id: str | None = None
    clear_project_id: bool = False


class JournalEntryOut(BaseModel):
    uid: str
    type: JournalWireType | None
    date: datetime.date
    title: str | None
    content: str
    project_id: str | None
    created: UtcDateTime | None
    modified: UtcDateTime | None


class JournalListOut(BaseModel):
    items: list[JournalEntryOut]
    total: int


# ---------------------------------------------------------------------------
# Time log
# ---------------------------------------------------------------------------


class TimeLogMode(StrEnum):
    COUNTDOWN = "countdown"
    COUNTUP = "countup"


class TimeLogStatus(StrEnum):
    NEEDS_ACTION = "needs-action"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    CANCELED = "canceled"


class TimeLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: str
    label: str
    start: UtcDateTime
    due: UtcDateTime | None
    completed_at: UtcDateTime | None
    status: TimeLogStatus
    mode: TimeLogMode
    task_id: str | None
    description: str | None
    created: UtcDateTime | None
    modified: UtcDateTime | None


class TimeLogListOut(BaseModel):
    items: list[TimeLogEntryOut]
    total: int


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshIn(BaseModel):
    refresh_token: str
