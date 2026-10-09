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

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from ..category.enums import TimeLogCategory
from ..entities import ProjectStatus, TaskStatus
from ..journal.facade import EntryType
from ..profile import ProfileTarget

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


class ProjectListOut(BaseModel):
    items: list[ProjectOut]
    total: int
    offset: int
    limit: int


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
    reference_date: datetime.date | None = None
    title: str | None = None
    content: str
    project_id: str | None = None


class JournalEntryUpdateIn(BaseModel):
    title: str | None = None
    content: str | None = None
    project_id: str | None = None
    clear_project_id: bool = False


class JournalEntryOut(BaseModel):
    uid: str
    type: JournalWireType | None
    date: datetime.date
    reference_date: datetime.date | None
    title: str | None
    content: str
    project_id: str | None
    created: UtcDateTime | None
    modified: UtcDateTime | None


class JournalListOut(BaseModel):
    items: list[JournalEntryOut]
    total: int
    offset: int
    limit: int


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
    category: TimeLogCategory = TimeLogCategory.OTHER
    description: str | None
    created: UtcDateTime | None
    modified: UtcDateTime | None
    last_milestone_minutes: int = 0


class TimeLogListOut(BaseModel):
    items: list[TimeLogEntryOut]
    total: int
    offset: int
    limit: int


class FocusSessionStartIn(BaseModel):
    """Body for POST /productivity/time_log.

    Starts a new focus session. ``mode`` is ``countdown`` (bounded,
    requires ``duration_minutes``) or ``countup`` (unbounded).
    """

    label: str = Field(..., min_length=1, description="Session label.")
    mode: TimeLogMode = Field(..., description="countdown or countup.")
    duration_minutes: int | None = Field(None, ge=1, description="Countdown length. Required for countdown mode.")
    category: TimeLogCategory | None = Field(None, description="Session category.")
    task_id: str | None = Field(None, description="Aligned task uid, if any.")
    description: str | None = Field(None, description="Session notes.")


class FocusSessionLodgeIn(BaseModel):
    """Body for POST /productivity/time_log/completed.

    Lodges a completed focus session (retrospective logging). The
    session is recorded as already finished: ``due`` and
    ``completed_at`` are both ``start + duration_minutes``.
    """

    label: str = Field(..., min_length=1, description="Session label.")
    start: UtcDateTime = Field(..., description="Session start (UTC).")
    duration_minutes: int = Field(..., ge=1, description="Session length in minutes.")
    category: TimeLogCategory | None = Field(None, description="Session category.")
    task_id: str | None = Field(None, description="Aligned task uid, if any.")
    description: str | None = Field(None, description="Session notes.")


class FocusSessionStopIn(BaseModel):
    """Body for POST /productivity/time_log/{uid}/stop.

    All fields optional. Missing ``completed_at`` defaults to now.
    """

    completed_at: UtcDateTime | None = Field(None, description="Stop time. Defaults to now.")
    category: TimeLogCategory | None = Field(None, description="Override the session category.")


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class StatisticsPeriod(StrEnum):
    """Period selector for the statistics rollup.

    Values match :class:`genesis.stats.Period` exactly. Keeping a
    wire-side enum means the contract is self-describing in OpenAPI
    and decoupled from internal renames.
    """

    THIS_WEEK = "this-week"
    THIS_MONTH = "this-month"
    LAST_30_DAYS = "last-30-days"
    ALL_TIME = "all-time"


class KpisOut(BaseModel):
    total_minutes: float
    session_count: int
    avg_minutes: float
    tasks_touched: int
    minutes_by_category: dict[TimeLogCategory, float]


class DailyPointOut(BaseModel):
    day: datetime.date
    minutes: float
    minutes_by_category: dict[TimeLogCategory, float]


class ByTaskOut(BaseModel):
    title: str
    count: int
    total_seconds: float
    avg_seconds: float


class ByCategoryOut(BaseModel):
    category: TimeLogCategory
    total_seconds: float
    count: int


class RecentSessionOut(BaseModel):
    uid: str
    start: UtcDateTime
    label: str
    task_title: str
    mode: TimeLogMode
    duration_seconds: float


class StatisticsOut(BaseModel):
    period: StatisticsPeriod
    kpis: KpisOut
    daily: list[DailyPointOut]
    by_task: list[ByTaskOut]
    by_category: list[ByCategoryOut]
    recent: list[RecentSessionOut]


# ---------------------------------------------------------------------------
# Profile (ADR-025)
# ---------------------------------------------------------------------------


# Wire-side mirror of ProfileTarget. The enum values
# (``profile`` / ``life_story``) are the on-wire strings; the
# Python identifier is ``ProfileWireTarget`` to keep import lines
# short elsewhere.
ProfileWireTarget = ProfileTarget


class ProfileContentOut(BaseModel):
    """Read response: target + path + trimmed content."""

    target: ProfileWireTarget
    path: str
    content: str


class ProfileWriteIn(BaseModel):
    """Overwrite request body."""

    content: str
    note: str | None = None


class ProfileEditIn(BaseModel):
    """Surgical edit request body.

    ``global_replace`` replaces every occurrence; the default is
    first-only (matching the storage layer's contract). Empty
    ``old_text`` is rejected by the storage layer with a 400.
    """

    old_text: str
    new_text: str
    global_replace: bool = False
    note: str | None = None


class ProfileWriteOut(BaseModel):
    """Write / edit response: what was written + whether a snapshot was taken."""

    target: ProfileWireTarget
    path: str
    bytes_written: int
    replacements: int = 0
    snapshot: str | None = Field(
        default=None,
        description=("Filename of the snapshot taken after the write, or None when dedup skipped it."),
    )


