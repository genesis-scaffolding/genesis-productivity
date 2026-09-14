# Plan for t1: preserve project_id when editing a task from a project page

## Context

Reported in `genesis-productivity-system-task-priorities.md` (`t-dw20mf`).
High-impact data-integrity bug. The user opens the Edit dialog for a task
that belongs to a project, edits only the time fields
(`scheduled_start_date` / `schedule_time` / `scheduled_duration`), and
clicks Save. The task then silently unlinks from its project and
disappears from the project's task list.

Root cause: the Project dropdown's session_state for the dialog widget
key (`etd_{uid}_proj`) persists across dialog opens. Once a user has
opened the dialog and either explicitly selected "(none)" or had the
default resolution go to "(none)" on a prior open, the dropdown
displays "(none)" on subsequent opens even when `task.project_id`
points at a real project. The dialog's Save handler then sends
`project_id=None, clear_project_id=True`, unlinking the task.

The defaulting logic in `task_edit_dialog` (`src/genesis/ui/_render.py`)
computes `cur_idx` correctly, but `st.selectbox` uses the session_state
value rather than `index=cur_idx` once the key has been touched. The
inline-edit path is already safe because it only sends project fields
when the Project column is visible (`show_project_column=True`).

## Steps

1. In `src/genesis/ui/_render.py`, at the top of `task_edit_dialog`
   (after `kp = _form_key("etd", uid)` and before any widget that uses
   that key prefix is rendered), reset the project dropdown's
   session_state so the dropdown defaults to the task's actual project
   on every dialog open. Use the canonical pattern: compute
   `project_labels` and `cur_idx` first, then either set
   `st.session_state[f"{kp}_proj"] = project_labels[cur_idx]` (forcing
   the initial render to use that label) or, equivalently,
   `st.session_state.pop(f"{kp}_proj", None)` (forcing streamlit to
   fall back to `index=cur_idx`). Pick whichever the surrounding code
   style prefers; document the choice with a one-line comment.

   Important: only reset the project dropdown key. Do not pop
   `f"{kp}_title"`, `f"{kp}_desc"`, etc. — those retain the user's
   in-progress input if they reopen, which is the desired behavior.
   Resetting only the project dropdown is enough to fix the bug
   without losing unrelated edits.

2. Add a regression test in `tests/test_caldav_unit.py` proving that
   `update_task` preserves `project_id` when the update carries no
   `project_id` or `clear_project_id` field. This is the
   contract-level guarantee the dialog path relies on. Place it in
   `TestTaskCRUD` next to the existing `test_update_task_sets_completed_at`
   test. Use `MockCalDAVFacade` so it runs under `make test`.

   ```python
   def test_update_task_preserves_project_id_when_not_changed(
       self, facade: MockCalDAVFacade
   ) -> None:
       """An update with no project fields must leave project_id alone."""
       project = facade.create_project(ProjectCreate(name="Work"))
       task = facade.create_task(
           TaskCreate(title="Linked", project_id=project.uid)
       )
       # Edit only the title; do not touch project_id or clear_project_id.
       updated = facade.update_task(task.uid, TaskUpdate(title="Renamed"))
       assert updated is not None
       assert updated.project_id == project.uid
   ```

3. Verify with:
   - `make lint`
   - `make typecheck`
   - `make test`

4. Manual smoke test (document in commit body, do not gate): start
   `make serve`, log in, open a project, click Edit on one of its
   tasks, change only the schedule time, click Save, confirm the task
   is still listed under the project.

## Out of scope

- Inline-edit path (`src/genesis/ui/_table.py`): already preserves
  project_id correctly because the Project column is hidden on the
  project detail page (`show_project_column=False`) and the handler
  only sets `project_id`/`clear_project_id` when the column is
  visible. No change needed.
- The other dialogs (`task_create_dialog`, `project_create_dialog`,
  `project_edit_dialog`, `session_log_dialog`): same root cause is
  not present in those dialogs; do not touch them.
- Streamlit version upgrade or new dependency: not needed.

## Files touched

- `src/genesis/ui/_render.py` (one localized change in
  `task_edit_dialog`)
- `tests/test_caldav_unit.py` (one new test method)