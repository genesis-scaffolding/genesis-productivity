# 033. Recurrent tasks as materialized series instances

> Revised in plan round 3 (2026-11-11). The edit-scope mechanism of
> the first revision (sections 4, 6, and 7 of the original text) is
> replaced. A recurrent task now has two separate editing surfaces:
> the individual occurrence and the series. The series lives in its own
> table on the tasks page, is edited through a form, and is the only
> place where the recurrence is cancelled.

## Context

Genesis has no recurrence support today. A task is one VTODO with one
UID, one status, one `completed_at`, and one set of dates. The recurring
use cases are rent, health insurance, and recurring house work. Those are
currently represented by manually cloned future tasks, for example
several separate "Pay rent" tasks.

The feature needs four first-class behaviors:

1. A recurrent task can be created from the Streamlit UI and from the
   FastAPI API.
2. The schedule of a recurrent task is changed on the series, not on
   the individual occurrences.
3. The future can be cancelled without deleting completed history, and
   the cancellation must be discoverable without hunting for the
   original task.
4. Both the UI and the API expose the same series operations, so a
   human and an agent can make the same adjustment.

The number of future occurrences kept ready must also be configurable.
The initial default is one, but the same model must support keeping
four or another practical number of future tasks visible without
changing the recurrence architecture.

The first implementation of this ADR put the choice between "this
occurrence" and "all later occurrences" into the task edit dialog and
into the task table as an inline scope dropdown. Using it as a user
exposed four problems:

- The inline scope dropdown was not understandable. It changed the
  meaning of every inline edit in the table based on a control the
  user had to notice and understand first.
- Deleting a materialized occurrence made it disappear and then come
  back on the next refresh, because the series immediately materialized
  a replacement with the same title.
- Editing the dates, the times, or the project of a future occurrence
  stuck, even though the series rule said otherwise. The series and
  the occurrence then disagreed with each other.
- Cancelling the recurrence required finding the original task,
  opening its edit dialog, and finding the cancel button inside it.
  When the original task had scrolled out of the list, the series was
  effectively uncancellable.

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
- the optional end date of the series;
- the template for future occurrences;
- the number of future occurrences to keep materialized;
- the highest occurrence index materialized so far;
- the index of the current occurrence;
- whether the series is active, and why it stopped;
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
`X-RECURRENCE-END-DATE`, `X-RECURRENCE-FUTURE-COUNT`,
`X-RECURRENCE-HIGHEST-INDEX`, `X-RECURRENCE-CURRENT-INDEX`,
`X-RECURRENCE-ACTIVE`, `X-RECURRENCE-STOP-REASON`, plus one JSON
property holding the future template. We do not write an RFC 5545
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

The series has an optional end date. An occurrence is only materialized
when its date is on or before the end date, and the end date caps the
materialized window: a series whose end date leaves room for two more
occurrences keeps two, not N. The end date does not end the series by
itself. A series with open occurrences is still active even when its
end date has passed; it finishes when it has no open occurrence left
(section 6). The end date is optional because an open-ended series is
the common case.

Weekday lists, month-position rules, and exception dates are deferred
rather than implied by a partly understood RRULE string.

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
occurrences instead. The end date caps the window: a series whose end
date leaves room for two more occurrences keeps two, not N.

Creating a recurrent task creates the series, the current occurrence,
and N future occurrences. Future occurrences are real tasks with real
dates, so they are immediately visible to the task list, dashboard,
calendar, and existing API period windows. There are N open future
occurrences materialized ahead of the current occurrence.

When the current occurrence becomes terminal (`COMPLETED` or
`CANCELED`), the earliest remaining open future occurrence becomes the
current occurrence and the facade materializes enough new occurrences
at the tail to restore N open futures. A deleted current occurrence is
treated as a skipped occurrence: the next open occurrence is promoted.
Reopening a completed occurrence does not rewind the already
materialized chain.

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

### 4. Two editing surfaces: the occurrence and the series

A recurrent task is edited in two places, and the two places never mix.

**The occurrence edit** changes one materialized instance. It may change
the instance's own content (`title`, `description`, `priority`) and its
lifecycle (`status`). It may not change the fields the series owns:

- `assigned_date`
- `hard_deadline`
- `scheduled_start`
- `scheduled_duration`
- `project_id`

Those five fields are *series-owned*: the series materializes them from
its own anchor, its own rule, and its own template. An attempt to
change one of them on an occurrence is refused with an error that names
the series and points at the series surface. A write that leaves the
value unchanged is not a change and is accepted, so a client may send
a full object without being rejected.

**The series edit** changes the series itself. It is the only way to
change a schedule. The series form edits the anchor, the start date,
the optional end date, the frequency, the interval, the content
template (title, description, project, priority, duration), and the
materialized horizon. Saving a series edit:

- re-derives the schedule of every *open* occurrence of the series from
  the new rule and the new start date;
