"""XP event entity model.

An XP event is a VEVENT stored in the xp_events collection. Each
event records a single gain or loss of XP for a single stat. They
are immutable facts ("at T, +/-N XP happened for stat S"), with no
lifecycle, no status transitions. See ADR-026.

All datetime fields hold tz-aware UTC datetimes per the project
convention documented in ``genesis.tz``. Naive input is coerced to
UTC at construction.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, field_validator

from ..tz import to_utc


class XpEventKind(StrEnum):
    """Whether the event is a gain or a loss.

    The sign of ``delta`` carries the same information. Kept as a
    separate field for fast server-side filtering (X-KIND exact
    match in CalDAV search) without parsing the integer.
    """

    GAIN = "gain"
    LOSS = "loss"


class XpEvent(BaseModel):
    """An XP event (gain or loss) for one stat.

    Attributes:
        uid: Unique identifier.
        stat_id: Stat id from ``GameConfig.stats``.
        delta: Signed integer. Positive for gains, negative for losses.
            Magnitude is the absolute XP awarded.
        kind: gain or loss. Mirrors the sign of ``delta``; kept for
            fast filtering.
        source_task_id: FK to Task.uid when sourced from a task
            completion, else None.
        source_session_id: FK to TimeLogEntry.uid when sourced from a
            session stop, else None.
        reason: Short human string ("task completed", "missed deadline",
            "40-minute work session"). Indexed for display but not for
            server-side filter.
        occurred_at: When the event happened, tz-aware UTC.
        idempotency_key: Caller-supplied dedup key. Unique per logical
            event so retries don't double-fire. Indexed for server-side
            filter.
        created: When the event was persisted.
        modified: When the event was last modified (always equal to
            ``created`` since events are immutable in v1).
    """

    uid: str
    stat_id: str
    delta: int
    kind: XpEventKind
    source_task_id: str | None = None
    source_session_id: str | None = None
    reason: str
    occurred_at: datetime
    idempotency_key: str
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {"extra": "ignore"}

    @field_validator("occurred_at", "created", "modified", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_kind(cls, v: object) -> object:
        if isinstance(v, XpEventKind):
            return v
        if isinstance(v, str):
            return XpEventKind(v)
        return v


class XpEventCreate(BaseModel):
    """Data for creating a new XP event.

    ``uid``, ``created``, and ``modified`` are assigned at creation
    time by the storage layer. Everything else is supplied by the
    caller.
    """

    stat_id: str
    delta: int
    kind: XpEventKind
    occurred_at: datetime
    idempotency_key: str
    source_task_id: str | None = None
    source_session_id: str | None = None
    reason: str

    model_config = {"extra": "ignore"}

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_kind(cls, v: object) -> object:
        if isinstance(v, XpEventKind):
            return v
        if isinstance(v, str):
            return XpEventKind(v)
        return v


__all__ = ["XpEvent", "XpEventCreate", "XpEventKind"]
