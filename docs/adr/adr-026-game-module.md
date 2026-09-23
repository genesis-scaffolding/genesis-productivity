# 026. Life gamification module

## Context

The Real-Life-Persona-System design note
(`~/Documents/Commonplace-Knowledgebase/Notebook/Real-Life-Persona-System.md`,
2026-09-02) describes a layer on top of the user's daily work that
turns tasks and focus sessions into XP events, accumulates per-stat XP
across five character stats (Courage, Discipline, Charisma,
Knowledge, Vitality), and surfaces levels and labels. The design
borrows from Persona (stats grow through activities, time-blocked
days, visible consequences), Solo Leveling (DM assigns objectives,
player executes, level-up feedback), and an F1 race engineer
(telemetry-driven, minimal-noise feedback).

We want this layer inside genesis. Two reasons:

1. The data is already here — tasks, pomodoro sessions, journals.
   Storing the XP layer elsewhere means duplicated access and
   eventual consistency on a system where XP must be tight with
   task completions.
2. The user wants future users of genesis to be able to opt out of
   the layer entirely. That implies a per-user toggle and per-user
   stat set, not a global feature flag. Co-locating config and
   storage with the rest of the user's data keeps that clean.

## Module

```
src/genesis/game/
  __init__.py     re-export GameFacade, XpEvent, XpSource variants, GameConfig
  source.py       XpSource tagged union: TaskSource, SessionSource, ReasonSource
  classify.py     stem-based keyword inference (NLTK Porter) with curated keyword maps
  xp.py           XP curve constants + per-event formulas (task flat, session tiered, PLAY penalty)
  labels.py       level labels per stat (10 per stat), hardcoded constants
  config.py       load GameConfig from genesis.configs
  facade.py       GameFacade: orchestration + persistence via genesis.caldav
```

Wired on `Facade` as `facade.game`, parallel to `facade.caldav`,
`facade.journal`, `facade.profile`. Same lazy-init pattern.

## Storage