- rewrites the content template of the series and applies it to every
  open occurrence. An individual content edit of an open occurrence is
  therefore overwritten by a later series content edit; the series form
  states this before saving;
- cancels the open occurrences that fall after a shortened end date;
- grows or shrinks the materialized window;
- never rewrites a completed or cancelled occurrence. History is
  immutable.

Changing the anchored *field* re-anchors the chain on the current
occurrence and recomputes the offsets from it. The occurrence indexes
stay stable, so the deterministic occurrence UIDs keep pointing at the
same logical occurrences.

The series edit does not carry a scope. There is no "this occurrence
versus all later occurrences" choice anywhere in the product. The
series edit applies to the open occurrences of the series, which is the
set the user can see in the series table.

### 5. Deleting a materialized occurrence

Deleting an occurrence of an active series removes that occurrence for
good. The occurrence index is not reused, and the window is not
immediately refilled, so the deleted row does not come back on the next
refresh. The series keeps its rule and renews as usual: the horizon is
one smaller until the current occurrence completes and the window is
restored.

When the deleted occurrence is the current one, the series treats it as
a skipped occurrence and promotes the next open occurrence.

Deleting an occurrence never cancels the series. The UI states this
before the deletion and offers the series cancellation as an explicit
option in the same dialog. The API exposes the same choice through an
optional `cancel_series` parameter on the task delete.

### 6. Cancelling a series

The series is the unit of cancellation. The cancellation is addressed
by the series UID, not by the UID of one of its occurrences, so it does
not require finding a particular task first. It:

1. marks every materialized open occurrence after the current one
   `CANCELED` (all N futures of a healthy series);
2. deactivates the series so no replacement future is created; and
3. leaves the current occurrence and all completed history unchanged.

The action is idempotent. It returns the series UID, all cancelled task
UIDs, and the resulting inactive series state so a human or agent can
confirm what happened.

A cancelled series is final. Its rule, its schedule, and its horizon
are frozen: a series update on a cancelled series is refused. The user
who wants a different recurrence creates a new series.

A series whose occurrences are all terminal *finishes* instead of
being cancelled: it stops renewing, but it is not final. The end date
only caps the materialized window (section 2), so a series whose end
date has passed and which still has open occurrences is still active.
A finished series can be updated again: a schedule change that allows
another occurrence (extending or removing its end date, or changing its
rule) reactivates it and materializes the missing occurrences. A
content-only change (a new title) leaves the series finished.

The existing `DELETE /productivity/tasks/{uid}` keeps its current
meaning: it deletes the selected occurrence. It is not a substitute for
the series cancellation and does not erase completed history.

### 7. API shape

The public API changes are additive:

- `POST /productivity/tasks` accepts an optional `recurrence` object.
  The recurrence object may include `materialized_future_count` and
  `end_date`; when the count is omitted, the server resolves the
  global default. A normal task request is unchanged. A recurrent
  request returns the current occurrence; the returned recurrence
  metadata includes the series UID and the effective count.
- `GET /productivity/tasks` accepts an optional `series_uid` filter so
  an agent can read the current occurrence, N future occurrences, and
  history as one series.
- `GET /productivity/recurrences` lists every series with its rule, its
  dates, its horizon, its status, its current occurrence, and its next
  occurrence date. This is the discovery path: an agent that knows
  nothing about the series can find them here.
- `GET /productivity/recurrences/{series_uid}` returns the series
  inspection state: rule, start and end date, effective N, current task
  UID, ordered future task UIDs, template, and active state.
- `PATCH /productivity/recurrences/{series_uid}` updates the series:
  rule, anchor, start date, end date, content template, and
  `materialized_future_count`. The response reports the effective
  values and the task UIDs that were created, cancelled, or rewritten
  while applying the change. An update on a cancelled series returns
  409.
- `POST /productivity/recurrences/{series_uid}/cancel` cancels the
  series. It is idempotent.
- `PATCH /productivity/tasks/{uid}` edits one occurrence. It refuses a
  change to a series-owned field with 409 and names the series.
- `DELETE /productivity/tasks/{uid}` deletes one occurrence. The
  optional `cancel_series` parameter also cancels the series.
- `TaskOut` gains an optional `recurrence` object, including the
  effective count, the occurrence date, the occurrence index, the
  series end date, and the role of the occurrence in the series
  (`current`, `future`, or `history`). Existing task responses and
  clients that ignore unknown optional fields remain valid.

There is no `scope` parameter on a task update. The first revision of
this ADR introduced one, together with a rule-change payload on the
task update. Both are removed: they mixed the series edit into the
occurrence edit, which is the confusion this revision removes. The
recurrence API was never released, so no client depends on them.

### 8. UI behavior

The Streamlit UI mirrors the API contract:

- The create-task dialog has a "Recurring task" switch. When it is
  on, the dialog shows the recurrence controls for frequency,
  interval, anchor, and "future occurrences to keep ready". The count
  defaults to the global setting and can be overridden for this
  series. It rejects creation when the selected anchor date is absent.
