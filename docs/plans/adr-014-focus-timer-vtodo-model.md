# Plan for adr-014

## Steps

1. Rewrite `src/genesis/entities/time_log.py` to the new VTODO model:
   add `TimeLogStatus` and `TimeLogMode` enums; new fields
   (`label`, `due`, `completed_at`, `status`, `mode`); make
   `task_id` optional on `TimeLogEntry` and `TimeLogEntryCreate`.
2. Update `src/genesis/entities/__init__.py` to export
   `TimeLogStatus` and `TimeLogMode`.
3. Rewrite `src/genesis/caldav/ical.py` `time_log_to_ical` to build
   `icalendar.Todo()` with `DTSTART`/`DUE`/`STATUS`/`COMPLETED`/
   `X-MODE`/`X-TASK-ID`/`SUMMARY`. Rewrite `time_log_from_ical`
   symmetrically.
4. Update `src/genesis/caldav/time_log.py`:
   - `create_time_log_entry` accepts new fields, calls
     `save_todo` instead of `save_event`.
   - `list_time_log` gains `active_only: bool = False` and
     `status: TimeLogStatus | None = None` parameters; default sort
     `(start desc, uid desc)` for completed, `(start asc, uid asc)`
     for active.
   - `update_time_log_entry` becomes the natural stop path; expose
     `stop_time_log_entry(uid, *, actual_due, completed_at,
     if_match_etag)` as a thin wrapper that fetches, mutates, and
     PUTs with If-Match via `obj.save()`.
5. Update `src/genesis/caldav/facade.py`: expose `stop_time_log_entry`
   and the new `list_time_log` parameters.
6. Update `tests/mock_caldav.py`: rewrite the time_log storage to
   use VTODO and the new fields; reimplement
   `stop_time_log_entry` with the new mock.
7. Update `tests/test_caldav.py` time_log tests for the new schema;
   add tests for `active_only=True`, status filter, and
   `stop_time_log_entry` semantics (countdown auto-stop, manual
   stop, concurrent stop race).
8. Update `tests/test_caldav_unit.py` time_log tests symmetrically.
9. Wipe `~/.local/share/genesis/default/radicale/collections/collection-root/default/time_log/`
   on local Radicale.
10. Add `src/genesis/ui/pages/focus.py` with start-session form and
    `@st.fragment(run_every="1s")` for live active-session display.
11. Update `src/genesis/ui/app.py` to add the Focus page.
12. Verify: `make lint`, `make typecheck`, `make test`,
    `make test-integration`, and a manual smoke run of the Focus
    page (start a count-up, stop it; start a countdown, watch it
    auto-stop).
13. Commit on a feature branch off `master`.
