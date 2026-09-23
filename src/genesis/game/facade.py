"""Game facade: orchestrator for the life-gamification layer.

ADR-026. Owns the xp_events CalDAV collection. Reads events to
compute levels and labels. Never reads tasks or sessions — those
are passed in via :mod:`genesis.game.source`.

Public surface:

- ``record_xp_event`` — infer missing stat/delta from the source,
  write the event. Idempotent on ``idempotency_key``.
- ``list_xp_events`` — pass-through to caldav.
- ``get_character`` — fetch all events, group by stat, sum deltas,
  compute level/label.
- ``list_stats`` — the user's stat config.
- ``get_curve`` — the XP curve table.
- ``is_enabled`` — the config toggle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..configs import GameConfig, StatConfig
from ..entities import XpEvent, XpEventCreate, XpEventKind
from . import classify, labels, xp
from .source import SessionSource, TaskSource, XpSource

if TYPE_CHECKING:
    from ..caldav.facade import CalDAVFacade


class GameDisabled(Exception):
    """Raised when the game layer is off (``configs.game.enabled = False``).

    Callers that wrap ``record_xp_event`` in best-effort try/except
    (e.g. ``Facade.complete_task``) should swallow this silently.
    It is not an error — it is a deliberate user choice.
    """


class GameClassificationError(Exception):
    """Raised when the keyword engine cannot pin a stat.

    Used for PERSONAL/OTHER sessions where guessing is worse than
    failing. Tasks default to discipline on miss instead.
    """


class GameFacade:
    """Orchestrator for the xp_events collection and stat math.

    The facade is constructed once per user (lazily, on
    ``Facade.game`` first access). It is not thread-safe; Streamlit
    runs single-threaded per session, and the FastAPI app builds
    a fresh facade per request.
    """

    def __init__(self, caldav: CalDAVFacade, config: GameConfig) -> None:
        self._caldav = caldav
        self._config = config

    @property
    def config(self) -> GameConfig:
        return self._config

    def is_enabled(self) -> bool:
        return self._config.enabled

    # ---------------------------------------------------------------------
    # Stats config + curve passthrough
    # ---------------------------------------------------------------------

    def list_stats(self) -> list[StatConfig]:
        return list(self._config.stats)

    def get_curve(self) -> list[tuple[int, int]]:
        """Return ``[(level, cumulative_xp_to_reach), ...]``."""
        return list(enumerate(xp.XP_CURVE, start=1))

    # ---------------------------------------------------------------------
    # XP event CRUD
    # ---------------------------------------------------------------------

    def record_xp_event(
        self,
        *,
        source: XpSource,
        stat_id: str | None = None,
        delta: int | None = None,
        idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> XpEvent | None:
        """Record an XP event, inferring any missing fields from ``source``.

        Returns the persisted event, or ``None`` if the user has
        the game layer disabled.

        Idempotency: when ``idempotency_key`` is provided and an
        existing event has the same key, that event is returned
        unchanged. Caller should always supply a key — main
        ``Facade`` derives one when the source is a task or session.

        Raises:
            GameDisabled: when ``game.enabled`` is False.
            ValueError: when ``stat_id`` or ``delta`` is invalid.
            GameClassificationError: when ``stat_id`` cannot be
                inferred (PERSONAL/OTHER session with no keyword
                hits).
        """
        if not self._config.enabled:
            raise GameDisabled("game layer is disabled in configs.game.enabled")

        if idempotency_key is not None:
            existing = self._caldav.list_xp_events(idempotency_key=idempotency_key)
            if existing:
                return existing[0]

        resolved_stat_id, resolved_delta = self._resolve(source, stat_id, delta)
        if occurred_at is None:
            occurred_at = datetime.now(UTC)

        kind = XpEventKind.GAIN if resolved_delta >= 0 else XpEventKind.LOSS
        source_task_id = source.task_uid if isinstance(source, TaskSource) else None
        source_session_id = source.session_uid if isinstance(source, SessionSource) else None

        create_model = XpEventCreate(
            stat_id=resolved_stat_id,
            delta=resolved_delta,
            kind=kind,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key or "",
            source_task_id=source_task_id,
            source_session_id=source_session_id,
            reason=self._reason_text(source),
        )
        return self._caldav.create_xp_event(create_model)

    def list_xp_events(self, **kwargs) -> list[XpEvent]:
        return self._caldav.list_xp_events(**kwargs)

    # ---------------------------------------------------------------------
    # Character view
    # ---------------------------------------------------------------------

    def get_character(self) -> CharacterState:
        """Compute the user's current character state from all stored events.

        Iterates every XP event (paginated client-side), groups by
        ``stat_id``, sums the signed deltas, computes level/label
        from the XP curve.
        """
        all_events = self._caldav.list_xp_events()
        by_stat: dict[str, int] = {s.id: 0 for s in self._config.stats}
        recent: list[XpEvent] = []
        for event in all_events:
            if event.stat_id in by_stat:
                by_stat[event.stat_id] += event.delta
        # "Recent" = the last 20 events, newest first.
        recent = sorted(all_events, key=lambda e: (e.occurred_at, e.uid), reverse=True)[:20]

        progress = [
            StatProgress(
                stat_id=s.id,
                name=s.name,
                xp=by_stat[s.id],
                level=xp.level_for_xp(by_stat[s.id]),
                label=labels.label_for_level(s.id, xp.level_for_xp(by_stat[s.id])),
                xp_to_next=xp.xp_to_next_level(xp.level_for_xp(by_stat[s.id]), by_stat[s.id]),
            )
            for s in self._config.stats
        ]
        return CharacterState(progress=progress, recent_events=recent)

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    def _resolve(self, source: XpSource, stat_id: str | None, delta: int | None) -> tuple[str, int]:
        """Fill in any missing ``stat_id`` / ``delta`` from the source.

        Returns ``(stat_id, delta)``. Raises ``GameClassificationError``
        when no stat can be inferred (PERSONAL/OTHER session with
        no keyword hits).
        """
        if stat_id is not None and delta is not None:
            return stat_id, delta

        if isinstance(source, SessionSource):
            text = source.label
            tier1_stat = xp.stat_from_session_category(source.category)
            if stat_id is None:
                if tier1_stat is not None:
                    stat_id = tier1_stat
                else:
                    candidate = classify.infer_stat_from_text(text, config=self._config, fail_on_no_match=True)
                    if candidate is None:
                        raise GameClassificationError(f"cannot classify stat from session label {text!r}")
                    stat_id = candidate
            if delta is None:
                duration = self._session_duration(source)
                delta = xp.delta_from_session_duration(duration, source.category)
            return stat_id, delta

        if isinstance(source, TaskSource):
            text = " ".join(filter(None, [source.title, source.description or ""]))
            if stat_id is None:
                candidate = classify.infer_stat_from_text(text, config=self._config, default_stat_id="discipline")
                if candidate is None:
                    stat_id = "discipline"
                else:
                    stat_id = candidate
            if delta is None:
                delta = xp.TASK_COMPLETION_XP
            return stat_id, delta

        # ReasonSource — caller is required to supply both.
        if stat_id is None or delta is None:
            raise ValueError(
                "ReasonSource requires explicit stat_id and delta; the game facade does not infer ad-hoc XP"
            )
        return stat_id, delta

    def _reason_text(self, source: XpSource) -> str:
        if isinstance(source, TaskSource):
            return f"task: {source.title}"
        if isinstance(source, SessionSource):
            return f"session ({source.category.value}): {source.label}"
        return source.reason

    def _session_duration(self, source: SessionSource) -> int:
        """Return the session duration in minutes.

        Falls back to 0 if the session cannot be fetched (the game
        facade does not read sessions; this is a deliberate
        convenience that uses the caldav read path the game
        facade already has via the xp_events collection).

        NOTE: in practice, the main Facade passes ``delta``
        explicitly when it has the duration; this method exists
        for the rare case where the caller only knows the session
        uid and asks the game facade to figure it out.
        """
        try:
            entry = self._caldav.get_time_log_entry(source.session_uid)
        except Exception:
            return 0
        if entry is None:
            return 0
        if entry.completed_at is not None and entry.start is not None:
            delta = entry.completed_at - entry.start
            return max(0, int(delta.total_seconds() // 60))
        return 0


# ---------------------------------------------------------------------
# Result dataclasses (plain dataclasses; no facade ABC)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class StatProgress:
    """Current per-stat state for one stat."""

    stat_id: str
    name: str
    xp: int
    level: int
    label: str
    xp_to_next: int | None  # None when at MAX_LEVEL


@dataclass(frozen=True)
class CharacterState:
    """Top-level character state returned by ``GameFacade.get_character``."""

    progress: list[StatProgress]
    recent_events: list[XpEvent] = field(default_factory=list)


__all__ = [
    "CharacterState",
    "GameClassificationError",
    "GameDisabled",
    "GameFacade",
    "StatProgress",
]
