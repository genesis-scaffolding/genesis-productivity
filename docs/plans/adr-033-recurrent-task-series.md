# Plan for adr-033

## Steps

1. **`src/genesis/entities/recurrence.py`**
   - Add the recurrence-frequency, anchor, rule, series-template, and
     series-status models.
   - Validate a positive interval, an explicit anchor field, and a
     materialized-future count from 1 through 100.
   - Store the effective per-series materialized-future count and the
     highest materialized occurrence index.
   - Add `end_date` and `stop_reason` to the series record.
   - Add `SERIES_OWNED_FIELDS` (the five fields the series owns) and
     the role literals.
   - Add `SeriesSummary`, `SeriesUpdate`, `SeriesUpdateResult`, and
     `CancelSeriesResult`; drop `RecurrenceRuleUpdate`.
   - Add `occurrence_role()` and `series_status()` so the API and the
     UI read the role and the status of a series the same way.

2. **`src/genesis/entities/task.py` and `src/genesis/entities/__init__.py`**
   - Add the series UID, occurrence date, and occurrence index to
     `Task`, `TaskCreate`, and `TaskUpdate` where appropriate.
   - Export the new recurrence models with the existing entity models.

3. **`src/genesis/caldav/constants.py`, `src/genesis/caldav/facade.py`, and `src/genesis/caldav/ical.py`**
   - Add the isolated `recurrences` collection and facade surface.
   - Serialize and parse the series record and the task instance's
     recurrence metadata.
   - Keep recurrence metadata out of the ordinary task collection.
   - Add the optional `X-RECURRENCE-END-DATE` and
     `X-RECURRENCE-STOP-REASON` properties to the series record. A
     record without them still parses.

4. **`src/genesis/caldav/recurrence.py`**
   - Implement the v1 daily, weekly, monthly, and yearly date arithmetic.
   - Implement idempotent series creation, an N-occurrence rolling
     materialization window, the series edit, the series
     cancellation, and runtime horizon resizing.
   - Use `(series_uid, recurrence_index)` as the logical occurrence
     identity so retries resume at the first missing index.
   - Derive a stable `t-` UID from that identity so concurrent or
     retried writers use the same CalDAV resource instead of creating
     duplicate occurrence objects.
   - Enforce the ownership rule: the five series-owned fields of an
     occurrence are read-only.
   - Reconcile project cancellation/deletion with active series state.

5. **`src/genesis/caldav/tasks.py` and `src/genesis/caldav/facade.py`**
   - Expose recurrent creation, the series edit, the series
     cancellation, the series listing, series filtering, series
     inspection, and runtime horizon resizing through the CalDAV
     facade.
   - Reconcile shadow VEVENTs for every materialized occurrence and for
     futures cancelled by a horizon reduction.
   - Guard the task write path against a series-owned change.

6. **`src/genesis/api/schemas.py` and `src/genesis/api/routers/productivity.py`**
   - Add the optional recurrence create payload, the task recurrence
     output, the series filter, the series list, series inspection,
     the series update, and the series cancellation.
   - Enrich task responses by fetching each distinct series once per
     response, not once per task.
   - Map an invalid anchor, an invalid horizon count, an invalid end
     date, and a change to a series-owned field to explicit HTTP
     errors.

7. **`src/genesis/ui/_render.py` and `src/genesis/ui/_table.py`**
   - Add recurrence controls, including the materialized-future count,
     to task creation.
   - Make the task edit dialog an occurrence-only editor: the
     series-owned fields are disabled, and the dialog shows a
     read-only summary of the series.
   - Add the series form and the series cancellation confirmation.
   - Show the recurrence rule, effective horizon, occurrence role, and
     series UID in the task table.
   - Add the read-only recurrence series table with its two row
     actions.

8. **`src/genesis/ui/pages/tasks.py`**
   - Render the recurrence series table under the task table.
   - Show the read-only series summary on the task detail page.

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
      the series edit, the series cancellation, the ownership guard, and
      the delete-without-refill behavior used by the real facade.

