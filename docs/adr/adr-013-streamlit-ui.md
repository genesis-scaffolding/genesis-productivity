# ADR-013: Streamlit Multi-Page UI

## Status

Accepted

## Context

The backend is now feature-complete for a single user (ADR-012). There is no way to physically exercise the system without a UI. We need a thin, low-risk web front-end so a developer can:

- Browse and edit projects, tasks, events, time log entries, and journal entries.
- See how shadow VEVENTs land on a calendar.
- Catch bugs that unit and integration tests miss (visual layout, CRUD flow, edge cases in forms).

The reference design is the React UI in `../genesis-scaffolding/`. We are not porting the agentic/automation/chat surfaces — only the productivity surfaces:

- Dashboard
- Projects (list + detail)
- Tasks (global list with quick-add)
- Calendar (week view of shadow VEVENTs)
- Journal (list + view/edit/create)

The reference Streamlit project at `../genesis-agent-backend/genesis_worker/ui/` already establishes conventions: `st.navigation` for multi-page, `@st.cache_resource` for the singleton, one page per file under `ui/pages/`, shared helpers in `ui/_render.py`. We follow the same shape so the codebase reads consistently.

Forces:

- Streamlit is already in the dependency tree (used for the placeholder page in `src/genesis/ui/app.py`).
- Custom CSS is fragile across Streamlit releases — we want minimal `unsafe_allow_html` use.
- The journal entry backend is currently a flat markdown file with no type or title fields. The reference UI's type filter chips require those fields.
- We must route to detail views (project, journal entry) without hand-rolling a router.

## Decision

### Multi-page architecture

Use `st.navigation({"Productivity": [st.Page(...), ...]}).run()` in `src/genesis/ui/app.py`. One page per file in `src/genesis/ui/pages/`.

### Page list

| Page | File | Purpose |
| --- | --- | --- |
| Dashboard | `pages/dashboard.py` | Today's tasks count, active project count, today's task list |
| Projects | `pages/projects.py` | Cards grouped by status, "New Project" button |
| Project Detail | `pages/project_detail.py` | Project metrics + task list + quick-add, reads `?uid=` |
| Tasks | `pages/tasks.py` | Global task list with `st.data_editor`, quick-add form |
| Calendar | `pages/calendar_page.py` | Week grid showing shadow VEVENTs |
| Journal | `pages/journal.py` | List with type filter chips |
| Journal Entry | `pages/journal_entry.py` | View / edit / create modes, reads `?date=` and `?new=` |

### Routing

Streamlit-native. Two detail pages read query params:

- `project_detail` — `st.query_params["uid"]` for the project UID.
- `journal_entry` — `st.query_params["date"]` plus `?new=1` or `?edit=1` mode flag.

No custom router. Navigation uses `st.switch_page("page_name")` and `st.query_params.<key> = value`.

### State and caching

- `@st.cache_resource` for the singleton `Facade` in `src/genesis/ui/_state.py`, keyed on `cfg.user`.
- Ephemeral state (toast text, last-edited UID, in-flight dialogs) in `st.session_state`.
- After every mutation, `st.rerun()` to reflect the new state.

### Forms and edits

- **Task quick-add** (Tasks, Project Detail): inline `st.form` with summary, optional project, optional scheduled_start.
- **Task full edit**: `@st.dialog("Edit task")` triggered from a row button.
- **Task inline edits** (summary, status, scheduled, project): `st.data_editor` with a `status` selectbox column. Other field changes go through the dialog.
- **Project create / edit**: `@st.dialog`.
- **Journal create / view / edit**: dedicated page (`journal_entry.py`) with view / edit / create modes. See below.
- **Delete confirmations**: small inline confirm using `st.button` twice, or a brief `@st.dialog` if the row is dense.

### Calendar view

Custom week grid in pure Streamlit. 7 `st.columns` for the day headers, hour rows 00:00–24:00 down the left margin. Shadow VEVENTs (`task_id` set) render as a block in their time slot, color-coded by project. No third-party calendar widget — keeps the dependency surface flat.

Above the grid: prev / next / today buttons and a month label.

### Journal entry format

Extend the journal backend so journal entries carry an optional `type` and `title`. Stored as YAML frontmatter at the top of the markdown file:

```markdown
---
type: daily
title: Weight Lifting Session
---

Performed weight lifting session today. ...
```

Files without frontmatter continue to parse (default: no type, title derived from date). The `JournalFacade.Entry` model gains two optional fields. The `JournalFacade` parses and serializes the frontmatter with `pyyaml` (already a dependency).

`type` is a `StrEnum` with values: `daily`, `weekly`, `monthly`, `yearly`, `project`, `misc`. Filter chips on the journal list page work against this field.

### What we skip in v1

- Recent Activity feed (no backend event log; not in scope).
- Assistant Pulse card (agents).
- Workspace Settings / Logout sidebar (multi-user is handled by the `genesis --user <name> ui` CLI invocation per ADR-010).
- Column chooser (fixed columns per page).
- Free-text search (one-line `st.text_input` filter only where it costs nothing; otherwise skip).
- Card-grid CSS equal-height nudges — only add if the layout genuinely breaks without it.

## Consequences

### Positive

- Testable UI with a clear page-per-file layout. New pages are obvious and additive.
- Canonical Streamlit patterns reduce risk of breakage on Streamlit upgrades.
- Backend journal change is small and backward-compatible.
- Calendar works out of the box because the shadow VEVENT pattern (ADR-012) already feeds the events collection.
- All UI features map 1:1 to existing facade methods — no new backend needed beyond the journal frontmatter.

### Negative

- More files in `src/genesis/ui/`. Acceptable; the structure is fixed.
- Streamlit forms and dialogs have some UX quirks (form state survives `rerun` but widget keys do not). We will use stable `key` strings everywhere.
- The journal frontmatter change is a backend touch driven by a UI need. We accept this coupling rather than spawning a separate ADR for two small fields.

## References

- Reference UI: `../genesis-scaffolding/` (React app, productivity surfaces only).
- Streamlit conventions: `../genesis-agent-backend/genesis_worker/ui/`.
- ADR-012: Time Layer CalDAV Model (shadow VEVENT pattern this UI depends on).
- ADR-010: CalDAV Multi-User Authentication (`--user` CLI flag, restarts the UI per user).