- The tasks page shows a *recurrence series table* underneath the task
  table. It lists every series: title, rule, anchor, start date, end
  date, materialized horizon, current occurrence, next occurrence
  date, and status. The table is read-only. It is not an inline
  editor.
- The series table has two row actions. "Edit" opens the series form.
  "Cancel series" opens a confirmation and then cancels the series.
  The cancellation is reachable from the tasks page itself; the user
  never has to find the original task.
- The series form is a form: title, description, project, priority,
  anchor, frequency, interval, start date, end date (with an explicit
  "no end date" toggle), and the materialized horizon. It states, before
  saving, that the change is applied to every open occurrence of the
  series and that completed history is not touched.
- The edit-task dialog of an occurrence edits that occurrence only.
  There is no scope choice. The series-owned fields are disabled in the
  dialog and carry a caption naming the series that owns them. The
  dialog shows a read-only summary of the series and points at the
  series table; it holds no series controls and no cancel button.
- The shared task table labels each row with the recurrence rule, the
  materialized horizon, the series UID, and the role of the row in the
  series: current, a numbered future in the window, or history. An
  inline edit that touches a series-owned field of an occurrence is
  refused and reported under the table; the edit is never applied.
- Settings shows the global default count and its YAML/environment
  override. It remains a read-only configuration surface, consistent
  with the existing ntfy setting. Per-series horizon changes are
  runtime-adjustable in the series form and the API.

### 9. Shadows, projects, and backups

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
materialized occurrence is not created with a dangling project id. The
project cascade writes the occurrences directly and is not blocked by
the series-owned field rule: it keeps the occurrences and the template
consistent with each other.
The backup manifest gains an optional recurrence-series count; raw
backup and restore continue to use the existing literal CalDAV
archive format.

## Status

Accepted. Revised in round 3: the edit scope is replaced by a separate
series surface, the series gains an optional end date, and the
cancellation is addressed by series.

## Consequences

Positive:

- Every occurrence is an ordinary task with its own UID, status,
  completion timestamp, and dates. Existing list, completion-history,
  dashboard, project, focus, and calendar code continues to operate on
  ordinary task records.
- The schedule of a recurrent task has exactly one owner. The series
  materializes the dates, the times, and the project; an occurrence
  owns its content and its lifecycle. The two can no longer disagree.
- The series is a visible object with its own table. Finding it, and
  cancelling it, does not depend on the position of a task in a list.
- The rule of a series, its dates, and its horizon are changed in one
  place, through one form, with a stated effect on the open
  occurrences.
- Cancelling the future is a single action that leaves completed
  history intact.
- The API and UI expose the same series operations, so an agent and a
  human can make the same adjustment.
- The rolling N-occurrence horizon replaces unbounded manual cloning
  while letting a series keep four or another practical number of
  future obligations ready.

Negative:

- A series needs multiple CalDAV writes: the series record and its task
  occurrences are not one atomic Radicale transaction. The recurrence
  operations must be idempotent and use compensating cleanup when
  creation fails part-way through.
- Concurrent completion or series-update requests need locking or
  occurrence-level idempotency checks. Two writers must not
  materialize the same occurrence twice. This round implements the
  occurrence-level part (the deterministic UID and the logical-index
  check); a per-series mutation lock and an ETag-protected series
  update are not implemented yet.
- Materialized occurrences consume more storage than one recurring
  VTODO. The rolling horizon limits this to current, N futures, and
  retained history. A larger N multiplies the number of CalDAV writes
  and the amount of work needed to recover from a partial failure.
- Other CalDAV clients see individual occurrences, not a native
  recurrence set. This is deliberate: they still get valid tasks and
  dates, but they do not expand the rule themselves.
- A series edit rewrites the open occurrences. An individual content
  edit of an open occurrence is lost when the series content is edited
  afterwards. The series form states this before saving. Per-field
  instance overrides are not implemented.
- Existing manually cloned tasks are not inferred or migrated. A task
  becomes recurrent only when the user or agent creates it with a
  recurrence rule or explicitly converts it in a later feature.

Neutral:

- The API gains optional fields and series endpoints, but existing
  create, read, update, and delete requests keep their meaning.
- The separate `recurrences` collection increases the number of fixed
  CalDAV collections from five to six.
- The recurrence model does not add a dependency. The supported rule
  set can be implemented with standard-library calendar arithmetic.
- Recurrence occurrence UIDs are deterministic get-or-create keys
  derived from `(series_uid, recurrence_index)`. This is the only
  exception to ADR-016's random UID generation and keeps the same
  `t-` plus six-character base36 public shape.
- The standard `genesis` CLI lives in a different repository. It must
  be extended separately to expose the new API fields, the series
  endpoints, and the cancellation action to agents that use the CLI
  rather than raw HTTP.
