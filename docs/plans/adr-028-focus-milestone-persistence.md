# Plan for adr-028

## Steps

1. Add `last_milestone_minutes: int = 0` to `TimeLogEntry`,
   `TimeLogEntryCreate`, and `TimeLogEntryUpdate` in
   `src/genesis/entities/time_log.py`. Document the field in
   the class docstring alongside the other iCal-mapped
   fields.

2. Update `src/genesis/caldav/ical.py`:
   - In `time_log_from_ical`, read
     `component.get("x-last-milestone-minutes")` and coerce to
     `int`, defaulting to `0` when the property is absent.
   - In `time_log_to_ical`, add
     `component.add("x-last-milestone-minutes", entry.last_milestone_minutes)`
     when `last_milestone_minutes > 0` (omit when `0` to keep
     the VTODO lean for fresh sessions, matching the
     X-CATEGORY convention).
   - In `time_log._populate`, add an `set_or_delete` entry for
     `x-last-milestone-minutes` so in-place updates preserve
     the field.

3. Update `src/genesis/caldav/time_log.py`:
   - `update_time_log_entry` already iterates `data.model_dump(exclude_unset=True)`,
     so the new field is wired automatically. Confirm the
     `_populate` change makes the round-trip work.
   - `stop_time_log_entry` writes the whole entry through
     `_populate`, so the field is preserved on stop. No
     change needed.

4. Update `tests/mock_caldav.py` `MockCalDAVFacade.update_time_log_entry`
   to handle the new field (setattr loop already covers it;
   confirm).

5. Rewrite `_maybe_fire_milestone` in
   `src/genesis/ui/pages/focus.py`:
   - Compute `elapsed_minutes = int((now - s.start).total_seconds() // 60)`.
   - Read `last = s.last_milestone_minutes`.
   - Compute `crossings = list(range(last + _MILESTONE_MINUTES,
     elapsed_minutes + 1, _MILESTONE_MINUTES))`. Empty list
     means no new crossings; return.
   - `new_last = crossings[-1]`.
   - Persist via `facade.caldav.update_time_log_entry(s.uid,
     TimeLogEntryUpdate(last_milestone_minutes=new_last))`.
   - On `None` (PUT failed): refetch via
     `caldav.get_time_log_entry(s.uid)`. If refetched exists
     and `refetched.last_milestone_minutes >= new_last`,
     return silently (other device won the race). Otherwise
     return silently (transient failure; next tick retries).
   - On success: update
     `st.session_state[f"focus_notify_max_{s.uid}"] =
     max(current, new_last)` so subsequent ticks in the same
     page load skip the PUT.
   - Fire `_notify_safely` for each crossing in order, using
     the existing `messages.session_milestone` builder with
     `task_title=_task_title_for_session(s)`.
   - Remove the old `fired_key = f"focus_notify_milestones_{s.uid}"`
     set-based logic; the durable field plus the new
     `fired_max` int cache replace it.

6. Update `src/genesis/ui/pages/focus.py` imports: add
   `TimeLogEntryUpdate` to the existing
   `from genesis.entities import ...` line.

7. Tests in `tests/test_caldav_round_trip.py`:
   - Add `test_time_log_round_trip_preserves_last_milestone_minutes`:
     build a `TimeLogEntry` with `last_milestone_minutes = 2`,
     round-trip through `time_log_to_ical` and
     `time_log_from_ical`, assert the field comes back equal.
   - Add `test_time_log_round_trip_legacy_entry_defaults_milestone_minutes_to_zero`:
     build a legacy VTODO without
     `X-LAST-MILESTONE-MINUTES` (reuse the
     `_build_legacy_vtodo` helper or build a similar one),
     assert `time_log_from_ical(...).last_milestone_minutes == 0`.
   - Add `test_time_log_round_trip_zero_milestone_minutes_omits_property`:
     build a `TimeLogEntry` with `last_milestone_minutes = 0`,
     assert `X-LAST-MILESTONE-MINUTES` is not present on the
     emitted component.

8. Add unit test for `_maybe_fire_milestone`:
   - New file `tests/test_focus_milestone_unit.py` (or extend
     an existing test module if a natural fit exists; check
     before adding a new file).
   - Use a minimal fake facade / caldav that records PUTs and
     captures notify calls. Drive the function directly with
     constructed `TimeLogEntry` values at known
     `last_milestone_minutes` and known `now - s.start`
     offsets. Assert: notify is called the expected number of
     times, the PUT writes the expected
     `last_milestone_minutes`, refetch-and-skip-on-412 is
     exercised, and the steady-state `st.session_state` cache
     suppresses redundant PUTs.
   - The current test suite does not exercise the focus page
     directly (no `test_focus.py`). Keep the new test focused
     on the pure `_maybe_fire_milestone` logic; do not try to
     spin up the Streamlit runtime.

9. Update `docs/adr/adr-027-ntfy-notifications.md`:
   - In the "Focus page integration" section, replace the
     paragraph that describes the
     `st.session_state[f"focus_notify_milestones_{s.uid}"]`
     set with a one-line note that the durable field is
     defined in ADR-028. Keeps the two ADRs cross-referenced.

10. Run `make format`, `make lint`, `make typecheck`,
    `make test`.

## File map

New files:

- `docs/adr/adr-028-focus-milestone-persistence.md`
- `docs/plans/adr-028-focus-milestone-persistence.md`
- `tests/test_focus_milestone_unit.py`

Modified files:

- `src/genesis/entities/time_log.py` (new field on three
  models)
- `src/genesis/caldav/ical.py` (round-trip the new X-
  property)
- `src/genesis/caldav/time_log.py` (`_populate` entry; other
  functions unchanged)
- `src/genesis/ui/pages/focus.py` (rewrite
  `_maybe_fire_milestone`, update imports)
- `tests/mock_caldav.py` (verify field flows through the
  update loop)
- `tests/test_caldav_round_trip.py` (three new round-trip
  tests)
- `docs/adr/adr-027-ntfy-notifications.md` (cross-reference
  update)

No new dependencies.