12. **Tests**
    - Add unit coverage for date arithmetic, N=1 and N>1 windows, the
      ownership guard, the instance edit, the series edit, the end
      date, the rolling renewal, partial retry idempotency, horizon
      expansion and reduction, the cancellation, and history
      retention.
    - Add API contract coverage for global-default inheritance,
      per-series overrides, the series listing and inspection, the
      series edit, the series cancellation, the ownership guard, the
      occurrence delete, and the error cases.
    - Add real-Radicale integration coverage for series persistence,
      N-occurrence windows, occurrence metadata, the end date, the
      series edit, and shadow events.
    - Add headless Streamlit coverage for the creation controls, the
      occurrence edit, the series table, the series form, the series
      cancellation, the refused inline edit, and the delete
      confirmation.

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
- **The ownership rule is one pure function.** `series_owned_changes`
  compares the *effective* new value of a field (after the `clear_*`
  flags) with the current value, so a client that sends a full object
  unchanged is not rejected. The guard lives in the facade's
  `update_task`, the single task write path, so the API, the UI, and
  the core facade all get the same refusal (409 at the API).
- **The project cascade bypasses the guard on purpose.** The cascade
  writes the occurrences through the module function, not through the
  guarded facade method, so the occurrences and the template stay
  consistent with each other.
- **The delete path does not refill the window.** `reconcile_series`
  takes a `refill` flag. The delete path calls it with
  `refill=False`: the deleted index is skipped for good, the window
  is one smaller until the current occurrence completes, and the
  natural renewal restores it. The index is never reused because the
  highest materialized index only grows.
- **A series finishes when it has no open occurrence left.** The
  end date caps the materialized window; it does not finish the
  series by itself. A series whose occurrences are all terminal is
  *finished*: it stops renewing but it is not cancelled, and it can
  be updated again.
- **Reactivating a finished series needs a schedule change.** The
  series comes back when the rule, the anchor, the start date, or the
  end date changed *and* the new configuration allows another
  occurrence. A content-only edit (a new title) leaves the series
  finished. When the series had no open occurrence left, the next
  occurrence becomes the new current one.
- **The end date is inclusive.** An occurrence may start on the end
  date; the one after it is not materialized. Shortening the end date
  cancels the open occurrences that fall after it.
- **A series content edit rewrites the open occurrences.** The
  template and every open occurrence take the new content. An
  individual content edit of an open occurrence is therefore lost
  when the series content is edited later. The series form states
  this before saving.
- **The series table is read-only.** The table is not an inline
  editor: it carries the two row actions ("Edit" and "Cancel
  series") and nothing else. The series form is the only editor.
- **The status column has a fourth value.** "Cancelled" (the series
  was cancelled), "Finished" (it ran out of occurrences), "Active",
  and "Inactive" (something else stopped it, a cancelled project for
  instance).
- **The task edit dialog has no delete button.** Streamlit forbids
  nested dialogs, so the delete confirmation cannot open from inside
  the edit dialog. The confirmation is reachable from the task table
  and the task detail page, and it states what the deletion does to
  the series and offers the series cancellation.
- **The occurrence edit sends no series-owned field.** The disabled
  widgets are not part of the update payload at all, so an
  occurrence edit can never trip the guard.
- **The inline edit helper is scope-free.** `apply_inline_task_edit`
  applies one edit and returns the refusal message; there is no
  scope, no scope control, and no fallback to another scope.
- **The create dialog has a "Recurring task" switch.** Ordinary task
  creation stays one click; the recurrence controls appear when the
  switch is on.
- **One place resolves the materialized horizon.** The core facade
  (`Facade.create_recurrent_task`) reads
  `configs.recurrence.default_materialized_future_count` when the
  request does not carry an explicit count. The API and the UI both
  go through it.
- **The idempotency guarantee is occurrence-level.** The deterministic
  occurrence UID and the logical-index check make a materialization
  resumable: a retry after a partial write creates only the missing
  indexes, and two writers cannot materialize one index twice. A
  per-series mutation lock and an ETag-protected series update are
  *not* implemented; two writers that change the series record at the
  same time can still overwrite each other. ADR-033 §3 records the
  same limitation.
- **Deleting a project clears the series templates.** The series
  stays active, but its template no longer points at the deleted
  project, so the next materialized occurrence is not created with a
  dangling project id. Cancelling a project deactivates the series
  before the cascade cancels its occurrences.
- **A DST-aware local conversion** (`genesis.tz.to_utc_walltime`)
  rebuilds the scheduled time of a future occurrence, so a scheduled
  time keeps its local wall-clock meaning across a daylight-saving
  transition.
- **The series inspection reports the template.** The response of
  `GET /recurrences/{uid}` carries the future template, so a client
  can understand a future edit without guessing.
