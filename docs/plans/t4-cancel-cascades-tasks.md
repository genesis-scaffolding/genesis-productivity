# Plan for adr-022

## Context

ADR-022 captures the decision to cascade project status changes
(`CANCELED` and `COMPLETED`) to the project's open tasks. The user
confirmed both transitions: "if the project is cancelled, then all
remaining tasks of that project are cancelled. If a project is
considered completed, then all remaining tasks are completed."

This plan implements the cascade in the backend and adds coverage
at the unit and API layers.

## Steps

1. In `src/genesis/caldav/projects.py`, modify `update_project` to
   cascade the terminal-status transition. Implementation:
   - After the project's own `_populate(obj.icalendar_component, project)`
     + `obj.save()`, check if `update_data["status"]` is `CANCELED` or
     `COMPLETED`. If so, iterate `list_tasks(project_id=uid)`. For each
     task whose `status` is in `{NEEDS_ACTION, IN_PROGRESS}`, dispatch
     `update_task(task.uid, TaskUpdate(status=update_data["status"]))`.
   - Tasks already in `CANCELED` or `COMPLETED` are left alone.
   - The `update_task` path already populates `completed_at` correctly
     for the COMPLETED case (existing logic in `tasks.py`); no change
     needed there.
   - Keep the cascade scoped via `list_tasks(project_id=uid)`; tasks
     not belonging to this project are not touched.

2. In `tests/mock_caldav.py`, mirror the cascade in `update_project`
   so unit tests see the same observable behavior. The mock has its
   own state, so the cascade is a small loop that calls
   `update_task` for each open task.

3. In `tests/test_caldav_unit.py`, extend `TestProjectCRUD` with cases:
   - `test_update_project_to_canceled_cascades_to_open_tasks`: create a
     project with one NEEDS_ACTION and one IN_PROGRESS task; transition
     to CANCELED; assert both tasks are now CANCELED.
   - `test_update_project_to_completed_cascades_to_open_tasks`: same
     shape, transition to COMPLETED; assert both tasks are now
     COMPLETED with non-null `completed_at`.
   - `test_update_project_terminal_leaves_terminal_tasks_alone`: a task
     already CANCELED stays CANCELED after the project transitions;
     same for COMPLETED.
   - `test_update_project_terminal_does_not_affect_other_projects`:
     a task on a different project is untouched.

4. In `tests/test_api.py` `TestTasks` (or a new class), add an
   integration-style unit test using `api_server_with_mock`:
   - Create project A with task T1; create separate task T3 on no
     project.
   - PATCH `/productivity/projects/{A}` with `{"status": "canceled"}`.
   - GET `/productivity/tasks/{T1}` and assert status is `canceled`.
   - GET the global task list and assert T3 is still `needs-action`.

   Use the `_login` / `_auth` helpers that other tests in the file
   already use.

5. Run `make lint`, `make typecheck`, `make test`. Confirm new tests
   pass and the full unit suite stays green (the one
   pre-existing failure in `test_api_statistics.py` is unrelated and
   addressed separately).

6. Commit, merge, and close the branch.

## Out of scope

- `delete_project` already cascades via `_clear_project_id_on_tasks`
  (clears `project_id` rather than changing `status`). No change
  needed.
- The `update_project` path for non-terminal status transitions
  (e.g. NEEDS_ACTION to IN_PROGRESS) does not cascade. Open tasks
  stay open. Intentional.
- The cascade is not transactional. Each task write is a separate
  PUT. A future change could batch them, but the existing
  `_clear_project_id_on_tasks` precedent is also sequential, so the
  precedent stands.
- Audit logging or "tasks cascaded" telemetry. Not requested.

## Files touched

- `src/genesis/caldav/projects.py` (cascade logic in `update_project`)
- `tests/mock_caldav.py` (cascade mirror in `update_project`)
- `tests/test_caldav_unit.py` (four new test methods in
  `TestProjectCRUD`)
- `tests/test_api.py` (one new test method)
- `docs/adr/adr-022-cancel-project-cascades-tasks.md` (already
  written in this branch)
- `docs/plans/t4-cancel-cascades-tasks.md` (this file)