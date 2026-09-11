# 020. Completed task time-period queries

## Context

`Task.completed_at` is captured on every transition into `COMPLETED`
and persisted as the iCalendar `COMPLETED` property (`entities/task.py`,
`caldav/tasks.py:213-215`, `caldav/ical.py:178,206`). It is exposed
on the wire as `completed_at` in `TaskOut` and on `TaskListOut`.

There is no way to ask "tasks I finished in this window" today. The
HTTP `GET /tasks` exposes `assigned_date_*`, `hard_deadline_*`, and
`scheduled_start_*` ranges, but no `completed_at_*` range
(`api/routers/productivity.py:354-372`).

`CalDAVFacade.list_tasks(start, end)` exists but its semantics do not
match. The `start`/`end` parameters feed an RFC 4791 §9.9 VTODO
time-range query, which matches `DTSTART` / `DUE` / `DTEND` in
window. `COMPLETED` is not in that set. The docstring at
`caldav/tasks.py:155-167` calls this out explicitly:

> The server-side range is a SUPERSET of "scheduled in window"; callers
> wanting strict DTSTART-only matching should additionally client-side
> filter by ``task.scheduled_start in [start, end)``.

`COMPLETED` is genuinely independent of `DTSTART` and `DUE`. A task
completed today with a `hard_deadline` of next month is a real case
(not an edge case). So a server-side approximation that filters by
`DUE in window` would silently drop correctly-completed tasks.

RFC 4791 §9.7 text-match on custom iCal properties supports
`starts-with`, `contains`, `equals`, etc. but not range queries. A
custom `X-COMPLETED-DATE` property with `starts-with("2026-03-15")`
narrows to a single day, but a week or month range would need one
REPORT call per day. Not worth it.

## Decision

Add two new optional parameters to `CalDAVFacade.list_tasks`:
`completed_at_start` and `completed_at_end`. Both are `datetime | None`
(UTC, tz-aware). Expose them on `GET /tasks` as the same names.

Semantics:

- If neither is set, behaviour is unchanged.
- If either is set, the function returns tasks where
  `task.completed_at` is non-null and falls in
  `[completed_at_start, completed_at_end]`.
- The function pushes `status=COMPLETED` to the RFC 4791 REPORT so
  the server-side candidate set is bounded to completed tasks.
  The caller may pass any other `status` (e.g. `CANCELED`) and the
  function still pushes `COMPLETED` to the server, because
  `COMPLETED` is the only status where `completed_at` is set. The
  client-side `completed_at` filter is the actual source of truth
  for the result.

Naming: `completed_at_start` / `completed_at_end`, parallel to the
existing `start` / `end` but explicitly about the completion
timestamp. Half-open interval `[start, end)` to match the existing
convention.

HTTP layer mirrors the pattern already used for the other date
ranges (`api/routers/productivity.py:354-413`). The router passes
the new params straight through to `CalDAVFacade.list_tasks`. No
extra client-side filter in the router; the facade handles it.

`MockCalDAVFacade.list_tasks` gains the same params so router
tests do not need real server fixtures.

No stats endpoint yet. A `GET /stats/tasks?period=today|week|month`
view is a thin wrapper over the same list call and is deliberately
deferred.

## Status

Proposed.

## Consequences

Positive:

- Single round trip answers "tasks finished today / this week / this
  month" with exact semantics on `completed_at`.
- The server-side `status=COMPLETED` filter keeps the candidate set
  bounded for typical users (hundreds of completed tasks, not
  thousands of all tasks).
- Additive: no existing call site breaks, no signature change for
  callers that do not opt in.

Negative:

- One more pair of params to remember. Mitigated by a clear name
  and parallel structure with the existing `start`/`end`.
- Client-side filter over all completed tasks in the window. For
  typical workloads this is negligible (Python list comprehension
  over hundreds of items). The Radicale fetch is the bottleneck.
- The HTTP filter cannot be combined with `status=CANCELED` in a
  useful way (canceled tasks have no `completed_at`). Documented
  in the facade docstring.

Neutral:

- `MockCalDAVFacade` grows by two optional params.
- No new iCal property. The custom `X-COMPLETED-DATE` idea is left
  on the table for a future optimisation if needed.