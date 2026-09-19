# Plan for adr-023

## Steps

### 1. Branch and prep

1. Branch `feature/cache-stable-sort` off `master`.
2. Read `_table.py`, `pages/projects.py`, `pages/tasks.py`,
   `pages/dashboard.py` once more to confirm current call sites match
   what is in this plan (they should; nothing has changed since the
   audit).

### 2. Extend the widget in `_table.py`

1. Add imports: `from datetime import date, datetime` (already imported
   transitively in some pages; make sure they are present in `_table.py`)
   and `from typing import Any`.
2. Add a module-level constant:

   ```python
   TASK_TABLE_DEFAULT_SORT_KEYS: list[tuple[str, Any]] = [
       ("hard_deadline", date.max),
       ("priority", 5),
       ("created", datetime.max),
   ]
   ```

   with a comment block explaining the three-level key and the
   `None`-handling convention (matches what is already in `_table.py`
   for the priority column default).
3. Add `_stable_sort_first_render(tasks, *, sort_keys, cache_key)` near
   the other module-private helpers. Pure logic that:
   - On first render (`st.session_state[cache_key]` missing): builds a
     tuple sort key from `sort_keys` (each tuple = `(field, none_default)`
     -> `getattr(t, field, None)` if not None else `none_default`),
     calls `sorted(tasks, key=...)`, writes the resulting UID list to
     `st.session_state[cache_key]`, and returns the sorted tasks.
   - On subsequent renders: reads the cached UID list, walks it, emits
     tasks in that order, and appends any tasks whose UID is missing
     from the cache at the end. Never writes to `st.session_state`
     here (the cache is only populated on first render; deletion
     triggers re-sort).
4. Refactor the body so the pure sort logic lives in
   `_stable_sort_first_render`, but expose a pure helper
   `_apply_cached_sort(tasks, *, cached_uids, sort_keys) -> tuple[list[Task], list[str]]`
   that returns `(sorted_tasks, new_cached_uids)`. The session_state
   read/write stays in `_stable_sort_first_render`; the pure walker
   lives in `_apply_cached_sort` so it can be unit-tested without
   Streamlit. The walker returns the full cache list (cache + newly
   appended UIDs) so the caller can decide whether to write back;
   `_stable_sort_first_render` only writes back when the cache was
   empty.
5. Add `sort_keys: list[tuple[str, Any]] | None = None` to
   `render_tasks_table`. When non-None:
   - Compute `cache_key = f"{key}__sort"`.
   - Render `st.button("Re-sort", key=f"{key}__resort_btn")` whose
     handler is `st.session_state.pop(cache_key, None); st.rerun()`.
   - Replace the local `tasks` variable with
     `tasks = _stable_sort_first_render(tasks, sort_keys=sort_keys, cache_key=cache_key)`.
6. Add `sort_keys: list[tuple[str, Any]] | None = None` to
   `render_filterable_tasks_table` and pass it through to
   `render_tasks_table` (so the cache + Re-sort logic kicks in after
   the status filter has narrowed the task list).
7. Update the docstrings on both functions to mention the new
   parameter and the "no auto-resort on edit" guarantee.

### 3. Update the call sites

1. `pages/projects.py`:
   - Drop the `sorted_tasks = sorted(tasks, key=lambda t: (t.status.value, -(t.priority or 5)))` line.
   - Add `from genesis.ui._table import TASK_TABLE_DEFAULT_SORT_KEYS`.
   - Pass `sort_keys=TASK_TABLE_DEFAULT_SORT_KEYS` to
     `render_filterable_tasks_table`.
2. `pages/tasks.py`:
   - Add `from genesis.ui._table import TASK_TABLE_DEFAULT_SORT_KEYS`.
   - Pass `sort_keys=TASK_TABLE_DEFAULT_SORT_KEYS` to
     `render_filterable_tasks_table`.
3. `pages/dashboard.py`:
   - Add `from genesis.ui._table import TASK_TABLE_DEFAULT_SORT_KEYS`.
   - Drop both `active_assignments.sort(...)` and `deadlines.sort(...)`
     local calls.
   - Pass `sort_keys=TASK_TABLE_DEFAULT_SORT_KEYS` to the two
     `render_tasks_table` calls.
4. `pages/calendar_page.py`: no change (sort stays by
   `scheduled_start`).

### 4. Unit test for the sort helper

1. Add `tests/test_ui_table_unit.py`.
2. Test cases for `_apply_cached_sort`:
   - First call with `cached_uids=None`, tasks out of order: returns
     tasks in sorted order and a cache list of UIDs in the same order.
   - First call with all `None` sort fields: stable order preserved,
     cache records that stable order.
   - First call with mixed `None` and concrete values: `None` becomes
     the configured `none_default`.
   - Second call with cached UIDs in order: tasks returned in cached
     order even if `sort_keys` would order them differently (this is
     the regression test for the row-drift fix).
   - Second call with a new task whose UID is not in the cache: the
     new task is appended at the end and its UID is appended to the
     returned cache list.
   - Second call with a cached UID that is missing from the tasks list
     (task deleted): the missing UID is silently skipped, other tasks
     keep their cached positions.
   - Second call with both a new task and a missing UID: existing
     tasks in cached positions, new task appended.
3. Tests use a `dataclass` / simple object that exposes
   `uid`, `hard_deadline`, `priority`, `created` as the only fields
   `_apply_cached_sort` reads. No `Task` import required, no
   CalDAVFacade involved. Keeps the test fast and focused.

### 5. Quality gates

1. `make lint` — clean.
2. `make typecheck` — 0 errors, 0 warnings.
3. `make test` — all unit tests pass (existing + new).
4. Spot-check the manual flow described in the bug report (priority
   edit on the project detail page). The displayed priority at the
   edited row should update in place without dragging a stale value to
   a different row. Subsequent priority edits should land on the row
   the user actually clicked, not a row that has drifted.

### 6. Document

1. Brief note in the `Plan for adr-013` history isn't required; the
   ADR is the source of truth.
2. No README change (the table widget's public surface is documented
   in `_table.py`'s docstring; that docstring is being updated in
   step 2.7).

### 7. Commit

1. Stage only files changed in this session:
   `docs/adr/adr-023-cache-stable-sort.md`,
   `docs/plans/adr-023-cache-stable-sort.md`,
   `src/genesis/ui/_table.py`, `src/genesis/ui/pages/projects.py`,
   `src/genesis/ui/pages/tasks.py`,
   `src/genesis/ui/pages/dashboard.py`,
   `tests/test_ui_table_unit.py`.
2. Commit message: `feat(ui): cache-based stable sort for task tables (ADR-023)`.
3. Push branch and merge to `master` per AGENTS.md.
