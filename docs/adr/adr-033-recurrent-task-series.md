# 033. Recurrent tasks as materialized series instances

## Context

Genesis has no recurrence support today. A task is one VTODO with one
UID, one status, one `completed_at`, and one set of dates. The recurring
use cases are rent, health insurance, and recurring house work. Those are
currently represented by manually cloned future tasks, for example
several separate "Pay rent" tasks.

The feature needs four first-class behaviors:

1. A recurrent task can be created from the Streamlit UI and from the
   FastAPI API.
2. An edit can target only the selected occurrence or all later
   occurrences in the series.
3. The future can be cancelled without deleting completed history.
4. Both the UI and the API expose the same scope and cancellation
   operations.

The number of future occurrences kept ready must also be configurable.
The initial default is one, but the same model must support keeping
four or another practical number of future tasks visible without
changing the recurrence architecture.

The existing task model and query machinery make the design constraints
specific:

- `Task` is keyed by a unique UID. `TaskOut` exposes one status and one
  `completed_at`.
- Dashboard and API period windows filter ordinary task fields:
  `assigned_date`, `hard_deadline`, `scheduled_start`, and
  `completed_at`.
- The Streamlit task table edits a concrete task UID.
- CalDAV clients can see the VTODOs, but Radicale stores recurrence
  metadata; it does not expand a recurrence set into independent task
  records for the application.
- A completed task can have a shadow VEVENT when it has a
  `scheduled_start`.

The obvious iCalendar alternative is to put an RFC 5545 `RRULE` on one
VTODO. That does not fit this application. One recurring VTODO still has
one UID and one status, so it cannot hold an independent completion
timestamp for every occurrence. Recurrence exceptions and future-range
edits would also require recurrence-aware reads and writes throughout
the current CRUD path. The period-window queries would no longer be
queries over ordinary dated task records.

The app therefore needs materialized occurrences. The architectural
question is where the recurrence rule and the future-task template
live, and how an operation identifies the selected occurrence relative
to the rest of its series.

## Decision

### 1. A series record plus ordinary task instances

A recurrent task is a series of ordinary task records. Each occurrence
is a normal `Task` with a unique `t-` UID. It gains three structured
fields:

- `recurrence_series_uid`: the UID of the series it belongs to, or
  `None` for an ordinary task.
- `recurrence_occurrence`: the local calendar date of that occurrence,
  or `None` for an ordinary task.
- `recurrence_index`: the zero-based occurrence number in the series, or
  `None` for an ordinary task.

The series itself is a separate domain record, `RecurrenceSeries`. It
stores:

- the recurrence rule;
- the anchor field and first occurrence date;
- the template for future occurrences;
- the number of future occurrences to keep materialized;
- the highest occurrence index materialized so far;
- whether the series is active;
- created and modified timestamps.

The series record is stored in a new isolated CalDAV collection named
`recurrences`. It is a VTODO because that is the task-shaped primitive
supported by the current caldav client, but it is not in the `tasks`
collection and is never returned as a user task. The series UID uses a
new `r-` typed prefix, following ADR-016.

This adds a sixth top-level collection to the current five. ADR-012's
fixed-collection rule is superseded for this record type. Raw backup and
restore already archive the complete per-user Radicale subtree, so the
new collection is included without a separate backup format.

The series record uses custom `X-` properties for its recurrence
metadata. The task instances use `X-RECURRENCE-SERIES-ID`,
`X-RECURRENCE-OCCURRENCE`, and `X-RECURRENCE-INDEX`. The series
record carries `X-RECURRENCE-FREQUENCY`, `X-RECURRENCE-INTERVAL`,
`X-RECURRENCE-ANCHOR`, `X-RECURRENCE-START-DATE`,
`X-RECURRENCE-FUTURE-COUNT`, `X-RECURRENCE-HIGHEST-INDEX`,
`X-RECURRENCE-CURRENT-INDEX`, and `X-RECURRENCE-ACTIVE`, plus one
JSON property holding the future template. We do not write an RFC 5545
`RRULE`. The series is an application construct with materialized
occurrences, not a single iCalendar recurrence set.

### 2. A small, explicit v1 rule

The first recurrence rule supports:

