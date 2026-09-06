# Plan for adr-012

## Steps

### Entities

1. Edit `src/genesis/entities/task.py`. Add `scheduled_duration: int | None = None` and `priority: int | None = None` to `Task`, `TaskCreate`, and `TaskUpdate`.
2. Edit `src/genesis/entities/project.py`. No new fields. Confirm `ProjectStatus` enum matches ADR (`needs-action` / `in-progress` / `completed` / `canceled`).
3. Create `src/genesis/entities/event.py`. New `Event`, `EventCreate`, `EventUpdate`, `EventStatus` enum (`tentative` / `confirmed` / `canceled`).
4. Create `src/genesis/entities/time_log.py`. New `TimeLogEntry`, `TimeLogEntryCreate`, `TimeLogEntryUpdate`.
5. Edit `src/genesis/entities/__init__.py`. Re-export new entities.

### Configs

6. Edit `src/genesis/configs.py`. Remove `task_calendar` and `project_calendar_prefix` fields from `DAV`. Add no replacements; collection names become constants inside the CalDAV package.

### CalDAV package rewrite

7. Create `src/genesis/caldav/constants.py`. Module-level constants: `PROJECTS_COLLECTION = "projects"`, `TASKS_COLLECTION = "tasks"`, `EVENTS_COLLECTION = "events"`, `TIME_LOG_COLLECTION = "time_log"`, `DEFAULT_SHADOW_DURATION_MINUTES = 30`.
8. Create `src/genesis/caldav/ical.py`. Shared ical helpers: `to_str`, `to_datetime`, `to_date`, `escape_text`, `unescape_text`, plus per-entity `task_from_ical`, `task_to_ical`, `project_from_ical`, `project_to_ical`, `event_from_ical`, `event_to_ical`, `time_log_from_ical`, `time_log_to_ical`. Each function returns the entity or accepts an entity and returns the ical string.
9. Create `src/genesis/caldav/_calendar.py`. Helper `_get_or_create_calendar(principal, name)` and a small `_CalendarCache` that holds the four calendar references per facade instance.
10. Create `src/genesis/caldav/projects.py`. `ProjectsMixin` with `get_project`, `list_projects`, `create_project`, `update_project`, `delete_project`. Uses in-place save (load object, mutate ical, `save()`).
11. Create `src/genesis/caldav/tasks.py`. `TasksMixin` with `get_task`, `list_tasks`, `create_task`, `update_task`, `delete_task`. Includes the shadow VEVENT logic: when `scheduled_start` is set, write or update the linked VEVENT in the `events` collection in the same logical operation; when `scheduled_start` is cleared, delete the shadow.
12. Create `src/genesis/caldav/events.py`. `EventsMixin` with `get_event`, `list_events`, `create_event`, `update_event`, `delete_event`. Shadow events (`task_id` set) are written only by `TasksMixin`; `EventsMixin` is the read/update surface and refuses direct mutation of `task_id` on shadow events (caller goes through `update_task`).
13. Create `src/genesis/caldav/time_log.py`. `TimeLogMixin` with `get_time_log_entry`, `list_time_log`, `create_time_log_entry`, `update_time_log_entry`, `delete_time_log_entry`. Mutable per ADR.
14. Rewrite `src/genesis/caldav/facade.py`. `CalDAVFacade` becomes thin: holds the principal and the four calendar references, inherits from the four mixins, exposes a `close()` method. Remove the per-project calendar code entirely.

### Facade

15. Edit `src/genesis/core/facade.py`. Update `CalDAVFacade` constructor call to remove `task_calendar_name` and `project_calendar_prefix` args.

### Mock backend

16. Rewrite `tests/mock_caldav.py`. Mirror the four-collection model. Four dicts keyed by UID. `create_task` with `scheduled_start` populates a parallel shadow entry in the events dict. `update_task` keeps shadow in sync. `delete_task` removes shadow too. Add mock CRUD for events and time_log.
17. Edit `tests/test_caldav_unit.py`. Update existing tests for new entity fields (`priority`, `scheduled_duration`). Add tests for shadow VEVENT creation, update, deletion.
18. Edit `tests/test_caldav.py`. Mirror the same updates for integration tests. Add an integration test that verifies the shadow VEVENT actually appears in Radicale storage with the correct UID and FK.
19. Add a regression test (in `tests/test_caldav_unit.py`) that asserts `update_task` no longer delete-then-creates iCal objects. Pattern: instrument the mock or use a counter; assert the operation path is mutate-then-save, not delete + add.

### Housekeeping

20. Edit `docs/adr/adr-008-caldav-storage.md`. Change `## Status` from `Accepted` to `Superseded by adr-012`. Keep the file.
21. Edit `docs/adr/adr-012-time-layer-caldav-model.md`. Change `## Status` from `Proposed` to `Accepted` once this plan is approved and implementation is green.

## Test plan

- Unit tests (`make test`): all existing tests still pass after the mock-backend rewrite; new tests for `priority`, `scheduled_duration`, shadow VEVENT create/update/delete, and the no-delete-on-update regression test.
- Integration tests (`make test-integration`): round-trip each entity through Radicale; assert shadow VEVENT appears with the same UID in the `events` collection after a task write; assert atomicity (no half-state if the second write fails).
- Lint (`make lint`) and typecheck (`make typecheck`) clean.

## Migration

No migration. Existing `project:<uid>` calendars and any VTODOs in `tasks` are abandoned. Users start with the four empty collections. This is documented in the ADR and in the commit message.

## Out of scope for this plan

- CardDAV (separate ADR when implemented)
- Per-project markdown files (separate ADR when implemented)
- ETag-aware save at the call-site level (the ADR delegates this to the library; no explicit try/retry loop in the facade)
- Streamlit UI surfaces for Event and TimeLogEntry (UI is a stub; the new entities exist in the facade and round-trip through CalDAV, but no UI is built for them this round)