class ProfileListRowOut(BaseModel):
    """One row of :class:`ProfileListOut`."""

    target: ProfileWireTarget
    path: str
    exists: bool
    bytes: int


class ProfileListOut(BaseModel):
    rows: list[ProfileListRowOut]
    profile_root: str


class ProfileSnapshotOut(BaseModel):
    """One history row."""

    ts: str
    filename: str
    sha: str | None
    size: int
    command: str | None
    note: str | None


class ProfileHistoryOut(BaseModel):
    target: ProfileWireTarget
    rows: list[ProfileSnapshotOut]
    total: int


class ProfileRestoreIn(BaseModel):
    """Restore request body."""

    version: str = Field(
        description=(
            "Snapshot identifier. Accepts the full UTC timestamp, a unique "
            "prefix of it, the bare .md filename, or the bare .meta.json filename. "
            "Errors on ambiguous prefix."
        ),
    )
    note: str | None = None


class ProfileRestoreOut(BaseModel):
    target: ProfileWireTarget
    applied_filename: str
    applied_size: int
    pre_restore_snapshot: str | None


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


# ---------------------------------------------------------------------------
# Game (ADR-026)
# ---------------------------------------------------------------------------


class XpEventKindWire(StrEnum):
    """Wire-side mirror of ``XpEventKind``."""

    GAIN = "gain"
    LOSS = "loss"


class StatOut(BaseModel):
    """One stat in the user's stat config."""

    id: str
    name: str


class StatListOut(BaseModel):
    items: list[StatOut]


class StatUpdateIn(BaseModel):
    """PATCH body for updating a stat's display name."""

    name: str


class XpEventOut(BaseModel):
    """One XP event for the wire."""

    uid: str
    stat_id: str
    delta: int
    kind: XpEventKindWire
    source_task_id: str | None
    source_session_id: str | None
    reason: str
    occurred_at: UtcDateTime
    idempotency_key: str


class XpEventListOut(BaseModel):
    items: list[XpEventOut]
    total: int
    offset: int
    limit: int


class XpEventCreateIn(BaseModel):
    """Body for POST /game/character/xp-events.

    Exactly one of ``task_id``, ``session_id``, ``reason`` is
    required. ``stat_id`` and ``delta`` are optional; when omitted
    the game facade infers from the source. ``idempotency_key`` is
    optional for task/session (auto-derived) and required for
    reason.
    """

    task_id: str | None = None
    session_id: str | None = None
    reason: str | None = None
    stat_id: str | None = None
    delta: int | None = None
    idempotency_key: str | None = None
    occurred_at: UtcDateTime | None = None


class XpCurvePointOut(BaseModel):
    level: int
    cumulative_xp: int


class XpCurveOut(BaseModel):
    items: list[XpCurvePointOut]


class StatProgressOut(BaseModel):
    stat_id: str
    name: str
    xp: int
    level: int
    label: str
    xp_to_next: int | None = None


class CharacterStateOut(BaseModel):
    progress: list[StatProgressOut]
    recent_events: list[XpEventOut]


# ---------------------------------------------------------------------------
# Contact (ADR-030)
# ---------------------------------------------------------------------------


class ContactCreateIn(BaseModel):
    """Body for POST /productivity/contacts.

    ``name`` is the only required field. Multi-value fields
    default to empty lists so a minimal contact can be created
    without specifying them. Wire-side names match the entity
    field names one-for-one; no translation is needed.
    """

    name: str
    given_name: str | None = None
    family_name: str | None = None
    additional_names: list[str] = []
    name_prefix: str | None = None
    name_suffix: str | None = None
    nickname: str | None = None
    organization: str | None = None
    title: str | None = None
    emails: list[str] = []
    phones: list[str] = []
    urls: list[str] = []
    addresses: list[str] = []
    note: str | None = None
    birthday: datetime.date | None = None


class ContactUpdateIn(BaseModel):
    """Body for PATCH /productivity/contacts/{uid}.

    All fields optional. Multi-value fields are replaced
    wholesale when supplied (no partial-patch semantics in
    v1). Scalar nullable fields use the same
    ``clear_<field>`` pattern as ``TaskUpdateIn`` so callers
    can distinguish "leave alone" from "set to None".
    """

    name: str | None = None
    given_name: str | None = None
    clear_given_name: bool = False
    family_name: str | None = None
    clear_family_name: bool = False
    additional_names: list[str] | None = None
    name_prefix: str | None = None
    clear_name_prefix: bool = False
    name_suffix: str | None = None
    clear_name_suffix: bool = False
    nickname: str | None = None
    clear_nickname: bool = False
    organization: str | None = None
    clear_organization: bool = False
    title: str | None = None
    clear_title: bool = False
    emails: list[str] | None = None
    phones: list[str] | None = None
    urls: list[str] | None = None
    addresses: list[str] | None = None
    note: str | None = None
    clear_note: bool = False
    birthday: datetime.date | None = None
    clear_birthday: bool = False


class ContactOut(BaseModel):
    """Wire model for a contact. Field names match the entity."""

    uid: str
    name: str
    given_name: str | None
    family_name: str | None
    additional_names: list[str]
    name_prefix: str | None
    name_suffix: str | None
    nickname: str | None
    organization: str | None
    title: str | None
    emails: list[str]
    phones: list[str]
    urls: list[str]
    addresses: list[str]
    note: str | None
    birthday: datetime.date | None
    created: UtcDateTime | None
    modified: UtcDateTime | None


class ContactListOut(BaseModel):
    items: list[ContactOut]
    total: int
    offset: int
    limit: int