- `frequency`: `daily`, `weekly`, `monthly`, or `yearly`;
- `interval`: an integer of at least 1;
- `anchor`: `assigned_date`, `hard_deadline`, or `scheduled_start`.

The selected anchor must exist on the task. Its value is the first
occurrence date. The other date fields move by the same recurrence
period and keep their relative offsets and times of day. Monthly and
yearly arithmetic clamps an invalid day to the last day of the target
month. Occurrence dates are computed from the series anchor and the
occurrence index, not by repeatedly advancing the previous clamped
value. A January 31 monthly series therefore runs January 31, February
28 (or 29), and March 31, rather than drifting to March 28. A scheduled
time keeps its local wall-clock time and is converted back to UTC, so a
daylight-saving transition does not move the task to a different local
time.

The rule has no count or end date in v1. The explicit "cancel future"
operation ends a series. Weekday lists, month-position rules, and
exception dates are deferred rather than implied by a partly understood
RRULE string.

### 3. A configurable materialization horizon

The number of future occurrences kept materialized ahead of the current
occurrence is a first-class series property. Call it
`materialized_future_count`, or N. N must be at least 1 and is capped
at 100 so a malformed request cannot request an unbounded number of
CalDAV writes. The default is 1.

There are two configuration levels:

- `RecurrenceConfig.default_materialized_future_count` is a per-user
  global default in `configs.yaml`. It defaults to 1. The environment
  override is
  `GENESIS__RECURRENCE__DEFAULT_MATERIALIZED_FUTURE_COUNT`.
- `RecurrenceSeries.materialized_future_count` is the effective,
  persisted per-series value. A newly created series inherits the
  global default when the request does not specify a count. Changing
  the global default later does not rewrite existing series.

A running series can change its own N through the UI or API. Increasing
N materializes the missing future occurrences at the tail of the
window. Decreasing N keeps the current occurrence and the nearest
future occurrences and marks the farthest extra future occurrences
`CANCELED`; it does not delete them. Shrinking and later expanding the
series does not resurrect cancelled occurrence indexes. It creates later
occurrences instead.

Creating a recurrent task creates the series, the current occurrence,
and N future occurrences. Future occurrences are real tasks with real
dates, so they are immediately visible to the task list, dashboard,
calendar, and existing API period windows. There are N open future
occurrences materialized ahead of the current occurrence.

When the current occurrence becomes terminal (`COMPLETED` or
`CANCELED`), the earliest remaining open future occurrence becomes the
current occurrence and the facade materializes enough new occurrences
at the tail to restore N open futures. A deleted current occurrence is
treated as a skipped occurrence. Reopening a completed occurrence does
not rewind the already materialized chain.

Renewal is driven by the Genesis create/update path, not by a background
scheduler. Materialization uses the logical identity
`(series_uid, recurrence_index)` and the series' highest materialized
index. A recurrence occurrence has a deterministic `t-` UID derived
from that logical identity. It keeps ADR-016's `t-` plus six-character
base36 shape, but it is a get-or-create key rather than a random UID.
The CalDAV resource path is therefore stable for a logical occurrence,
so concurrent or retried writers update the same object instead of
creating duplicates.

Before creating an occurrence, the engine checks whether that logical
occurrence already exists and verifies that an existing UID belongs to
the same series and index. The guarantee this gives is
*occurrence-level idempotency*: a retried or concurrent writer
addresses the same CalDAV resource for a logical occurrence, and a
write that failed half-way through resumes at the first missing index
instead of duplicating one of the N objects. It is not a
series-level lock: two writers that change the series record itself
at the same time can still overwrite each other's rule, template, or
horizon. Serializing the series mutations (a per-series lock plus an
ETag-protected update with a reload-and-retry path) is not
implemented in this round and stays open. A direct third-party CalDAV
client that edits a task's status without going through the Genesis
facade is outside the supported recurrence contract.

### 4. Edit scope

A task update has an explicit scope:

- `current`: update only the task UID in the request path. For a
  recurrent task, this is a one-occurrence edit.
- `all-future`: update every materialized open occurrence strictly
  after the selected occurrence, plus the series template used for
  later unmaterialized occurrences. When the selected occurrence is
  current, this is the complete N-occurrence future window. The
  selected occurrence is not changed. Completed and cancelled
  occurrences are never changed.