A fifth CalDAV collection `xp_events` lives next to the existing
four (projects, tasks, events, time_log). Each XP event is a
VEVENT — they are immutable facts ("at T, +N XP happened for stat
S"), with no lifecycle, no status transitions. VTODO is wrong
because there is no ACTIVE-vs-COMPLETED to model.

Custom iCal fields per event:

- `X-STAT-ID` — stat id from `GameConfig.stats`
- `X-DELTA` — integer, signed (positive = gain, negative = loss)
- `X-KIND` — "gain" | "loss" (mirrors the sign but kept for fast filtering)
- `X-SOURCE-TASK-ID` — FK to Task.uid when sourced from a task
- `X-SOURCE-SESSION-ID` — FK to TimeLogEntry.uid when sourced from a session
- `X-REASON` — short human string ("task completed", "missed deadline")
- `X-IDEMPOTENCY-KEY` — caller-supplied dedup key, indexed via search

XP events are per-user (one user's events live in that user's
principal under CalDAV), matching ADR-012.

This is the first amendment to ADR-012 since it landed: that ADR
said "four fixed top-level collections"; this one adds a fifth.
Future amendments continue to grow that count.

## Wiring

The game facade does not read tasks or sessions. It writes its own
`xp_events` collection and computes level/XP from those events
plus the keyword engine. Tasks and sessions stay clean — no
`X-CLASSIFIED-STAT` field on the task VTODO. The caller (main
`Facade`) is the only thing that touches both sub-facades.

```
        ┌────────────┐
        │  caller    │   (UI button, agent tool call)
        └─────┬──────┘
              │ record_xp_event(task_id=..., session_id=..., reason=..., ...)
              ▼
        ┌────────────┐
        │  Facade    │   (orchestration: fetch from caldav, build XpSource)
        └─────┬──────┘
              │ game.record_xp_event(source=TaskSource(...), stat_id=..., delta=..., idempotency_key=...)
              ▼
        ┌────────────┐
        │ GameFacade │   (infer missing fields, write xp_events via caldav)
        └─────┬──────┘
              │ caldav.create_xp_event(...)
              ▼
        ┌────────────┐
        │ CalDAVFacade│   (existing, unchanged)
        └────────────┘
```

The caller never has to know that XP events are stored in CalDAV.
The game module owns its storage end to end. Callers invoke
`facade.record_xp_event(...)` and get back an `XpEvent` (or
`None` if the source entity could not be found).

Two-step at the call site, no peer-extension hooks: the UI's
existing `focus_completion_dialog` "Yes" branch gains one
additional `facade.record_xp_event(...)` call after the existing
`facade.caldav.update_task(...)`. Same for the pomodoro stop
path. Both are best-effort — XP failure does not roll back a
completed task.

## XP math

Locked constants in `game/xp.py`:

**Task completion:** flat `+5 XP`, keyword-inferred stat. Caller
can override via `delta=` on the Facade call.

**Session reward:** `floor(duration_minutes / 25)` XP to a
category-default stat. Per category:

| Category | Stat | Formula |
|---|---|---|
| WORK | discipline | `floor(duration / 25)` gain |
| WORKOUT | vitality | `floor(duration / 25)` gain |
| PERSONAL | keyword-inferred from label | `floor(duration / 25)` gain |
| OTHER | keyword-inferred from label | `floor(duration / 25)` gain |
| PLAY | discipline | `floor(duration / 120)` loss |

PLAY is a penalty, not a reward — different divisor (120 min =
2 h) and negative sign. Examples: 1h59 play = 0 loss, 2h00 play
= -1 discipline, 4h00 play = -2 discipline.

**Reason (ad-hoc):** caller must provide both `stat_id` and
`delta`. No inference on ad-hoc. Throws `ValueError` if either
is missing. Used by the agent for retroactive catch-up, manual
XP rewards, and missed-deadline deductions.

**Mixed mode:** explicit fields win for whatever is given; only
the missing fields are inferred.

## XP curve

Hardcoded table in `game/xp.py`. Per-level XP required to reach
the next level:

| Level | XP to next | Cumulative |
|-------|-----------:|-----------:|
| 1 → 2 | 30 | 30 |
| 2 → 3 | 50 | 80 |
| 3 → 4 | 70 | 150 |
| 4 → 5 | 100 | 250 |
| 5 → 6 | 140 | 390 |
| 6 → 7 | 190 | 580 |
| 7 → 8 | 250 | 830 |
| 8 → 9 | 320 | 1150 |
| 9 → 10 | 400 | 1550 |

Curve is identical across all five stats. We do not differentiate
per stat; if we want to later, it's one constant table per stat.

## Inference engine

Tier-1 dispatch on session category handles WORK, WORKOUT, PLAY
unambiguously (no inference). Tier-2 keyword matching handles
PERSONAL and OTHER sessions, and task title/description
classification. Task default on miss is `discipline`. Session
PERSONAL/OTHER on miss raises `GameClassificationError` (built-in
exception, raised from the game facade).

Keyword matching uses `nltk.stem.PorterStemmer` against
case-folded, alphanumeric-tokenised text. Porter handles common
inflections (meet/meeting/met, run/running/ran, read/reading)
without listing variants in the keyword map. The stemmer is pure
Python, no model download required, no `nltk.download()` call.

Curated keyword maps per stat live in `game/classify.py`. Five
lists, ~15-30 entries each. Initial set:

- courage — difficult conversation, hard conversation, hard email,
  cold call, cold email, scary, confront, push back, decline,
  reject, apologize, admit, first time, before ready, ask out,
  brave
- discipline — routine, habit, deadline, follow up, ship, finish,
  weekly, monthly, plan, admin, paperwork, report, schedule,
  calendar, review, audit, tidy, organize, check in, follow through
- charisma — call, meet, conversation, family, social, date,
  dinner, lunch, coffee, chat, talk, present, attend, host,
  friend, partner, mom, dad, birthday, anniversary, catch up,
  deep talk, quality time
- knowledge — read, study, learn, research, course, book, paper,
  write, draft, chapter, blog, notes, tutorial, practice, spec,
  design, analysis
- vitality — gym, workout, lift, run, walk, sleep, nap, eat, meal,
  cook, food, nature, hike, swim, cycle, yoga, meditate, doctor,
  appointment, recovery, rest, sauna, stretch, water, push day,
  pull day

Gen-specific terms (e.g. "rs8ii", "alisa") are intentionally
excluded. Keyword lists are generalisable across users; per-user
tuning happens via `GameConfig.stats`.

Scoring: stem each token in the source text, stem each keyword,
count matches per stat, highest count wins. Ties go to the first
stat in `GameConfig.stats` order.

Future work: the same stemmer-based engine can replace the
keyword logic in `genesis.category.inference` (which currently
classifies session type). Out of scope here; a separate ADR
when we get to it.

## Idempotency

Every XP event has an `X-IDEMPOTENCY-KEY`. Caller supplies it
explicitly, or the main `Facade` auto-derives it from the source:

- task completion → `task:{uid}:completed`
- session stop → `session:{uid}:stopped`
- reason (ad-hoc) → caller-supplied (required)

`GameFacade.record_xp_event` checks for an existing event with
the same key before computing or persisting. On match, returns
the existing event unchanged. No second XP, no level bump, no
duplicate row.

## Config

Added to `genesis.configs`:

```python
class StatConfig(BaseModel):
    id: str
    name: str

class GameConfig(BaseModel):
    enabled: bool = True
    stats: list[StatConfig] = [
        {"id": "courage", "name": "Courage"},
        {"id": "discipline", "name": "Discipline"},
        {"id": "charisma", "name": "Charisma"},
        {"id": "knowledge", "name": "Knowledge"},
        {"id": "vitality", "name": "Vitality"},
    ]

class Configs(BaseSettings):
    ...
    game: GameConfig = Field(default_factory=GameConfig)
```

Editable by hand in `configs.yaml` per user. No HTTP edit surface
this round. Per-user because `configs.yaml` is per-user. Env-var
override works for free: `GENESIS__GAME__ENABLED=false`.

What is NOT configurable per user:

- Level labels per stat (hardcoded constants in `game/labels.py`).
  Changing them = a code release + a migration.
- XP curve (hardcoded table in `game/xp.py`). Same reasoning.
- Per-task XP magnitude tiers (the design note's minor/normal/hard/stretch).
  Task XP is fixed at 5; the agent can override per-call if needed.
- Per-category session XP formulas. Same as above — these are math
  tied to the design, not preferences.

This deliberately keeps the user-editable surface small. Most
users will leave defaults alone; if they want to tune, they can
add/remove/rename stats and toggle the layer on or off. Anything
more is a code change.

## API

Mounted on the existing FastAPI app under `/game`, JWT-protected
like the rest of `/productivity`. Endpoints:

| Method | Path                                  | Purpose                                    |
|--------|---------------------------------------|--------------------------------------------|
| GET    | `/game/character`                     | Current XP totals + levels + labels per stat |
| GET    | `/game/character/xp-events`           | List XP events (paged, filterable by stat / source) |
| POST   | `/game/character/xp-events`           | Record an XP event (delegates to `facade.record_xp_event`) |
| GET    | `/game/stats`                         | List the user's stat config (id + name) |
| PATCH  | `/game/stats/{id}`                   | Override a stat's display name (id is immutable) |
| GET    | `/game/curve`                         | Return the XP curve table |

The per-request handler builds a fresh `Facade` from the JWT
subject, calls the orchestration method, maps facade results to
HTTP errors. Same pattern as the existing productivity and
profile routers.

## UI

A single new Streamlit page lives under the "User" nav group:
`Character`. Surfaces only when `configs.game.enabled` is True —
the User group renders unchanged when the layer is off. Page
contents:

- Five stat cards, one per stat in `GameConfig.stats`, showing
  level, label, and an XP-to-next progress bar
- Level history table (newest first)

The dashboard is intentionally untouched in this iteration — the
Character page is the dedicated surface, not a dashboard widget.
The "User" nav group is the right home because the game layer is
a personal context (your character), not a productivity tool.

Inline feedback on task completion and session stop comes for
free from the hook surfaces in the previous section: the focus
completion dialog and the focus stop handlers get XP for free;
no extra toast / animation is wired this round.

## Migration

A one-time backfill populates `xp_events` from completed tasks
and completed sessions that pre-date this layer. See
`scripts/backfill_xp.py`. Run dry-run first to preview the
plan, then drop `--dry-run` to apply. Idempotency keys match
live events (`task:{uid}:completed`, `session:{uid}:stopped`)
so re-runs are safe and live events fired after this layer
shipped aren't duplicated.

The script duplicates `GameFacade._resolve`'s inference logic
intentionally, for performance: the backfill iterates over
historical data and would be slow if every event went through
the facade. If the inference rules ever change, update the
script's copy of `_resolve_task` / `_resolve_session` in
lock-step with `src/genesis/game/xp.py` and
`src/genesis/game/classify.py`.

Backfilled events carry `reason="backfill: <source>"` so the
events list shows where each XP came from. Future agents
reading the events log can distinguish backfilled events from
live ones by inspecting the reason. We do not rewrite the
historical `created` / `modified` on the source tasks or
sessions — only `occurred_at` on the new XP event carries the
historical timestamp.

## Non-goals

- No telemetry ingestion (iOS Shortcuts, screen time, HealthKit).
  The design note mentions it; that's a separate ADR when we get
  to it.
- No daily focus as a stored entity. The agent (Max) writes it
  into the user's daily journal as plain markdown.
- No social links (Alisa as a named link, etc.).
- No DM agent. The personal assistant agent uses a
  persona-instruction mode when needed.
- No CLI subcommand for the game layer.
- No game config editing over HTTP. Edit by hand for now; an
  edit endpoint is a future ADR if it earns its place.
- No multi-stat XP events in v1. Data model supports it
  (one event = one stat) but the Facade records one event per
  call. The caller can issue multiple calls if needed.
- No porting of the stemmer to `genesis.category.inference` this
  round. Tracked as future work.

## Dependencies

One new third-party package: `nltk`. Required by
`game/classify.py`. Used only for `nltk.stem.PorterStemmer`,
which is pure-Python tables — no model download. AGENTS requires
developer approval before adding any new package. Flagged in the
plan.

## Consequences

Positive:

- XP layer sits next to its source data, no cross-service chatter
  on every task completion.
- Game layer is opt-in per user (`game.enabled`), addressing the
  opinionatedness concern without abandoning the design intent.
- Game facade isolated from CalDAV reads of tasks/sessions — the
  storage layer can change without touching the game logic.
- Explicit two-step at call sites: no magic hooks, no implicit
  side effects, easy to debug.

Negative:

- One new CalDAV collection (`xp_events`) — first amendment to
  ADR-012's "four fixed collections" invariant.
- One new dependency (`nltk`). Pure-Python, small, but a new
  supply-chain surface regardless.
- Game config is YAML-edited only. Toggling the layer off or
  changing the stat set requires editing `configs.yaml` and
  restarting the Facade.
- `GameFacade.record_xp_event` is the only public surface; ad-hoc
  callers must use the Facade orchestration method, not the
  underlying caldav write. This is a feature, not a bug, but
  worth flagging.

Neutral:

- Keyword engine uses stemmer for robustness. If we want to swap
  to a different approach later (TF-IDF, embeddings, LLM
  classification), `classify.py` is the only file that changes.
- Level labels and XP curve are hardcoded. A future ADR can move
  them to per-user config if we want finer control.

## File map

- New files:
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
  - `docs/adr/adr-026-game-module.md` (this ADR)
  - `docs/plans/adr-026-game-module.md` (the plan)

- Modified files:
  - `src/genesis/caldav/constants.py` — add `XP_EVENTS_COLLECTION`
  - `src/genesis/caldav/ical.py` — `xp_event_to_ical` / `xp_event_from_ical`
  - `src/genesis/caldav/facade.py` — four delegations for xp_events
  - `src/genesis/configs.py` — `GameConfig`, `StatConfig`, `_build_game`
  - `src/genesis/core/facade.py` — `.game` lazy property, `record_xp_event`, `complete_task`, `stop_session`, `_fire_xp_safely`
  - `src/genesis/api/schemas.py` — game wire models
  - `src/genesis/api/app.py` — include the game router
  - `src/genesis/api/routers/productivity.py` — `PATCH /tasks/{uid}` detects status→COMPLETED, fires XP
  - `src/genesis/ui/app.py` — register Character page in User group (gated on `configs.game.enabled`)
  - `src/genesis/ui/_render.py` — `focus_completion_dialog` uses `facade.complete_task`
  - `src/genesis/ui/pages/focus.py` — auto-stop + manual Stop use `facade.stop_session`
  - `src/genesis/ui/_table.py` — `_handle_inline_edit` detects status→COMPLETED, fires XP
  - `pyproject.toml` — `nltk` dep (developer approved)