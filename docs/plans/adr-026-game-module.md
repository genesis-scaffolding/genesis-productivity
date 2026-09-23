# Plan for adr-026

## Steps

1. **Confirm `nltk` dependency with developer.** ADR-026 requires
   `nltk` for `PorterStemmer`. Per AGENTS, this needs explicit
   approval before adding the package. If approved, `uv add nltk`.
   If not, vendor PorterStemmer or fall back to regex word
   boundaries + curated variants (Tier 1 from the design
   discussion).

2. Add `XP_EVENTS_COLLECTION = "xp_events"` to
   `src/genesis/caldav/constants.py`. Fifth collection;
   ADR-012 amendment is acknowledged in this ADR.

3. Create `src/genesis/entities/xp_event.py` with:
   - `XpEventKind(StrEnum)` — `GAIN | "gain"`, `LOSS | "loss"`
   - `XpEvent(BaseModel)` — full event (uid, stat_id, delta, kind,
     source_task_id, source_session_id, reason, occurred_at,
     created, modified, idempotency_key)
   - `XpEventCreate(BaseModel)` — input model for create
   - Field validators for tz-aware UTC datetimes (matches the
   `time_log` entity pattern at
   `src/genesis/entities/time_log.py`).

4. Add `xp_event_to_ical(component)` / `xp_event_from_ical(...)`
   in `src/genesis/caldav/ical.py`. VEVENT-based with custom X-
   properties (`X-STAT-ID`, `X-DELTA`, `X-KIND`,
   `X-SOURCE-TASK-ID`, `X-SOURCE-SESSION-ID`, `X-REASON`,
   `X-IDEMPOTENCY-KEY`). Reuse `to_str`, `to_int`, `to_datetime`
   helpers already in the module. Add `new_uid("x")` callsite.

5. Create `src/genesis/caldav/xp_events.py` mirroring
   `caldav/time_log.py`:
   - `create_xp_event(facade, data: XpEventCreate) -> XpEvent`
   - `get_xp_event(facade, uid: str) -> XpEvent | None`
   - `list_xp_events(facade, stat_id=None, kind=None,
     source_task_id=None, source_session_id=None,
     idempotency_key=None, since=None, until=None,
     sort_by=None, sort_order="desc") -> list[XpEvent]`
   - `delete_xp_event(facade, uid: str) -> bool`
   - Server-side filters: `X-STAT-ID`, `X-KIND`,
     `X-SOURCE-TASK-ID`, `X-SOURCE-SESSION-ID`,
     `X-IDEMPOTENCY-KEY` (exact match), `DTSTART` range.

6. Wire four delegations in
   `src/genesis/caldav/facade.py`:
   - `XP_EVENTS_COLLECTION: str = XP_EVENTS_COLLECTION`
   - `create_xp_event(self, data)`, `get_xp_event(self, uid)`,
     `list_xp_events(self, **kwargs)`, `delete_xp_event(self, uid)`.

7. Add `GameConfig` / `StatConfig` to `src/genesis/configs.py`:
   - `StatConfig(BaseModel)` with `id`, `name`
   - `GameConfig(BaseModel)` with `enabled: bool = True` and
     `stats: list[StatConfig]` defaulting to the 5 design-note stats
   - `Configs.game: GameConfig = Field(default_factory=GameConfig)`
   - `_build_game(raw: dict) -> dict` helper that merges YAML
     overrides with defaults, called from `Configs.__init__`
   - Env-var override works automatically through `BaseSettings`
     (`GENESIS__GAME__ENABLED`, `GENESIS__GAME__STATS__0__ID`, etc.)

8. Create `src/genesis/game/labels.py` with the per-stat level
   label ladders (10 each, design-note values). Constants only,
   no logic. Pure-Python `dict[str, list[str]]`.

9. Create `src/genesis/game/xp.py` with:
   - `XP_CURVE: list[tuple[int, int]]` — the locked table
     (level_threshold -> cumulative_xp)
   - `xp_for_level(level: int) -> int` and `level_for_xp(xp: int)
     -> int` pure functions
   - `TASK_COMPLETION_XP = 5` constant
   - `SESSION_XP_DIVISOR = 25` (gain), `PLAY_LOSS_DIVISOR = 120`
   - `delta_from_session_duration(duration_minutes: int, category:
     TimeLogCategory) -> int` — handles PLAY's negative sign
   - `stat_from_session_category(category: TimeLogCategory) ->
     str | None` — returns the tier-1 mapping (WORK, WORKOUT,
     PLAY); None for PERSONAL / OTHER (caller falls through to
     keyword inference)

10. Create `src/genesis/game/classify.py` with:
    - `_STAT_KEYWORDS: dict[str, list[str]]` — the five curated
      lists from the ADR
    - `infer_stat_from_text(text: str, default_stat_id: str | None
      = None) -> str` — PorterStemmer-based scoring, returns the
      winning stat id (or `default_stat_id` if no hits)
    - `tokenize(text: str) -> list[str]` — `\b[a-z]+\b` regex on
      lowercase
    - Optional `_stem_cache` so we don't re-stem the same token
      twice within a call

