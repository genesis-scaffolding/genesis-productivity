# 014. Focus timer as VTODO in time_log

## Context
The productivity system has a `time_log` collection for completed work
sessions, currently modelled as VEVENTs in
`docs/adr/adr-012-time-layer-caldav-model.md`. A flagship feature
requested by the user is a Pomodoro / count-up timer that:

- Optionally aligns to a task; if aligned, the time counts against that
  task. If not, time is still recorded for the user's own tracking
  (e.g., "I was in the gym for 1h 20m").
- Lets the user choose count-down (default 25 min, configurable per
  session) or count-up (no limit).
- Has no pause — just start and stop.
- Must work reliably across phone and laptop over VPN: the user can
  start on one device, stop on another, and the same timer must be
  visible to both.
- Auto-stops a count-down session when its duration elapses, without
  requiring the user to click Stop.

The four-collection model from ADR-012 is the foundation: tasks,
events, projects, time_log. Adding a fifth collection was an
option but rejected — the user's preferred direction is to reuse the
existing `time_log` collection and let one VTODO lifecycle cover both
active sessions and completed historical records.

## Decision

### Storage: VTODO in `time_log`
A focus session is a VTODO in the existing `time_log` collection.
Active sessions have `STATUS != COMPLETED`; completed sessions have
`STATUS = COMPLETED`. `list_time_log(active_only=True)` returns the
former; `list_time_log()` defaults to the latter (historical view).

VTODO is chosen over VEVENT because VTODO has the lifecycle semantics
we need: `STATUS`, `DUE`, `COMPLETED`. VEVENT only models confirmed/
tentative/cancelled, which doesn't fit a running timer.

```
DTSTART:        session start time
DUE:            countdown target end; absent for count-up until stop
STATUS:         NEEDS-ACTION | IN-PROGRESS | COMPLETED
SUMMARY:        label (task name when aligned, user-supplied otherwise)
X-MODE:         COUNTDOWN | COUNTUP
X-TASK-ID:      task UID when aligned (absent otherwise)
COMPLETED:      timestamp when status transitioned to COMPLETED
CREATED, LAST-MODIFIED: managed by CalDAV server
```

`task_id` becomes optional on `TimeLogEntry` and
`TimeLogEntryCreate`. Aligned sessions carry `X-TASK-ID`; unaligned
sessions (gym, reading) carry none.

### Lifecycle
- **Start**: create VTODO with `DTSTART=now`, `DUE=now+duration` if
  countdown, `STATUS=NEEDS-ACTION`, `X-MODE`, `SUMMARY`, `X-TASK-ID`.
- **Manual stop**: PUT with `If-Match: <etag>`, set
  `STATUS=COMPLETED`, `DUE=<actual_end>`, `COMPLETED=<now>`.
  - Countdown stopped early: `actual_end = now`.
  - Countdown stopped at or after DUE: `actual_end = due` (target).
  - Count-up: `actual_end = now`.
- **Auto-stop** (countdown DUE passed): the Focus page fragment
  detects `now >= session.due AND status != COMPLETED` and runs the
  manual stop sequence with `actual_end = session.due` (canonical
  target). The auto-stop only fires while a device has the Focus
  page open; recovery on the next device-open is graceful because
  the canonical target is preserved.
- **Discard**: DELETE the VTODO. No record kept.

### Race protection
The PUT-with-If-Match on stop is the atomic primitive. Two devices
that both click Stop race on the PUT; one wins, the other gets a 412
precondition-failed and exits gracefully. The losing device's stop
request is discarded, not retried — its user sees the timer vanish
within the next fragment tick.

### Distributed consistency
All session state lives in CalDAV. Both devices see the same active
list within the fragment's refetch interval. The fragment reruns
every 1 second for display updates; CalDAV refetch is throttled to
every 30 seconds by default to keep traffic light. Display updates
between refetches use the cached session list and recompute elapsed
locally.

### Live ticking
The Focus page renders active sessions inside
`@st.fragment(run_every="1s")`. The fragment fetches active sessions
from CalDAV at the throttled interval and renders each session card.
Auto-stop is detected and applied during the fragment tick. Manual
Stop / Discard buttons live inside the fragment; their callbacks
issue the PUT / DELETE against CalDAV and call `st.rerun(scope="app")`
so the parent re-reads state.

### TimeLogEntry schema change
Existing `TimeLogEntry` is rewritten to match the new VTODO model:

```
uid: str
label: str                            # was implicit via SUMMARY
start: datetime                       # DTSTART
due: datetime | None                  # DUE (was end)
completed_at: datetime | None          # COMPLETED (new)
status: TimeLogStatus                 # NEW enum
mode: TimeLogMode                     # NEW enum
task_id: str | None                   # was str (required)
description: str | None
created: datetime | None
modified: datetime | None
```

`TimeLogEntryCreate` gains `mode`, `label`, `status` fields and
`task_id` becomes optional. `TimeLogEntryUpdate` retains all-fields-
optional semantics. Existing callers are internal to `caldav/` and
tests; no UI depends on the schema yet.

### Migration
Per the project's "no migration — wipe and start fresh" stance
established in ADR-012, existing VEVENT time_log entries are
discarded. The user's local Radicale will be wiped for the
`time_log` collection at the start of testing.

## Status
Accepted

## Consequences

### Positive
- One collection, one entity, one lifecycle. No new collection to
  provision, no new entity to learn.
- VTODO semantics (`STATUS`, `DUE`, `COMPLETED`) map directly to the
  active/completed distinction. No invented state machine.
- Cross-device consistency falls out of CalDAV + If-Match.
- Auto-stop in the fragment is a small piece of code that runs
  exactly when needed (when DUE passes while a device is watching).
- Existing tests cover the CRUD surface; rewriting them is mechanical.

### Negative
- Mixed semantics in the `time_log` collection: VTODOs that are
  still active and VTODOs that are historical. Default
  `list_time_log()` filters to completed to preserve the "log"
  reading; `active_only=True` opts in to active.
- Renaming `end` → `due` is a breaking API change on `TimeLogEntry`.
  Acceptable because no UI consumes it yet.
- Auto-stop relies on a device being open to the Focus page. Server-
  side GC (cron / systemd timer) is not in scope.

### Neutral
- A "Logs" page for completed sessions + statistics is a follow-up.
- Unaligned sessions get `SUMMARY` from user-supplied label (default:
  "Focus session"). Task-aligned sessions get `SUMMARY` = task name.
- Discarded sessions leave no audit trail.
