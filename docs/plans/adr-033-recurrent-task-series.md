# Plan for adr-033

## Steps

1. **`src/genesis/entities/recurrence.py`**
   - Add the recurrence-frequency, anchor, rule, series-template, and
     series-status models.
   - Validate a positive interval, an explicit anchor field, and a
     materialized-future count from 1 through 100.
   - Store the effective per-series materialized-future count and the
     highest materialized occurrence index.

2. **`src/genesis/entities/task.py` and `src/genesis/entities/__init__.py`**
   - Add the series UID, occurrence date, and occurrence index to
     `Task`, `TaskCreate`, and `TaskUpdate` where appropriate.
   - Export the new recurrence models with the existing entity models.

3. **`src/genesis/caldav/constants.py`, `src/genesis/caldav/facade.py`, and `src/genesis/caldav/ical.py`**
   - Add the isolated `recurrences` collection and facade surface.
   - Serialize and parse the series record and the task instance's
     recurrence metadata.
   - Keep recurrence metadata out of the ordinary task collection.

4. **`src/genesis/caldav/recurrence.py`**
   - Implement the v1 daily, weekly, monthly, and yearly date arithmetic.
   - Implement idempotent series creation, an N-occurrence rolling
     materialization window, current-only update, all-future update,
     future cancellation, and runtime horizon resizing.
   - Use `(series_uid, recurrence_index)` as the logical occurrence
     identity so retries resume at the first missing index.
   - Derive a stable `t-` UID from that identity so concurrent or
     retried writers use the same CalDAV resource instead of creating
     duplicate occurrence objects.
   - Reconcile project cancellation/deletion with active series state.

5. **`src/genesis/caldav/tasks.py` and `src/genesis/caldav/facade.py`**
   - Expose recurrent creation, scoped update, future cancellation,
     series filtering, series inspection, and runtime horizon resizing
     through the CalDAV facade.
   - Reconcile shadow VEVENTs for every materialized occurrence and for
     futures cancelled by a horizon reduction.

6. **`src/genesis/api/schemas.py` and `src/genesis/api/routers/productivity.py`**
   - Add the optional recurrence create/update payload, task recurrence
     output, edit scope, affected-UID response, series filter,
     series-inspection endpoint, series-horizon update endpoint, and
     cancel-future endpoint.
   - Enrich task responses by fetching each distinct series once per
     response, not once per task.
   - Map invalid anchors, invalid horizon counts, non-recurrent scope
     requests, and invalid future-scope status changes to explicit HTTP
     errors.

7. **`src/genesis/ui/_render.py` and `src/genesis/ui/_table.py`**
   - Add recurrence controls, including the materialized-future count,
     to task creation.
   - Add the current versus all-future choice to task editing.
   - Add a series inspection section and a control for changing the
     materialized-future count on a running series.
   - Add the confirmed cancel-future action.
   - Make inline edits of recurring tasks use an explicit table scope,
     while keeping status transitions current-occurrence only.
   - Show the recurrence rule, effective horizon, occurrence role, and
     series UID in the task table.

8. **`src/genesis/ui/pages/tasks.py`**
   - Surface recurrence metadata and the current/future relationship in
     the task page without changing the existing dashboard or calendar
     period-window semantics.

9. **`src/genesis/configs.py`, `src/genesis/core/facade.py`, and `src/genesis/ui/pages/settings.py`**
   - Add the per-user global default materialized-future count, default
     1, with YAML and environment override support.
   - Pass the resolved global default into recurrent task creation.
   - Show the current global default and configuration instructions in
     Settings without adding a new config-writing convention.

10. **`src/genesis/backup/manifest.py` and `src/genesis/backup/facade.py`**
    - Add the optional recurrence-series count and validate series VTODO
      samples during backup validation.

11. **`tests/mock_caldav.py`**
    - Mirror the series store, configurable horizon, recurrence creation,
      scoped update, N-occurrence materialization, resizing, and
      cancellation behavior used by the real facade.

12. **Tests**
    - Add unit coverage for date arithmetic, N=1 and N>1 windows,
      current-only versus all-future edits, rolling renewal, partial
      retry idempotency, horizon expansion and reduction, cancellation,
      and history retention.
    - Add API contract coverage for global-default inheritance,
      per-series overrides, series inspection and resizing, recurrent
      creation, series listing, both edit scopes, cancellation, and
      error cases.
    - Add real-Radicale integration coverage for series persistence,
      N-occurrence windows, occurrence metadata, and shadow events.
    - Add headless Streamlit coverage for creation controls, horizon
      inspection and resizing, scoped saving, inline scope handling,
      and cancel confirmation.

13. **Documentation**
    - Keep ADR-033 and this plan linked from the affected module
      docstrings.
    - Document the API contract, global default, and separate CLI
      follow-up.

14. **Verify**
    - Run `make lint`.
    - Run `make typecheck`.
    - Run `make test`.
    - Run `make test-integration`.
    - Run `make test-all` when the focused suites pass.

## Implementation notes

The implementation follows the steps above. The points below record
the decisions the implementation made where the plan left room, and
the places where the code differs from the wording of the plan.

- **Shared engine, two stores.** The recurrence logic lives in
  `src/genesis/caldav/recurrence.py` behind a `RecurrenceStore`
  abstract base class. The CalDAV-backed store
  (`CalDAVRecurrenceStore`) and the in-memory store in
  `tests/mock_caldav.py` implement the same contract, so both
  backends run the same engine and cannot drift apart.
- **Date arithmetic in the domain layer.** `occurrence_date`,
  `add_months`, and `add_years` live in
  `src/genesis/entities/recurrence.py` so the UI can preview the next
  occurrence without importing the storage layer.
- **Series template as one JSON property.** The template has a dozen
  fields; the series record stores it as one
  `X-RECURRENCE-TEMPLATE` JSON property instead of a dozen flat
  `X-` properties.
- **The current occurrence is tracked explicitly.** The series stores
  `current_index`. It moves forward when the current occurrence
  becomes terminal and never moves back, so reopening an old
  occurrence does not rewind the chain.
- **Re-anchoring a future-scope edit.** The chain anchor date shifts
  by the day delta the edit applied to the anchored field, which
  keeps the monthly clamping day of the series. Changing the anchored
  field (rather than its value) re-anchors the chain at the selected
  occurrence. The occurrence indexes stay stable so the
  deterministic occurrence UIDs keep pointing at the same logical
  occurrences.
- **The create dialog has a "Recurring task" switch.** Ordinary task
  creation stays one click; the recurrence controls appear when the
  switch is on.
- **A status change is per-occurrence.** In the all-future scope the
  UI applies the status to the selected occurrence on its own; the
  API rejects a future-scope request that carries a status.
- **Project cancellation deactivates the series first**, before the
  cascade cancels the occurrences, so no replacement occurrence is
  materialized over cancelled work.
- **A DST-aware local conversion** (`genesis.tz.to_utc_walltime`)
  rebuilds the scheduled time of a future occurrence, so a scheduled
  time keeps its local wall-clock meaning across a daylight-saving
  transition.