`PATCH /productivity/tasks/{uid}` keeps its existing path and defaults
to `current`, so existing clients remain compatible. The wire request
gains an optional `scope` value. The response identifies the applied
scope and the UIDs that were affected.

A future-scope edit can change the content, schedule, priority, project,
and recurrence rule. It applies the change to every affected
materialized future task and to the template for later unmaterialized
occurrences. Instance lifecycle status remains a per-occurrence
operation. A future-scope request that tries to change status is
rejected rather than silently completing or cancelling several
occurrences. A rule change only has a meaning for the future chain,
so a request that carries one with the `current` scope is rejected
instead of being answered with a change that did nothing.

Changing the rule or anchor in future scope re-anchors the future
chain and recomputes every affected future row. The chain keeps its
anchor date and shifts it by the day delta the edit applied to the
anchored field, so the monthly clamping day of the series is
preserved: a January 31 series stays on the 31st. A field the edit
did not touch keeps its offset. Changing the anchored *field* (rather
than its value) re-anchors the chain at the selected occurrence and
recomputes the offsets from it, whether or not the selected
occurrence is the chain anchor. The current occurrence and any
completed history are never moved or rewritten, and the occurrence
indexes stay stable so the deterministic occurrence UIDs keep
pointing at the same logical occurrences.

A current-only edit does not alter the future task or the series
template. This is the key distinction from editing a recurring master
in place.

### 5. Cancelling the future

`POST /productivity/tasks/{uid}/cancel-future` is the explicit series
cancellation action. It:

1. finds the series containing the selected task;
2. marks every materialized open occurrence strictly after the selected
   occurrence `CANCELED` (all N futures when invoked on the current
   occurrence);
3. deactivates the series so no replacement future is created; and
4. leaves the selected current occurrence and all completed history
   unchanged.

The action is idempotent. It returns the series UID, all cancelled task
UIDs, and the resulting inactive series state so a human or agent can
confirm what happened.

The existing `DELETE /productivity/tasks/{uid}` keeps its current
meaning: it deletes the selected occurrence. It is not a substitute for
"cancel future" and does not erase completed history.

### 6. API shape

The public API changes are additive:

- `POST /productivity/tasks` accepts an optional `recurrence` object.
  The recurrence object may include
  `materialized_future_count`; when omitted, the server resolves the
  global default. A normal task request is unchanged. A recurrent
  request returns the current occurrence; the returned recurrence
  metadata includes the series UID and effective count.
- `GET /productivity/tasks` accepts an optional `series_uid` filter so
  an agent can read the current occurrence, N future occurrences, and
  history as one series.
- `GET /productivity/recurrences/{series_uid}` returns the series
  inspection state: rule, effective N, current task UID, ordered future
  task UIDs, template, and active state.
- `PATCH /productivity/recurrences/{series_uid}` accepts a new
  `materialized_future_count` for a running series. The response
  reports the effective count and the task UIDs created or cancelled
  while resizing the window. Resizing an inactive series returns 409.
- `PATCH /productivity/tasks/{uid}` accepts `scope=current` (default)
  or `scope=all-future`. The response reports the scope and every
  affected future UID.
- `POST /productivity/tasks/{uid}/cancel-future` cancels the complete
  future window and stops renewal.
- `TaskOut` gains an optional `recurrence` object, including the
  effective count, occurrence date, and occurrence index. Existing task
  responses and clients that ignore unknown optional fields remain
  valid.

### 7. UI behavior

The Streamlit UI mirrors the API contract:

- The create-task dialog has a "Recurring task" switch. When it is
  on, the dialog shows the recurrence controls for frequency,
  interval, anchor, and "future occurrences to keep ready". The count
  defaults to the global setting and can be overridden for this
  series. It rejects creation when the selected anchor date is absent.
- The edit-task dialog shows an explicit "This instance" versus "All
  future instances" choice for a recurrent task and dispatches to the
  matching scoped operation. A status change is a per-occurrence
  operation: in the all-future scope it is applied to the selected
  occurrence on its own.
- The recurrent edit dialog includes a series inspection section with
  the rule, effective N, current occurrence, and ordered future
  occurrences. A number input changes N on the running series.
  Increasing N materializes more rows immediately. Decreasing N asks
  for confirmation because the farthest extra futures are cancelled.
