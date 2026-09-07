# Plan for adr-013

## Steps

### 1. Branch and prep

1. Create `feature/streamlit-ui` branch off `master`.
2. Confirm `streamlit>=1.61.1` and `pyyaml>=6.0.3` are already in `pyproject.toml` (they are).
3. Add `make ui` target that runs `uv run streamlit run src/genesis/ui/app.py`.

### 2. Journal frontmatter backend

1. Update `src/genesis/journal/facade.py`:
   - Add `EntryType` `StrEnum` with values `daily`, `weekly`, `monthly`, `yearly`, `project`, `misc`.
   - Add `type: EntryType | None` and `title: str | None` fields to `Entry`.
   - Add `_parse(content: str) -> tuple[dict, str]` to split frontmatter (if any) from body.
   - Add `_serialize(meta: dict, body: str) -> str` to write frontmatter + body.
   - Update `get_entry` and `save_entry` to use both helpers.
2. Add `tests/test_journal_unit.py` with cases for:
   - File with full frontmatter parses all fields.
   - File without frontmatter returns `type=None`, `title=None`.
   - Save then load round-trips.
   - Save with no type/title writes no frontmatter block (backward compatibility).
3. Run `make test` to confirm green.

### 3. UI scaffolding

1. Create `src/genesis/ui/_state.py`:
   - `@st.cache_resource` function `get_facade() -> Facade` resolving `CONFIG_PATH_ENV` and instantiating the `Facade`.
   - Expose `get_caldav()` and `get_journal()` helpers that return sub-facades from the cached root.
2. Create `src/genesis/ui/_render.py`:
   - `status_pill(status: StrEnum) -> None` using `st.badge` for task / project / event status.
   - `project_card(project: Project) -> None` (name, description preview, status pill) inside a `st.container(border=True)`.
   - `task_dialog(existing: Task | None) -> dict | None` — the `@st.dialog`-decorated function for create/edit. Returns the form values on save, `None` on cancel.
   - `project_dialog(existing: Project | None) -> dict | None`.
   - `confirm_delete(label: str) -> bool` — small inline confirm pattern.
3. Rewrite `src/genesis/ui/app.py`:
   - `st.set_page_config(page_title="genesis", layout="wide", page_icon=":material/bolt:")`.
   - Build `nav: dict[str, list[st.Page]]` with one section `"Productivity"` and 7 pages, each `st.Page(path, title=..., icon=..., url_path=...)`.
   - `st.navigation(nav).run()`.

### 4. Pages

1. `pages/dashboard.py`:
   - Three `st.container(border=True)` cards: today's tasks, active projects, recent activity placeholder.
   - Below: today's task list with status pills.
2. `pages/projects.py`:
   - Fetch all projects via `caldav.list_projects()`.
   - Group by status, render as cards in a 3-column grid using `st.columns(3)`.
   - "New Project" button opens `_render.project_dialog(None)`.
   - Each card has a button that sets `st.query_params.project = uid` and `st.switch_page("project_detail")`.
3. `pages/project_detail.py`:
   - Read `st.query_params.get("project")` for the UID; redirect to list if missing.
   - Metrics row: progress percentage, task count, open count, deadline (mirrors the original screenshot).
   - Project tasks list (uses `caldav.list_tasks(project_id=uid)`).
   - "Edit Project" button opens `_render.project_dialog(project)`.
   - Inline quick-add task form.
4. `pages/tasks.py`:
   - Fetch all tasks via `caldav.list_tasks()`.
   - Render with `st.data_editor` for in-place summary / status / scheduled / project edits. Status column is a selectbox.
   - "Add task" button opens `_render.task_dialog(None)`.
   - Row action menu: "Edit" opens `_render.task_dialog(task)`, "Delete" uses `confirm_delete`.
5. `pages/calendar_page.py`:
   - Session-state-backed `current_week` date.
   - Prev / next / today buttons. Compute Monday of the displayed week.
   - Header row: 7 columns, day labels.
   - Body: 24 hour rows, each a row of 7 columns. In each day cell, render any events whose `[start, end)` intersects that hour slot.
   - Events come from `caldav.list_events()`; filter to shadow events (`task_id` is not None) and group by day.
6. `pages/journal.py`:
   - Fetch all entries via `journal.list_entries()`.
   - Filter chips: `st.pills(label, options=[..., "All"])` with the entry types + All.
   - "New Entry" button navigates to `journal_entry?date=<today>&new=1`.
   - Each row is a button that navigates to `journal_entry?date=<date>`.
7. `pages/journal_entry.py`:
   - Read `st.query_params.get("date")` and `st.query_params.get("new")`.
   - **View mode** (default): title, type badge, "Edit" + "Delete" buttons, `st.markdown` of the body.
   - **Edit mode** (`?edit=1`): title `st.text_input`, type selectbox, body `st.text_area`, "Save" / "Cancel" buttons.
   - **Create mode** (`?new=1`): date picker, type selectbox, title `st.text_input`, body `st.text_area`, "Create" button. Save navigates to view mode for the new date.

### 5. CLI

1. Update `src/genesis/cli/app.py` `ui` command (it already launches streamlit at `src/genesis/ui/app.py`; verify the path is correct after the rewrite and add `--server.headless` and `--server.address 0.0.0.0` flags if missing).

### 6. Tests and quality gates

1. Run `make lint` — auto-fix, then check.
2. Run `make typecheck` — 0 errors, 0 warnings.
3. Run `make test` — all unit tests pass.
4. Run `make test-integration` — all integration tests pass.

### 7. Document

1. Add a short `## UI` section to `README.md` documenting `make ui` and `genesis --user <name> ui`.
2. Note that the user passes `--user` to pick the data scope (per ADR-010).

### 8. Commit

1. Stage only files changed in this session (no `git add -A`).
2. Commit message: `feat(ui): add streamlit multi-page UI for productivity surfaces (ADR-013)`.
3. Push branch and merge to `master` (fast-forward) per AGENTS.md.
