# Plan: direct dialog invocation

Fix the session-state-flag pattern bug across all five dialogs by replacing it with direct `@st.dialog` calls from button bodies. Also adds `on_dismiss="rerun"` so X-button dismissal produces a clean page render.

## Bug recap

- Flag set in on_click callback or button body, processed at top of page via `maybe_show_*`. Race: top-of-page runs BEFORE the body's flag set, so the dialog only opens on the next rerun (apparent "click did nothing").
- After dismissal via the dialog's X-button (or click outside), no rerun triggered, no flag popped. Subsequent page renders reopen the dialog from leftover flag.

## Files

### 1. `src/genesis/ui/_render.py`

- Rename:
  - `_create_task_dialog` → `task_create_dialog`
  - `_edit_task_dialog` → `task_edit_dialog`
  - `_create_project_dialog` → `project_create_dialog`
  - `_edit_project_dialog` → `project_edit_dialog`
  - `_delete_confirm_dialog` → `delete_confirm_dialog`
- All five decorators gain `on_dismiss="rerun"`.
- Remove all session-state indirection:
  - `_close_create_task_dialog`, `_close_edit_task_dialog`
  - `open_task_dialog`, `maybe_show_task_dialog`
  - `open_project_dialog`, `maybe_show_project_dialog`
  - `request_delete`, `maybe_show_delete_confirm`
- Inside each dialog body:
  - Cancel buttons: just `st.rerun()`. No more flag-pops.
  - Confirm/Add buttons: do the work, `st.toast`, `st.rerun()`.

### 2. `src/genesis/ui/_table.py`

- Import `task_edit_dialog`, `delete_confirm_dialog`.
- `_handle_action`: call `task_edit_dialog(target.uid)` and `delete_confirm_dialog(label, callback)` directly. Drop the session-state flag.

### 3. `src/genesis/ui/pages/tasks.py`

- Drop the top-of-page `maybe_show_task_dialog()` and `maybe_show_delete_confirm()` calls.
- Button body: `task_create_dialog()`.

### 4. `src/genesis/ui/pages/projects.py`

- Drop the top-of-page `maybe_show_*` calls.
- Buttons directly call `project_create_dialog`, `project_edit_dialog`, `task_create_dialog`, `delete_confirm_dialog`.

### 5. `src/genesis/ui/pages/dashboard.py`

- Drop the top-of-page `maybe_show_task_dialog()` call.

### 6. `src/genesis/ui/pages/calendar_page.py`

- Drop the top-of-page `maybe_show_task_dialog()` call.

### 7. `src/genesis/ui/pages/journal.py`

- `_handle_action` for table row Delete: call `delete_confirm_dialog(...)` directly.
- Entry view `🗑 Delete` button: call `delete_confirm_dialog(...)` directly.
- Drop the in-page `pending_delete` flag handling and `maybe_show_delete_confirm()` calls.

## Why this works

Direct invocation: the dialog function is called inside the button body. Streamlit's rerun processes the click, the body runs, the dialog opens — all in ONE rerun. After any rerun (whether triggered by Save/Cancel/X), the parent button is no longer in clicked-state, so its body doesn't re-fire. The dialog function is not called again. Dialog closes.

`on_dismiss="rerun"` makes X-button dismissal trigger an explicit rerun, so any session-state changes the dialog widgets made get flushed immediately. Without it, X just hides the dialog visually until the next user interaction triggers a rerun — same outcome with one less rerun.

## Tests

- No new unit tests (no unit tests for streamlit UI; behavior verified manually).
- Smoke test: launch streamlit, click each page's primary buttons (Add Task, Edit Project, Delete Task), confirm dialog opens on first click and does not reopen after navigation.

## Out of scope

- The same pattern (session_state flag + maybe_show) does not exist elsewhere — no other dialog flows to migrate.
