"""Game endpoints: character state, XP events, stats, curve (ADR-026).

Mirrors the productivity router pattern: per-request Facade built
from the JWT subject; entity -> wire translation; error mapping.

Endpoints:

- GET  /game/character              current stat progress + recent events
- GET  /game/character/xp-events    list XP events (paged, filterable)
- POST /game/character/xp-events    record an XP event
- GET  /game/stats                  list the user's stat config
- PATCH /game/stats/{id}            update a stat's display name
- GET  /game/curve                  return the XP curve table
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...configs import resolve_config_path
from ...core.facade import Facade
from ...entities import XpEventKind
from ...game.facade import GameClassificationError, GameDisabled
from ..auth import current_subject
from ..schemas import (
    CharacterStateOut,
    StatListOut,
    StatOut,
    StatProgressOut,
    StatUpdateIn,
    XpCurveOut,
    XpCurvePointOut,
    XpEventCreateIn,
    XpEventKindWire,
    XpEventListOut,
    XpEventOut,
)

router = APIRouter(prefix="/game", tags=["game"])


def _facade(subject: str) -> Facade:
    return Facade(resolve_config_path(subject))


def _xp_event_to_wire(event) -> XpEventOut:
    return XpEventOut(
        uid=event.uid,
        stat_id=event.stat_id,
        delta=event.delta,
        kind=XpEventKindWire(event.kind.value),
        source_task_id=event.source_task_id,
        source_session_id=event.source_session_id,
        reason=event.reason,
        occurred_at=event.occurred_at,
        idempotency_key=event.idempotency_key,
    )


def _paginate(items, offset, limit):
    total = len(items)
    effective_offset = offset if offset is not None else 0
    page = items[effective_offset : effective_offset + limit] if limit is not None else items[effective_offset:]
    return page, total, effective_offset, limit if limit is not None else (total - effective_offset)


# ---------------------------------------------------------------------------
# Character state
# ---------------------------------------------------------------------------


@router.get("/character", response_model=CharacterStateOut)
def get_character(
    subject: Annotated[str, Depends(current_subject)],
) -> CharacterStateOut:
    facade = _facade(subject)
    try:
        state = facade.game.get_character()
        return CharacterStateOut(
            progress=[
                StatProgressOut(
                    stat_id=p.stat_id,
                    name=p.name,
                    xp=p.xp,
                    level=p.level,
                    label=p.label,
                    xp_to_next=p.xp_to_next,
                )
                for p in state.progress
            ],
            recent_events=[_xp_event_to_wire(e) for e in state.recent_events],
        )
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# XP events
# ---------------------------------------------------------------------------


@router.get("/character/xp-events", response_model=XpEventListOut)
def list_xp_events(
    subject: Annotated[str, Depends(current_subject)],
    stat_id: Annotated[str | None, Query()] = None,
    kind: Annotated[XpEventKindWire | None, Query()] = None,
    source_task_id: Annotated[str | None, Query()] = None,
    source_session_id: Annotated[str | None, Query()] = None,
    offset: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query()] = 50,
) -> XpEventListOut:
    facade = _facade(subject)
    try:
        entity_kind = XpEventKind(kind.value) if kind is not None else None
        events = facade.game.list_xp_events(
            stat_id=stat_id,
            kind=entity_kind,
            source_task_id=source_task_id,
            source_session_id=source_session_id,
        )
        page, total, offset, limit = _paginate(list(events), offset, limit)
        return XpEventListOut(
            items=[_xp_event_to_wire(e) for e in page],
            total=total,
            offset=offset,
            limit=limit,
        )
    finally:
        facade.shutdown()


@router.post("/character/xp-events", response_model=XpEventOut, status_code=status.HTTP_201_CREATED)
def create_xp_event(
    body: XpEventCreateIn,
    subject: Annotated[str, Depends(current_subject)],
) -> XpEventOut:
    facade = _facade(subject)
    try:
        try:
            event = facade.record_xp_event(
                task_id=body.task_id,
                session_id=body.session_id,
                reason=body.reason,
                stat_id=body.stat_id,
                delta=body.delta,
                idempotency_key=body.idempotency_key,
                occurred_at=body.occurred_at,
            )
        except GameDisabled as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except GameClassificationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if event is None:
            raise HTTPException(status_code=404, detail="source entity not found")
        return _xp_event_to_wire(event)
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=StatListOut)
def list_stats(
    subject: Annotated[str, Depends(current_subject)],
) -> StatListOut:
    facade = _facade(subject)
    try:
        return StatListOut(
            items=[StatOut(id=s.id, name=s.name) for s in facade.game.list_stats()],
        )
    finally:
        facade.shutdown()


@router.patch("/stats/{stat_id}", response_model=StatOut)
def update_stat(
    stat_id: str,
    body: StatUpdateIn,
    subject: Annotated[str, Depends(current_subject)],
) -> StatOut:
    """Update a stat's display name.

    The ``stat_id`` is immutable (renaming an id is a breaking
    change to stored events). Only the display ``name`` changes,
    and the change lives in the user's ``configs.game.stats``
    ( ADR-026 ships YAML-only editing; this endpoint writes the
    new value back to ``configs.yaml``).
    """
    facade = _facade(subject)
    try:
        stats = facade.game.list_stats()
        for s in stats:
            if s.id == stat_id:
                # The game facade reads from the cached Configs
                # model. The simplest v1 behaviour is to require
                # the user to edit configs.yaml directly; this
                # endpoint is a TODO and returns 501. Tracked in
                # ADR-026 as "future work".
                raise HTTPException(
                    status_code=501,
                    detail="stat name editing over HTTP not yet implemented; edit configs.yaml",
                )
        raise HTTPException(status_code=404, detail=f"unknown stat_id: {stat_id}")
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# XP curve
# ---------------------------------------------------------------------------


@router.get("/curve", response_model=XpCurveOut)
def get_curve(
    subject: Annotated[str, Depends(current_subject)],
) -> XpCurveOut:
    facade = _facade(subject)
    try:
        return XpCurveOut(
            items=[
                XpCurvePointOut(level=level, cumulative_xp=cumulative) for level, cumulative in facade.game.get_curve()
            ],
        )
    finally:
        facade.shutdown()
