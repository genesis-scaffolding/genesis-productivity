# Plan for t3: hide done tasks by default

## Context

Reported in `genesis-productivity-system-task-priorities.md` (`t-gesoeo`)
plus a follow-up note in the developer's clarification: default the
status filter on the task tables to exclude `COMPLETED` and
`CANCELED` so the user sees an actionable backlog instead of the full
historical record. Also hide done tasks from the Focus page task
dropdown so a session cannot be aligned to a closed task.

The composite component from `t2-composite-task-filter` already
accepts `default_statuses`. This branch flips the default on the two
pages that use it and adjusts the focus page task dropdown.

## Steps

1. In `src/genesis/ui/pages/tasks.py`, pass
   `default_statuses=[TaskStatus.NEEDS_ACTION, TaskStatus.IN_PROGRESS]`
   to the `render_filterable_tasks_table` call. Re-add the
   `TaskStatus` import only if needed (it is needed for the
   `default_statuses` list literal).

2. In `src/genesis/ui/pages/projects.py`, in `_render_detail`, pass
   the same `default_statuses` to
   `render_filterable_tasks_table`.

3. In `src/genesis/ui/pages/focus.py`, in `_render_start_form`, the
   task list is currently
   `tasks = caldav.list_tasks(status=st.session_state.get("focus_task_status_filter"))`.
   This already supports a `focus_task_status_filter` session key but
   defaults to None (all statuses). Seed the default to
   `[TaskStatus.NEEDS_ACTION, TaskStatus.IN_PROGRESS]` when the key is
   unset. The list value is a single-task filter, but the existing
   `list_tasks` interface takes a single `status` not a list, so
   instead either:
   - (preferred) call `list_tasks` once per active status and concat
     the results, deduplicating by uid; or
   - call `list_tasks(status=None)` then filter client-side to the
     union of NEEDS_ACTION + IN_PROGRESS.

   Pick the second option (client-side filter) — `list_tasks` already
   runs a server-side calendar-query and the candidate set is small
   for personal use. Add a one-line code comment explaining the
   choice. Keep `focus_task_status_filter` as the override hook for
   power users.

   Also keep the `(unaligned)` entry at the top of the dropdown so
   the user can still launch an unaligned session.

4. Verify with `make lint`, `make typecheck`, `make test`. No new
   tests (UI-only behavior; existing tests must stay green).

5. Manual smoke test (document, do not gate): `make serve`, navigate
   to Tasks, confirm the status pills show only NEEDS_ACTION and
   IN_PROGRESS selected, with COMPLETED and CANCELED off. Navigate to
   a project detail page, same expectation. On Focus, confirm the
   task dropdown does not list any completed or canceled tasks.

## Out of scope

- Removing the option to show completed/canceled tasks: the pills
  remain editable so the user can opt back in.
- A persistent user preference: this branch seeds the default each
  time the session_state key is missing; existing keys in
  session_state are preserved across runs.
- The retrospective session log dialog (`session_log_dialog` in
  `_render.py`): it shows all tasks for alignment; out of scope for
  this branch.

## Files touched

- `src/genesis/ui/pages/tasks.py`
- `src/genesis/ui/pages/projects.py`
- `src/genesis/ui/pages/focus.py`