- The recurrent edit dialog has a confirmed "Cancel future" action.
  Its confirmation states that the current occurrence and completed
  history remain. The action is relative to the selected occurrence:
  it cancels every materialized open occurrence after it and stops
  the renewal.
- The shared task table has an edit-scope control for recurring tasks.
  Inline content, date, priority, and project edits use that scope;
  status changes remain current-occurrence operations. An inline edit
  the chosen scope rejects is reported to the user; it is never
  applied under a different scope.
- The task table shows the recurrence rule, effective N, and series
  UID. It also labels each row as current, a numbered future in the N-
  occurrence window, or history. For N greater than 1, all N future
  rows are visible as ordinary task rows.
- Settings shows the global default count and its YAML/environment
  override. It remains a read-only configuration surface, consistent
  with the existing ntfy setting. Per-series horizon changes are
  runtime-adjustable in the task UI and API.

### 8. Shadows, projects, and backups

Each materialized occurrence follows the existing shadow VEVENT
lifecycle. A scheduled future therefore appears on the Calendar page.
Cancelling a future removes its shadow appointment while retaining the
cancelled task record.

Project cancellation and deletion must not leave an active recurrence
series pointing at cancelled tasks. Cancelling a project deactivates
the series of its occurrences before the cascade cancels them, so no
replacement occurrence is materialized over cancelled work. Deleting a
project only unlinks its tasks; the series stays active, but the
project link is cleared from the series templates too, so the next
materialized occurrence is not created with a dangling project id.
The backup manifest gains an optional recurrence-series count; raw
backup and restore continue to use the existing literal CalDAV
archive format.

## Status

Accepted.

## Consequences

Positive:

- Every occurrence is an ordinary task with its own UID, status,
  completion timestamp, and dates. Existing list, completion-history,
  dashboard, project, focus, and calendar code continues to operate on
  ordinary task records.
- Editing one occurrence and editing the future are separate, explicit
  operations. There is no hidden mutation of an RRULE master.
- Cancelling the future is a single action that leaves completed
  history intact.
- The API and UI expose the same scope vocabulary and cancellation
  semantics, so an agent and a human can make the same adjustment.
- The rolling N-occurrence horizon replaces unbounded manual cloning
  while letting a series keep four or another practical number of
  future obligations ready.

Negative:

- A series needs multiple CalDAV writes: the series record and its task
  occurrences are not one atomic Radicale transaction. The recurrence
  operations must be idempotent and use compensating cleanup when
  creation fails part-way through.
- Concurrent completion or future-update requests need locking or
  occurrence-level idempotency checks. Two writers must not
  materialize the same occurrence twice. This round implements the
  occurrence-level part (the deterministic UID and the logical-index
  check); a per-series lock and an ETag-protected series update are
  not implemented yet.
- Materialized occurrences consume more storage than one recurring
  VTODO. The rolling horizon limits this to current, N futures, and
  retained history. A larger N multiplies the number of CalDAV writes
  and the amount of work needed to recover from a partial failure.
- Other CalDAV clients see individual occurrences, not a native
  recurrence set. This is deliberate: they still get valid tasks and
  dates, but they do not expand the rule themselves.
- Existing manually cloned tasks are not inferred or migrated. A task
  becomes recurrent only when the user or agent creates it with a
  recurrence rule or explicitly converts it in a later feature.

Neutral:

- The API gains optional fields and scope-aware task and series
  endpoints, but existing create, read, current-scope update, and delete
  requests keep their meaning.
- The separate `recurrences` collection increases the number of fixed
  CalDAV collections from five to six.
- The recurrence model does not add a dependency. The supported rule
  set can be implemented with standard-library calendar arithmetic.
- Recurrence occurrence UIDs are deterministic get-or-create keys
  derived from `(series_uid, recurrence_index)`. This is the only
  exception to ADR-016's random UID generation and keeps the same
  `t-` plus six-character base36 public shape.
- The standard `genesis` CLI lives in a different repository. It must
  be extended separately to expose the new API fields, scope choice, and
  cancellation action to agents that use the CLI rather than raw HTTP.
