# Plan for category-time-log

## Goal

Tag every `TimeLogEntry` with a category (Work / Play / Workout / Personal
/ Other) so tracked time stops conflating gameplay and groceries with
"working minutes". Inference fills in the category at stop time; the
user can override. Statistics exposes a per-category breakdown.

## User decisions captured

- Wire property: `X-CATEGORY` (matches `X-MODE` / `X-TASK-ID` pattern).
- History lookup: most-recent prior session with the same `label`
  decides the category. Falls through to keyword rules, then `Personal`.
- Task-aligned sessions default to `Work` but the UI lets the user
  override to anything (e.g. a "Gym" task -> `Workout`).
- UI control: a select with "Auto (inferred: X)" plus the five explicit
  categories. Default is Auto. UI never sends the literal value
  "Auto"; it resolves to the inferred value before calling stop.
- Statistics: keep `tasks_touched`. Break down `total_seconds` per
  category in the KPI strip; stacked daily chart; new `by_category`
  section.
- Recent sessions table: unchanged.
- Migration: Pydantic defaults missing `X-CATEGORY` to `Other` so
  legacy reads never crash. A standalone CLI backfills via inference.

## New module: `src/genesis/category/`

Mirror the per-module facade pattern (ADR-004).

- `enums.py`: `TimeLogCategory` (`work` / `play` / `workout` /
  `personal` / `other`).
- `inference.py`: pure function
  `infer_category(label, task_id, history) -> TimeLogCategory`.
  Precedence:
  1. `task_id` is set -> `Work`.
  2. Most-recent prior entry with the same `label` (any category) ->
     its category.
  3. Keyword match (case-insensitive substring) against a small
     word list per category (Play / Workout / Personal only -- Work
     never comes from keywords).
  4. Default `Personal`.
- `facade.py`: `CategoryFacade(caldav)` exposing
  `infer(label, task_id) -> TimeLogCategory` that pulls history via
  `caldav.list_time_log()`. Pure-history lookup; no IO outside caldav.
- `migration.py`: CLI entry `python -m genesis.category.migration`
  that iterates time log entries, fills missing categories via
  `infer`, writes back, and prints a summary. `--dry-run` prints what
  it would change without writing.
- `__init__.py`: re-exports `TimeLogCategory`, `infer_category`,
  `CategoryFacade`.

## Entity

- `src/genesis/entities/time_log.py`:
  - Add `category: TimeLogCategory = TimeLogCategory.OTHER` to
    `TimeLogEntry`, `TimeLogEntryCreate`, `TimeLogEntryUpdate`.
  - Add validator that coerces a bare string into `TimeLogCategory`
    (defensive; matches the enum-aware style elsewhere).
- `src/genesis/entities/__init__.py`: export `TimeLogCategory`.

## CalDAV round-trip

- `src/genesis/caldav/ical.py`:
  - `time_log_from_ical`: read `X-CATEGORY` -> `TimeLogCategory`;
    missing -> `OTHER`.
  - `time_log_to_ical`: write `X-CATEGORY` when not `OTHER`.
- `src/genesis/caldav/time_log.py`:
  - `_populate`: include `x-category` set/delete.
  - `create_time_log_entry`: pass through `data.category`.
  - `update_time_log_entry`: handle category in update payload.
  - `stop_time_log_entry`: accept `category: TimeLogCategory | None`
    and set on the entry when provided.
- `tests/mock_caldav.py`: mirror the same changes on `MockCalDAVFacade`
  so unit tests see the same observable state.

## Statistics

- `src/genesis/stats/facade.py`:
  - `Kpis` gains `seconds_by_category: dict[TimeLogCategory, float]`
    (one entry per category; zeros included).
  - `DailyPoint` gains `minutes_by_category: dict[TimeLogCategory, float]`
    (sum across categories == legacy `minutes`).
  - New `ByCategory` dataclass: `category`, `total_seconds`, `count`.
  - `Rollup` gains `by_category: list[ByCategory]` sorted by
    `total_seconds` desc.
  - `compute_rollup`: bucket in-period entries by category in KPIs,
    daily, and by_category.
- `src/genesis/stats/__init__.py`: export `ByCategory`.

## API wire

- `src/genesis/api/schemas.py`:
  - New `TimeLogCategory` StrEnum mirroring the entity enum.
  - `TimeLogEntryOut`: add `category: TimeLogCategory`.
  - `DailyPointOut`: keep `minutes` (sum) and add
    `minutes_by_category: dict[TimeLogCategory, float]`.
  - `KpisOut`: add `seconds_by_category: dict[TimeLogCategory, float]`.
  - New `ByCategoryOut`: `category`, `total_seconds`, `count`.
  - `StatisticsOut`: add `by_category: list[ByCategoryOut]`.
- `src/genesis/api/routers/productivity.py`: include the new fields
  in the statistics response.

## Focus page

- `src/genesis/ui/pages/focus.py`:
  - Add `CategoryFacade` import.
  - On each active session card, render a select with options
    `["Auto", "Work", "Play", "Workout", "Personal", "Other"]`.
    The select defaults to `Auto`. A small caption below shows the
    inferred category so the user sees what Auto resolves to.
  - When `Stop` is clicked, resolve `Auto` via
    `category_facade.infer(label, task_id)`, otherwise honor the
    picked value. Pass `category=...` to
    `caldav.stop_time_log_entry`.
  - Cache the inferred category per-session in `st.session_state`
    so the caption stays stable while the user decides.
  - Keep `Discard` unchanged (no category stored).

## Statistics page

- `src/genesis/ui/pages/statistics.py`:
  - Replace the four-card KPI strip with: the existing four cards
    plus a fifth "By category" card showing the per-category seconds
    as a small stacked or list breakdown.
  - Replace the daily bar chart with a stacked one: one column per
    category, one row per day. Use `st.bar_chart` with the dict as
    columns (Streamlit stacks multi-column bar charts by default).
  - Add a new `By category` section above `By task` listing
    `rollup.by_category` as a small table.
  - Leave `By task` and `Recent sessions` unchanged.

## Tests

- `tests/test_category.py`:
  - `TimeLogCategory` enum members + string values.
  - `infer_category` precedence: task-aligned -> Work, history
    override, keyword match, default.
  - Keyword edge cases: case-insensitive substring, multi-word,
    empty label.
  - `CategoryFacade.infer` pulls history correctly from a mock
    caldav.
- `tests/test_stats.py`: add cases for category KPI breakdown,
  category bucketed daily points, `by_category` ordering, and the
  empty-categories default to zero.
- `tests/test_api_statistics.py`: add cases for
  `seconds_by_category` shape, `minutes_by_category` per-day shape,
  and `by_category` payload.
- `tests/test_api_unit.py`: round-trip test confirming
  `TimeLogEntryOut.category` is set when an entry has an explicit
  category and defaults to `other` when missing.
- `tests/test_category_migration.py`: migration utility updates
  only entries without a category; respects `--dry-run`; idempotent.

## Migration CLI

- `src/genesis/category/migration.py`: Typer CLI (matches
  `src/genesis/cli/__main__.py` style) with one command:
  `backfill [--dry-run]`. Default behaviour writes through
  `caldav.update_time_log_entry`. Prints a one-line summary per
  changed entry plus a final count.

## Quality gates

- `make lint` clean.
- `make typecheck` 0 errors.
- `make test` green (including the new tests).
