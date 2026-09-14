# 022. Cascading status changes from projects to tasks

## Context

The user manages work as a tree: a project is an outcome-level goal,
tasks are the units of work under it. When a project reaches a
terminal state — completed or canceled — its open tasks should follow
the same terminal state. Otherwise the project page lists a pile of
NEEDS_ACTION / IN_PROGRESS tasks under a project that the user has
already written off, and the global task backlog gets cluttered with
abandoned work.

Today the `update_project` path in `src/genesis/caldav/projects.py`
only flips the project's own VTODO. Tasks that still point at the
project are left untouched. The user must either open each task and
mark it completed / canceled, or accept that the orphaned tasks stay
active forever.

Two specific behaviors matter, distinguished by the project's
terminal status:

- `CANCELED` means "abandon this outcome". All open tasks under the
  project were attempts at the outcome and should themselves be
  canceled — they are no longer worth anyone's time.
- `COMPLETED` means "the outcome shipped". Open tasks under the
  project are leftover work; the cleanest model is to mark them
  completed so they fall out of the active backlog and stop showing
  up in dashboards and statistics.

Both transitions are status changes that the user has to apply
manually today, which is tedious and error-prone for projects with
many tasks.

## Decision

When `update_project` transitions a project's status to either
`CANCELED` or `COMPLETED`, cascade the transition to every task that
currently points at the project and is not already in a terminal
state. Concretely:

1. `update_project(uid, ProjectUpdate(status=CANCELED))` walks the
   task list returned by `list_tasks(project_id=uid)`. For each task
   whose status is `NEEDS_ACTION` or `IN_PROGRESS`, the facade
   dispatches `update_task(task.uid, TaskUpdate(status=CANCELED))`.
   The single-task update already populates `completed_at` from
   `now_utc()` for COMPLETED transitions (per the existing rule in
   `src/genesis/caldav/tasks.py`); the cascade relies on the same
   path, so `completed_at` is set consistently.
2. `update_project(uid, ProjectUpdate(status=COMPLETED))` does the
   same for the COMPLETED transition.
3. Tasks already in a terminal state (`CANCELED` or `COMPLETED`) are
   left alone — the cascade is a no-op for them.
4. Tasks not pointing at this project are untouched (already true
   because the cascade is scoped via `list_tasks(project_id=uid)`).

### Where the cascade lives

`update_project` in `src/genesis/caldav/projects.py` already calls
into the tasks module (`_clear_project_id_on_tasks` for the delete
case). Adding a status cascade here keeps the project-side concerns
on the project side and lets `update_task` own the per-task
serialization rules. The cascade runs after the project's own
`_populate` + `obj.save()` so the project's new status is durable
before the tasks flip.

### Mock parity

`tests/mock_caldav.py` mirrors `update_project` to also cascade on
status changes, so unit tests exercise the same observable behavior
as the real facade.

### API wire

The existing `PATCH /productivity/projects/{uid}` handler already
maps `status` into the entity `ProjectUpdate`. The cascade is purely
a backend behavior change — the wire contract is unchanged, so
existing API consumers are unaffected.

## Status

Proposed.

## Consequences

- Open tasks under a canceled or completed project stop appearing
  in the global Tasks page, dashboard active-assignments list,
  calendar "this week" view, and focus task dropdown. They become
  invisible in active views without a status filter.
- Completed cascading sets `completed_at` on each affected task via
  `update_task`, so time-period queries (`completed_at_start` /
  `completed_at_end`) include them as expected.
- One project update can now produce many VTODO writes (one per
  open task). For a project with hundreds of tasks this is more
  HTTP traffic than a single PUT, but the operations are sequential
  within one request and the user is gated on the response. No
  parallelism is added; per-task writes already exist in the
  delete-project path (`_clear_project_id_on_tasks`) and the
  performance profile matches that precedent.
- A user who wants to leave a few tasks open even though the
  project is "completed" loses the easy path to do so: they would
  need to mark the project completed first, then re-open the
  tasks. This is intentional — the project terminal state is a
  commitment that all open work either shipped or was abandoned.
- Tests: extend `tests/test_caldav_unit.py` `TestProjectCRUD` with
  cases for cascading to NEEDS_ACTION, IN_PROGRESS, no-op on
  already-terminal tasks, and the COMPLETED vs CANCELED branches.
  Add an integration case to `tests/test_api.py` that PATCHes a
  project's status and asserts the affected task's status on the
  wire.