11. Create `src/genesis/game/source.py`:
    - `@dataclass(frozen=True) class TaskSource: task_uid: str;
      title: str; description: str | None`
    - `@dataclass(frozen=True) class SessionSource: session_uid:
      str; label: str; category: TimeLogCategory`
    - `@dataclass(frozen=True) class ReasonSource: reason: str`
    - `XpSource = TaskSource | SessionSource | ReasonSource`

12. Create `src/genesis/game/config.py` with `load_game_config()`
    that returns the user's `GameConfig` from
    `Configs.game`. Thin pass-through; the lazy property on
    `Facade` calls it.

13. Create `src/genesis/game/facade.py` with `GameFacade`:
    - Constructor takes `caldav: CalDAVFacade` and
      `config: GameConfig`
    - `record_xp_event(*, source, stat_id, delta,
      idempotency_key, occurred_at) -> XpEvent`
      - Idempotency check first: search by `X-IDEMPOTENCY-KEY`,
        return existing if found
      - Build context text from source
      - For missing stat_id / delta, dispatch on source type:
        SessionSource → tier-1 by category, then tier-2 keyword;
        TaskSource → tier-2 keyword with discipline fallback;
        ReasonSource → must already have both (caller validated)
      - Sign convention: PLAY uses negative delta; the formula in
        `xp.delta_from_session_duration` handles this
      - Call `self._caldav.create_xp_event(...)` and return the
        persisted event
    - `list_xp_events(**kwargs)` — pass-through to caldav
    - `get_character() -> CharacterState` — fetch all events,
      group by stat_id, sum deltas, compute level/label from
      `XP_CURVE` and `LABELS`
    - `list_stats() -> list[StatConfig]` — pass-through to config
    - `get_curve() -> list[tuple[int, int]]` — pass-through to xp
      module

14. Wire `game` lazy property + orchestration in
    `src/genesis/core/facade.py`:
    - `@property def game(self) -> GameFacade`
    - Lazy-construct on first access; takes `self.caldav` and
      `self.configs.game`
    - Add `record_xp_event(*, task_id, session_id, reason,
      stat_id, delta, idempotency_key, occurred_at)` orchestration
      method on `Facade`:
      - Exactly one of `task_id`, `session_id`, `reason` (else
        `ValueError`)
      - If `task_id`: `task = self.caldav.get_task(task_id)`;
        build `TaskSource(task.uid, task.title, task.description)`;
        auto-derive `idempotency_key = f"task:{task.uid}:completed"`
        when caller did not supply
      - If `session_id`: `session = self.caldav.get_time_log_entry(
        session_id)`; build `SessionSource(...)`; auto-derive key
        `f"session:{session.uid}:stopped"`
      - If `reason`: validate both `stat_id` and `delta` are
        provided; build `ReasonSource(reason)`; require explicit
        `idempotency_key`
      - Delegate to `self.game.record_xp_event(...)`
    - Add `complete_task(uid: str) -> Task | None` convenience
      wrapper on `Facade`:
      - Calls `self.caldav.update_task(uid, TaskUpdate(status=
        COMPLETED))` then `_fire_xp_safely(self.record_xp_event,
        task_id=uid, occurred_at=now_utc())`
      - Returns the updated task (or None if not found)
    - Add `stop_session(uid: str, *, actual_due, completed_at,
      category=None) -> TimeLogEntry | None`:
      - Calls `self.caldav.stop_time_log_entry(uid, ...)` then
        `_fire_xp_safely(self.record_xp_event, session_id=uid,
        occurred_at=completed_at or now_utc())`
      - Returns the updated time log entry (or None if not found)
    - Add private `_fire_xp_safely(fn, **kwargs) -> None` helper:
      try/except wrapper that logs and swallows exceptions.

15. Add wire schemas to `src/genesis/api/schemas.py`:
    - `XpEventOut`, `XpEventCreateIn`, `XpEventListOut`,
      `XpEventKind`, `StatOut`, `StatListOut`,
      `XpCurvePointOut`, `XpCurveOut`, `CharacterStateOut`,
      `StatProgressOut`
    - Datetime fields use existing `UtcDateTime` annotation.

16. Create `src/genesis/api/routers/game.py` mirroring
    `api/routers/productivity.py`:
    - `GET /game/character` — current XP totals + levels + labels
      per stat
    - `GET /game/character/xp-events` — list events (paged,
      filterable)
    - `POST /game/character/xp-events` — record an XP event
      (delegates to `facade.record_xp_event`)
    - `GET /game/stats` — list the user's stat config
    - `PATCH /game/stats/{id}` — update a stat's display name (id
      immutable)
    - `GET /game/curve` — return the XP curve
    - Per-request `_facade(subject)`, `try/finally facade.shutdown()`
    - Map facade results to HTTP errors (404 source not found, 400
      classification error, 400 reason without stat/delta).

17. Register the game router in
    `src/genesis/api/app.create_app`. One line:
    `app.include_router(game_router)`.

