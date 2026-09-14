# Plan for t2: composite task filter + table component

## Context

Reported in `genesis-productivity-system-task-priorities.md` (`t-mzzdk3`).
The global Tasks page has status filter pills above the table; the
project detail page does not. Anyone managing tasks from the project
view works with an unfiltered, cluttered table.

Currently the pills live inline in `src/genesis/ui/pages/tasks.py`,
so adding them to the project detail page would duplicate the same
pills + filtering logic in `src/genesis/ui/pages/projects.py`. The
fix is to extract the pills + filtering into a reusable composite
component so both pages (and future pages) share one implementation.

The default status set on the pills is set by `t3-default-hide-done-tasks`
in a follow-up branch, so this branch keeps the existing default of
all-statuses and just moves the rendering into a shared component.

## Steps

1. In `src/genesis/ui/_table.py`, add a new function
   `render_filterable_tasks_table` that wraps `render_tasks_table`
   with the status filter pills:

   ```python
   def render_filterable_tasks_table(
       tasks: list[Task],
       *,
       caldav: CalDAVFacade,
       key: str,                                # table key prefix
       show_project_column: bool = True,
       filter_key: str | None = None,           # pills key prefix
       default_statuses: list[TaskStatus] | None = None,
   ) -> None:
       """Render status filter pills above a tasks table.

       When ``filter_key`` is None, render the table only (no pills).
       When set, render the pills first and filter ``tasks`` to the
       selected statuses before passing to ``render_tasks_table``.
       ``default_statuses`` seeds the initial pill selection (defaults
       to all statuses when None).
       """
   ```

   Implementation notes:
   - Compute the available statuses once: `[s.value for s in TaskStatus]`.
   - Read the current selection from
     `st.session_state.get(filter_key, [s.value for s in default_statuses or TaskStatus])`.
   - Render with `st.pills("Status", options=statuses_all, default=..., selection_mode="multi", key=filter_key)`.
   - Filter `tasks` to the selected statuses and pass to
     `render_tasks_table` with `key=key`.

   Do not change `render_tasks_table` itself; it remains the single
   source of truth for the table widget. The wrapper composes pills
   on top.

2. In `src/genesis/ui/pages/tasks.py`, replace the inline pills +
   `render_tasks_table` block with a single call to
   `render_filterable_tasks_table` using key
   `"tasks_filter_statuses"` (existing session key — preserve user
   filter state across runs) and key `"global_tasks"` (existing table
   key). Drop the now-unused imports (`st.pills` logic moves into
   the wrapper). Keep the page title, the "+ Add Task" button, and
   the divider.

3. In `src/genesis/ui/pages/projects.py`, in `_render_detail`, replace
   the bare `render_tasks_table` call with
   `render_filterable_tasks_table` using a per-project filter key
   `"pd_filter_statuses_{uid}"` (so filter state does not leak across
   different projects) and the existing table key
   `"project_tasks_{uid}"`. Default status set is all statuses for
   this branch (Task 3 changes the default in a follow-up).

4. Verify with `make lint`, `make typecheck`, `make test`. The new
   composite has no test (UI-only); existing tests must remain green.

5. Manual smoke test (document, do not gate): `make serve`, navigate
   to Tasks, confirm pills still work and selection persists across
   page navigation. Navigate to a project, confirm pills render above
   its task table and filtering changes the row set.

## Out of scope

- Default status set on the project detail page: this branch keeps
  the all-statuses default. `t3-default-hide-done-tasks` flips it to
  `NEEDS_ACTION + IN_PROGRESS` across both pages.
- Inline status filter inside the table (e.g. column-level filter):
  not requested.
- Filters on priority, scheduled date, etc.: out of scope; the bug
  report names status only.
- Search box, sort selector: not requested.

## Files touched

- `src/genesis/ui/_table.py` (add `render_filterable_tasks_table`)
- `src/genesis/ui/pages/tasks.py` (call wrapper)
- `src/genesis/ui/pages/projects.py` (call wrapper in
  `_render_detail`)