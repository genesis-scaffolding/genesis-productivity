# Plan for t7: focus session and task list integration

## Context

The user reported in `genesis-productivity-system-task-priorities.md`
(`t-new-feature`) a set of small UX wins connecting the Focus page to
the Tasks page:

1. A "Start focus session" action from the task list, pre-attached
   to the chosen task.
2. After a focus session ends (auto-stop or manual stop), if a
   task was linked, ask the user whether to mark that task
   completed.
3. When the user picks a task inside the new-session dialog, the
   label auto-fills with the task title (editable).
4. Default the session category to ``work`` when a task is linked,
   ``other`` when not.

The user explicitly said these are straightforward and do not need
an ADR. No backend changes are required: both flows reuse existing
``update_task`` and ``create_time_log_entry`` calls, and the auto-
category is just a UI default that the user can still override.

## Steps

1. In `src/genesis/ui/_render.py`, add a new "Focus session
   dialogs" section:
   - ``focus_create_dialog(task: Task | None = None)``: a
     ``@st.dialog`` modelled on ``session_log_dialog`` and the
     existing ``_render_start_form`` body. When ``task`` is given,
     the dialog pre-selects it in the task selectbox, defaults
     the label to ``task.title``, and defaults the category to
     ``TimeLogCategory.WORK``. Without a task, the label defaults
     to ``"Focus session"`` and the category defaults to
     ``TimeLogCategory.OTHER``. The Start button writes a new
     in-progress entry via ``caldav.create_time_log_entry``.
   - ``focus_completion_dialog(task_uid, task_title)``: a small
     ``@st.dialog`` with a single Yes/No question and an Update
     call to ``caldav.update_task(uid, TaskUpdate(status=
     TaskStatus.COMPLETED))`` on Yes. No-op on No.
   - Move the ``_start_session`` builder from
     `pages/focus.py` into `_render.py` so the dialog can use it.
     The helper now lives next to its sibling in ``_render``.

2. In `src/genesis/ui/_table.py`, add a ``"Focus"`` entry to the
   per-row action list (right of ``"Edit"``, before
   ``"Delete"``). Dispatch the label in ``_handle_action`` to
   ``focus_create_dialog(task=target)``. No other tables change.

3. In `src/genesis/ui/pages/focus.py`:
   - Replace the inline ``_render_start_form()`` body with a single
     button that calls ``focus_create_dialog()``. Keep the existing
     public page layout (title, divider, active sessions fragment).
   - In ``_render_active_sessions`` (the fragment), after the
     auto-stop path stops an entry whose ``task_id`` is set, set
     ``st.session_state["focus_completion_pending"] = {"task_uid":
     ..., "task_title": ...}`` and ``st.rerun(scope="app")``. The
     auto-stop toast is dropped on the rerun; the completion
     dialog is the more useful signal.
   - In ``_render_session_card``, after the manual Stop writes the
     stopped entry, do the same flag-and-rerun if ``s.task_id`` is
     set. Discard path stays unchanged.
   - At the top of the focus page script, after the title/caption
     and before ``focus_create_dialog()``, check the flag. If set,
     call ``focus_completion_dialog`` (which pops the flag inside
     its Yes/No handlers). This runs on the app-level rerun, so
     the dialog opens cleanly from the parent script.

4. No backend changes. No new tests (UI behavior; verified
   manually). Verify with `make lint`, `make typecheck`,
   `make test`.

## Out of scope

- Cross-device completion prompt (the dialog is local to the page
  that did the stop).
- Auto-marking the linked task when the user dismisses the dialog
  with ESC; the existing on_dismiss="rerun" rerun still leaves the
  task open. The user can mark it from the task list or accept on
  next session.
- A separate API endpoint for the focus completion flow. The dialog
  reuses ``update_task`` directly.

## Files touched

- `src/genesis/ui/_render.py` (add focus_create_dialog,
  focus_completion_dialog; move _start_session here)
- `src/genesis/ui/_table.py` (add Focus button column entry)
- `src/genesis/ui/pages/focus.py` (replace form with button; wire
  completion dialog; update manual stop)
- `docs/plans/t7-focus-task-integration.md` (this file)
