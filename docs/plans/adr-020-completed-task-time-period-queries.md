# Plan for adr-020

## Steps

1. Update `src/genesis/caldav/facade.py`: add
   `completed_at_start: datetime | None = None` and
   `completed_at_end: datetime | None = None` parameters to
   `CalDAVFacade.list_tasks`. Forward them to the module-level
   `tasks.list_tasks`. Update the docstring to mention the new
   filter alongside the existing `start`/`end`.

2. Update `src/genesis/caldav/tasks.py`: extend `list_tasks`
   signature with the same two params. When either is set:
   - Force `status = TaskStatus.COMPLETED` for the server-side
     REPORT so the candidate set is bounded. Override any
     `status` value the caller passed.
   - After parsing each candidate task, keep only those where
     `task.completed_at is not None` and falls in
     `[completed_at_start, completed_at_end)`. Use a small private
     helper `_in_completed_window(task, start, end)` so the
     comparison logic is in one place.
   - Update the module docstring to describe the new branch. The
     existing `start`/`end` paragraph stays unchanged.

3. Update `tests/mock_caldav.py`: add the same two params to
   `MockCalDAVFacade.list_tasks`. Apply the filter client-side
   (the mock has no real CalDAV server, so this mirrors the
   production path's client-side step). Keep the existing
   "datetime | None" parity pattern documented in the existing
   `start`/`end` comment.

4. Update `src/genesis/api/routers/productivity.py`: add
   `completed_at_start: Annotated[datetime | None, Query()] = None`
   and `completed_at_end: Annotated[datetime | None, Query()] = None`
   to the `list_tasks` route handler. Pass them through to
   `facade.caldav.list_tasks(...)`. No new client-side filter
   needed in the router because the facade handles it.

5. Tests in `tests/test_caldav_unit.py`:
   - `TestListTasksCompletedAtRange.test_in_window` — create one
     task completed inside the window, one outside; assert only
     the in-window task is returned when both bounds are set.
   - `TestListTasksCompletedAtRange.test_only_start` — only
     `completed_at_start` set: returns tasks with
     `completed_at >= start`.
   - `TestListTasksCompletedAtRange.test_only_end` — only
     `completed_at_end` set: returns tasks with
     `completed_at < end`.
   - `TestListTasksCompletedAtRange.test_excludes_uncompleted` —
     a task that is `NEEDS_ACTION` is never returned even when its
     `hard_deadline` is in the window.
   - `TestListTasksCompletedAtRange.test_empty_window` — bounds
     that exclude every task return `[]`.
   - `TestListTasksCompletedAtRange.test_combines_with_project_id`
     — `project_id` filter still applies alongside the new range.

6. Tests in `tests/test_api_unit.py`:
   - `TestListTasksCompletedAtQuery.test_passes_through_to_facade`
     — assert the facade receives the new params and the route
     returns matching tasks.
   - `TestListTasksCompletedAtQuery.test_none_params_match_all_completed`
     — neither bound set, `status=COMPLETED` returns all completed
     tasks (existing behaviour).
   - `TestListTasksCompletedAtQuery.test_combined_with_search` —
     `search` and the new range both apply.

7. Integration test in `tests/test_api.py` (under
   `@pytest.mark.integration`): create three tasks against a real
   Radicale, complete one with `completed_at` in window and one
   outside, leave one uncompleted, hit `GET /tasks` with the new
   query params, assert exactly the in-window task returns.

8. Run `make test` (unit, fast). Fix anything red.

9. Run `make test-integration`. Confirm the new integration test
   passes against a real server.

10. Run `make lint` and `make typecheck`. Fix all findings.

11. Update `tests/test_api_coverage.py` if it has a coverage map
    for `/tasks` query params — add the two new params.

12. Manual smoke: `make api`, create a task via
    `POST /tasks`, complete it via `PATCH /tasks/<uid>`, then hit
    `GET /tasks?status=completed&completed_at_start=...&completed_at_end=...`
    and confirm it appears. Confirm an uncompleted task with the
    same deadline window does not appear.

13. Update `README.md` only if it documents the `/tasks` query
    surface; otherwise skip.