18. **Streamlit UI integration**:
    - In `src/genesis/ui/_render.py`, `focus_completion_dialog`
      "Yes" branch: replace the existing
      `facade.caldav.update_task(task_uid, TaskUpdate(status=
      COMPLETED))` with `facade.complete_task(task_uid)`.
    - In `src/genesis/ui/pages/focus.py`, the auto-stop path in
      `_render_active_sessions`: replace the existing
      `caldav.stop_time_log_entry(s.uid, actual_due=s.due,
      completed_at=now, category=category)` with
      `facade.stop_session(s.uid, actual_due=s.due,
      completed_at=now, category=category)`.
    - In `src/genesis/ui/pages/focus.py`, the manual Stop button
      in `_render_session_card`: same replacement.
    - In `src/genesis/ui/_table.py`, `_handle_inline_edit`: after
      the existing `caldav.update_task(original.uid, update)`,
      detect whether the new status is COMPLETED (already known
      from the `changes` dict). If yes, call
      `_state.get_facade().record_xp_event(task_id=original.uid,
      occurred_at=now_utc())` via `_fire_xp_safely`. No extra
      `get_task` round trip needed.

19. **API integration** (consistency across surfaces):
    - In `src/genesis/api/routers/productivity.py`,
      `update_task` handler: after the existing
      `facade.caldav.update_task(uid, TaskUpdate(**kwargs))`,
      detect whether the request body set `status` to COMPLETED
      (already known from `body.status`). If yes, fire XP via
      the Facade's `_fire_xp_safely`. Same idempotency, same
      idempotency key (`task:{uid}:completed`).

20. **New "Character" page in UI**:
    - `src/genesis/ui/pages/character.py`: five stat cards
      (level, label, XP-to-next progress bar) + level history
      table. Read-only this round; no edit surfaces.
    - In `src/genesis/ui/app.py`, register the Character page in
      the "User" group **conditionally**: only include it when
      `facade.configs.game.enabled` is True. Build the nav dict
      after constructing the facade (not at module top level, so
      a logged-out user doesn't see the gate decision).
    - Show an empty-state info when `game.enabled` is False: page
      says "Game layer is off. Set `game.enabled: true` in
      `configs.yaml` to enable."

21. Add `tests/test_game_unit.py` (fast, mock-backed):
    - `xp.py` — curve monotonic, `level_for_xp` round-trips
    - `classify.py` — known text → known stat, ties resolve by
      config order, discipline fallback for tasks
    - `source.py` — frozen dataclasses, equality
    - `facade.py` (with a `FakeCalDAVFacade` and a tiny
      `FakeConfigs`) — idempotency key dedup, tier-1 dispatch,
      reason-must-be-explicit raises
    - `core/facade.record_xp_event` orchestration — fetches
      entity, builds source, delegates
    - `core/facade.complete_task` and `core/facade.stop_session`
      call the underlying caldav methods and fire XP via
      `_fire_xp_safely`; XP failure does not propagate

22. Add `tests/test_game_xp_events.py` (integration-marked,
    `@pytest.mark.integration`):
    - Round-trip XP event create → list → get → delete against a
      real Radicale collection
    - Filter by `X-STAT-ID` and `X-IDEMPOTENCY-KEY` works
    - Range filter on `DTSTART` works
    - Per-user isolation: alice's events don't appear in bob's list

23. Run `make format`, `make lint`, `make typecheck`,
    `make test`.

## File map

New files:

- `src/genesis/game/__init__.py`
- `src/genesis/game/source.py`
- `src/genesis/game/classify.py`
- `src/genesis/game/xp.py`
- `src/genesis/game/labels.py`
- `src/genesis/game/config.py`
- `src/genesis/game/facade.py`
- `src/genesis/entities/xp_event.py`
- `src/genesis/caldav/xp_events.py`
- `src/genesis/api/routers/game.py`
- `src/genesis/ui/pages/character.py`
- `tests/test_game_unit.py`
- `tests/test_game_xp_events.py`
- `docs/adr/adr-026-game-module.md`
- `docs/plans/adr-026-game-module.md`

Modified files:

- `src/genesis/caldav/constants.py`
- `src/genesis/caldav/ical.py`
- `src/genesis/caldav/facade.py`
- `src/genesis/configs.py`
- `src/genesis/core/facade.py`
- `src/genesis/api/schemas.py`
- `src/genesis/api/app.py`
- `src/genesis/api/routers/productivity.py` (`PATCH /tasks/{uid}` fires XP on completion)
- `src/genesis/ui/app.py` (Character page in User group, gated on `game.enabled`)
- `src/genesis/ui/_render.py` (`focus_completion_dialog` uses `facade.complete_task`)
- `src/genesis/ui/pages/focus.py` (auto-stop + manual Stop use `facade.stop_session`)
- `src/genesis/ui/_table.py` (`_handle_inline_edit` detects status→COMPLETED, fires XP)
- `pyproject.toml` (`nltk` — developer approved)