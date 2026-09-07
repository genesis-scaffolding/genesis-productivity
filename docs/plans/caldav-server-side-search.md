# Plan: CalDAV server-side search

Refactor the four `list_*` functions to use `Calendar.search()` / `Calendar.searcher()` (which wrap RFC 4791 REPORT calendar-query) instead of `Calendar.objects()` + client-side filter loops. Tier 1 and Tier 2 items from the audit, in suggested order.

## Steps

### 1. Branch

Already on `feature/caldav-server-search` (off master).

### 2. `src/genesis/caldav/events.py` — list_events

Replace the `objects()` loop with `cal.searcher(event=True, ...)`. Add server-side filters:

- `task_id`: `searcher.add_property_filter("X-TASK-ID", task_id, operator="==")`
- `status`: `searcher.add_property_filter("STATUS", status.value, operator="==")`
- `search`: `searcher.add_property_filter("SUMMARY", needle, operator="contains")` OR'd with `DESCRIPTION` (CalDAV reports AND property filters, so two separate searches then union, OR collapse to a SUMMARY-only filter for first cut and keep description as client-side post-filter)
- Keep `sort_by`/`sort_order` as client-side (Radicale single-key sort)
- Default sort: `(start, uid)` if no sort_by
- Keep `NotFoundError` per-iteration catch

### 3. `src/genesis/caldav/tasks.py` — list_tasks

Same pattern with `todo=True` (must pass `include_completed=True` to match current behavior).

Add new parameters:
- `start: datetime | None = None`
- `end: datetime | None = None`

These map to REPORT calendar-query time-range filter (`<C:time-range>`). Used by calendar page for "this week" tasks and future dashboard "today".

Keep `(created, uid)` default sort.

### 4. `src/genesis/caldav/projects.py` — list_projects

`todo=True` (projects are VTODOs), `include_completed=True`. Same pattern.

- `status`: server-side STATUS filter
- `search`: SUMMARY filter
- sort_by/sort_order: client-side
- Default sort: `(name, uid)` (was no default)

### 5. `src/genesis/caldav/time_log.py` — list_time_log

`event=True`. Same pattern.

- `task_id`: server-side X-TASK-ID filter
- sort_by/sort_order: client-side
- Default sort: `(start, uid)`

### 6. `src/genesis/caldav/facade.py` — update delegations

Update `list_tasks` and `list_events` to accept and pass new `start`/`end` parameters. `list_projects` and `list_time_log` signatures unchanged (still expose same args).

### 7. `src/genesis/ui/pages/calendar_page.py` — use server time-range

Change calendar week view to use `start`/`end` for both `list_events` and `list_tasks` instead of filtering in Python.

For `list_events`: pass `start=week_start, end=week_end_plus_one_day` for server-side overlap filter.
For `list_tasks`: pass `start=week_start, end=week_end_plus_one_day` for server-side DTSTART filter.

### 8. `src/genesis/ui/pages/dashboard.py` — use status filter

Change `active_projects` from `list_projects()` + Python filter to `list_projects(status=ProjectStatus.IN_PROGRESS)`.

Keep `today_tasks` client-side as-is (combines scheduled_start / assigned_date / hard_deadline — not all mappable to one server query).

### 9. Tests

Existing tests use `MockCalDAVFacade` (mocks at facade level, not caldav library), so they verify the public contract independent of implementation. They should keep passing without changes.

Add new integration tests to `tests/test_caldav.py`:
- `test_list_events_status_filter_returns_only_matching` — confirm server-side STATUS filter works against Radicale
- `test_list_tasks_project_id_filter_returns_only_matching` — X-PROJECT-ID server-side
- `test_list_tasks_time_range_filter` — DTSTART in window

### 10. Quality gates

- `make lint`
- `make typecheck`
- `make test`
- `make test-integration`

### 11. Commit

Single commit: `refactor(caldav): use server-side calendar-query for list_* filters`.

Files staged explicitly (no `git add -A`):
- src/genesis/caldav/events.py
- src/genesis/caldav/tasks.py
- src/genesis/caldav/projects.py
- src/genesis/caldav/time_log.py
- src/genesis/caldav/facade.py
- src/genesis/ui/pages/calendar_page.py
- src/genesis/ui/pages/dashboard.py
- tests/test_caldav.py
- docs/plans/caldav-server-side-search.md (this file)

## Out of scope (defer)

- Search (SUMMARY substring) DESCRIPTION OR branch — currently too much complexity for the value
- Server-side multi-key sort
- Tier 3 items from audit
- Project page `list_tasks(project_id=uid)` is already using a parameter that becomes server-side automatically — no UI change needed
