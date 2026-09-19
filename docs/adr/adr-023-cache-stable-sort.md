# ADR-023: Cache-based Stable Sort for Task Tables

## Status

Proposed

## Context

The `render_tasks_table` widget (and its `render_filterable_tasks_table`
wrapper) uses `st.data_editor` for inline edits. `data_editor` tracks
in-flight edits (`edited_rows`) **by row position**, keyed off the widget's
`key=`. The element identity of a keyed `data_editor` does not include the
data — so when the underlying data reorders between renders, the browser
resends positional edits that now address different records. This is
upstream bug
[streamlit/streamlit#17026](https://github.com/streamlit/streamlit/issues/17026)
on Streamlit 1.60+ (we are on 1.61.1) and there is no setting to disable
it. Programmatic mutation of `DataEditorState` is documented as
unsupported, so `state["edited_rows"] = {}` in `_handle_inline_edit` is
silently ignored.

The bug manifests concretely on `pages/projects.py`'s project detail
view, which sorts by `(status.value, -priority)`. An inline priority edit
reorders the rows, the next render re-applies the pending edit at the
**old** row position, and the wrong task gets the new priority. If the
user edits again, the prior pending edit and the new one both dispatch
against whatever task sits at those positions, cascading the corruption.

The previous fix (`f3d7c65`) addressed this by sorting `list_tasks()` by
`(created, uid)` for stable default order and clearing `edited_rows` in
the callback. That covers pages whose sort key does not change with
priority (global tasks, dashboard, calendar). It does **not** cover the
project detail page because that page does a local `sorted(...)` keyed on
priority, which is by definition unstable on a priority edit. The clear
also does not actually take effect because `DataEditorState` is
read-only.

We want inline edits to be safe on every page that uses the table, and
we want all of those pages to share the same ordering so the table's
behaviour is consistent.

## Decision

We will make the table widget responsible for the "do not re-sort on
edit" guarantee. Pages opt in by passing a `sort_keys` parameter. The
widget freezes the sort order on the first render of a session, walks
that cached order on subsequent renders, and offers a "Re-sort" button
to opt out of the freeze. The cache lives in `st.session_state`, keyed
by the widget's `key=`, so it survives across reruns and is unique per
table instance.

`sort_keys` is a list of `(field_name, none_default)` tuples. Each tuple
is one sort level: the field on `Task` to read, and the value to use
when that field is `None`. The widget composes them into a tuple sort
key in the given order. This keeps the API declarative (no lambdas,
matches the style of `list_tasks(..., sort_by=[...])`) and gives the page
explicit control over how `None` is handled per field.

The default order, applied to the project detail page, the global tasks
page, and both of the dashboard's tables:

1. `hard_deadline` ascending, `None` -> `date.max` (no deadline sorts
   last).
2. `priority` ascending, `None` -> `5` (matches the existing convention
   in `_table.py` and the data_editor column default).
3. `created` ascending, `None` -> `datetime.max` (no creation date sorts
   last).

Defined once in `_table.py` as `TASK_TABLE_DEFAULT_SORT_KEYS` and shared
across pages.

The Re-sort button is a single `st.button("Re-sort", ...)` rendered
just above the `data_editor`. Its handler pops the cache entry and
calls `st.rerun()`, which causes the next render to recompute from the
current task list.

The calendar's "this week in detail" table is **not** opted in. Its
existing `scheduled_start` sort is the right UX for a week view and
does not have the row-drift problem because the sort key does not
change in response to an inline edit.

## Consequences

Positive:

- Inline edits are safe on every opted-in page. The row the user
  clicked maps to the same task on every subsequent render, so the
  data_editor's positional `edited_rows` payload stays aligned with the
  right tasks. The cascade is broken at the source.
- All opted-in tables sort identically (deadline -> priority ->
  created), so the table's behaviour is consistent across surfaces.
- The cache and Re-sort logic are encapsulated in the widget. Pages do
  not touch `st.session_state` for sort state.
- A Re-sort button gives users an explicit way to refresh the order
  after multiple edits without reloading the page.
- The `sort_keys` parameter is opt-in. Pages that do not pass it keep
  the existing pre-sort-on-page behaviour (which is what the calendar
  wants).

Negative:

- Users must click "Re-sort" or refresh the page to see a new ordering
  after editing priority or deadline. This is the explicit trade-off
  that makes the bug fix possible. It applies uniformly to opted-in
  pages.
- One `st.button` per opted-in table. Four buttons total (project
  detail, global tasks, dashboard assignments, dashboard deadlines).
- The Re-sort button uses the widget `key=`, which means the button
  itself rebuilds when the cache is bumped — same widget identity
  pattern the rest of the widget already uses.
- `_stable_sort_first_render` is the only non-trivial piece of logic
  added to the widget. It is covered by a focused unit test against a
  mocked session_state.

## References

- streamlit/streamlit#17026 — upstream `data_editor` row-drift bug.
- `f3d7c65` — prior partial fix (stable `list_tasks` sort + clear
  `edited_rows`). Kept for the pages that already sort stably; not
  relied upon for correctness on opted-in pages.
- AGENTS.md — ADR conventions, testing model.
- `docs/adr/adr-013-streamlit-ui.md` — task table widget contract.
