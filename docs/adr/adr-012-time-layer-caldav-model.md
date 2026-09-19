# 012. Time Layer CalDAV Model

## Context

We have a partial CalDAV implementation with two entity types (Task, Project) and a per-project collection layout (`project:<uid>` calendars each holding a single VEVENT). Three problems:

1. **Per-project collections are over-modelled.** Each project occupies a whole CalDAV collection named by opaque UID, and stores the project metadata as a VEVENT inside it. Project discovery requires scanning every collection; the calendar container and its content are conflated; a project name (the human-readable field) has nowhere natural to live.
2. **No appointment or pomodoro storage.** The design calls for four primitives (project, task, appointment, pomodoro) but only two exist. `Task.scheduled_start` exists as a field but produces no calendar artefact.
3. **Concurrency is broken.** `update_task` and `update_project` use a delete-then-create pattern that bypasses the `caldav` library's ETag handling. Last-write-wins is the worst-case behaviour.

Forces:

- Standard iCalendar semantics (VTODO for tasks, VEVENT for events) must be preserved for client interop with Apple Calendar, Thunderbird, Evolution, etc.
- Off-the-shelf CalDAV clients do not render VTODOs on a time grid. Shadow VEVENTs are the only way to make time-blocked tasks visible as appointments in those clients.
- Multi-user data isolation is already in place via Radicale htpasswd auth + per-user storage. The four-collection layout must not break that.
- The `caldav` library and Radicale handle ETag negotiation under the hood. We just have to stop fighting them with delete-then-create.

## Decision

This ADR is the canonical record of the Time Layer design. It supersedes the storage layout and entity field mappings in ADR-008 with respect to Task and Project, and adds two new entities (Event, TimeLogEntry).

### 1. Architecture: The Triad

Three layers, each using the most appropriate tool:

- **Time** (CalDAV): when things happen and what is due. This ADR.
- **People** (CardDAV): who is involved. Deferred to a future ADR.
- **Knowledge** (Markdown): deep research, notes, project docs. Daily journal is implemented; per-project markdown is deferred.

### 2. Four fixed top-level collections

Each user's CalDAV principal has exactly four named collections. No nesting. Each has one role:

| Collection | iCal Object | Role |
|---|---|---|
| `projects` | VTODO | Project registry (one VTODO per project) |
| `tasks` | VTODO | Actionable items |
| `events` | VEVENT | Appointments + shadow events for time-blocked tasks |
| `time_log` | VEVENT | One VEVENT per completed pomodoro session |

Collection names are hardcoded constants in `genesis/caldav/facade.py`. They are not user-configurable. The `task_calendar_name` and `project_calendar_prefix` fields on `DAV` config are removed.

Projects are NOT sub-collections. A project is a VTODO inside `projects`, identified by its `UID`.

### 3. Entity models

#### Project — VTODO in `projects`

| Field | Type | iCal property | Notes |
|---|---|---|---|
| `uid` | str | `UID` | Immutable PK. Format: `{uuid4}@genesis` |
| `name` | str | `SUMMARY` | Human-readable project name |
| `description` | str \| None | `DESCRIPTION` | Markdown body |
| `start_date` | date \| None | `DTSTART` (DATE) | When the project starts |
| `deadline` | date \| None | `DUE` (DATE or DATE-TIME) | When the project is due |
| `status` | enum | `STATUS` | `needs-action` / `in-progress` / `completed` / `canceled` |
| `created` | datetime \| None | `CREATED` | UTC |
| `modified` | datetime \| None | `LAST-MODIFIED` | UTC |

#### Task — VTODO in `tasks`

| Field | Type | iCal property | Notes |
|---|---|---|---|
| `uid` | str | `UID` | Immutable PK |
| `summary` | str | `SUMMARY` | Task title |
| `description` | str \| None | `DESCRIPTION` | Optional notes |
| `hard_deadline` | datetime \| None | `DUE` | Firm delivery, datetime UTC; the UI treats this as date-only and snaps the picked date to 23:59:59 local on write |
| `assigned_date` | date \| None | `X-ASSIGNED-DATE` | Day the user plans to work on it |
| `scheduled_start` | datetime \| None | `DTSTART` | Time-blocked appointment, datetime UTC; triggers shadow VEVENT |
| `scheduled_duration` | int \| None | `X-SCHEDULED-DURATION` | Duration in minutes; defaults to 30 when shadowing |
| `priority` | int \| None | `PRIORITY` | 1–9; genesis UI sorts 9 at the top, so the larger integer is the more urgent task (opposite of the RFC 5545 convention) |
| `status` | enum | `STATUS` | Same four values as Project |
| `project_id` | str \| None | `X-PROJECT-ID` | FK to Project.UID, optional |
| `completed_at` | datetime \| None | `COMPLETED` | Set when status transitions to completed |
| `created` | datetime \| None | `CREATED` | UTC |
| `modified` | datetime \| None | `LAST-MODIFIED` | UTC |

#### Event — VEVENT in `events`

| Field | Type | iCal property | Notes |
|---|---|---|---|
| `uid` | str | `UID` | Immutable PK |
| `summary` | str | `SUMMARY` | Event title |
| `start` | datetime | `DTSTART` | datetime UTC |
| `end` | datetime | `DTEND` | datetime UTC |
| `location` | str \| None | `LOCATION` | Optional |
| `description` | str \| None | `DESCRIPTION` | Optional |
| `status` | enum | `STATUS` | `tentative` / `confirmed` / `canceled`. Default `confirmed`. |
| `task_id` | str \| None | `X-TASK-ID` | FK to Task.UID. Present only on shadow events. |
| `created` | datetime \| None | `CREATED` | UTC |
| `modified` | datetime \| None | `LAST-MODIFIED` | UTC |

#### TimeLogEntry — VEVENT in `time_log`

| Field | Type | iCal property | Notes |
|---|---|---|---|
| `uid` | str | `UID` | Immutable PK |
| `start` | datetime | `DTSTART` | Pomodoro start, datetime UTC |
| `end` | datetime | `DTEND` | Pomodoro end, datetime UTC |
| `description` | str \| None | `DESCRIPTION` | Optional session notes |
| `task_id` | str | `X-TASK-ID` | FK to Task.UID |
| `created` | datetime \| None | `CREATED` | UTC |

### 4. Foreign keys

All FKs are single-value custom properties, NOT `CATEGORIES`:

- `Task.X-PROJECT-ID` to `Project.UID`
- `TimeLogEntry.X-TASK-ID` to `Task.UID`
- `Event.X-TASK-ID` to `Task.UID` (shadow events only)

Single-value properties are simpler to parse, round-trip cleanly through every CalDAV client, and match the pattern already exercised in the codebase. The original design sketch proposed `CATEGORIES`; that was rejected because `CATEGORIES` is multi-value and harder to parse deterministically.

### 5. Shadow VEVENT pattern

When a `Task` is written with `scheduled_start` set, a linked `VEVENT` is maintained in `events` with the same UID:

- `DTSTART` is taken from `Task.scheduled_start`.
- `DTEND` is `scheduled_start + scheduled_duration` minutes. `scheduled_duration` defaults to 30 when not set.
- `X-TASK-ID` is the task UID.
- Writes are atomic at the facade level: a task write that affects the shadow writes both objects in one logical operation. No half-state where the task is updated and the shadow is stale, or vice versa.
- When `scheduled_start` is cleared, the shadow VEVENT is deleted.

VTODOs do not render on time grids in off-the-shelf CalDAV clients. The shadow VEVENT is what makes time-blocked tasks visible as calendar appointments in Apple Calendar, Thunderbird, Evolution, and other standard clients. Our own Streamlit UI could render a VTODO with `scheduled_start` on a grid without the shadow, but the shadow is required for client interop.

### 6. Time fields on Task

Three fields with three different semantics, kept independent:

| Field | Type | Semantic |
|---|---|---|
| `assigned_date` | date | "I will work on it Tuesday" — planning, low commitment |
| `scheduled_start` | datetime UTC | "I have 2pm to 3pm Tuesday" — time-blocked, high commitment, triggers shadow |
| `hard_deadline` | datetime UTC | "Must be done by Friday EOD" — firm constraint |

No auto-derivation between them. `assigned_date` may exist without `scheduled_start` and vice versa. `scheduled_duration` is only meaningful when `scheduled_start` is set; otherwise it is ignored.

### 7. Cross-cutting rules

1. **Timezone.** UTC on the wire and at rest. Local timezone applied only at the display layer.
2. **Concurrency.** Rely on the `caldav` library and Radicale to handle ETag negotiation. Modify iCal components in place and call `save()` on the existing object. Do NOT delete-then-recreate iCal objects. This is a behaviour change from the current `update_task` and `update_project` implementations.
3. **Custom metadata.** `X-` prefix on every custom property. Standard iCal properties used for everything else.
4. **Identity.** `UID` is the immutable primary key for every iCal object. Never regenerated. Surrogate UUIDs are generated as `{uuid4}@genesis`.
5. **Multi-user.** Unchanged. One Radicale htpasswd user per `genesis` user. Auth is per-user. Data is namespaced per user at the CalDAV layer. Server lifecycle is per-Facade.

### 8. Facade surface

`CalDAVFacade` exposes four method groups, one per entity:

- Projects: `get_project`, `list_projects`, `create_project`, `update_project`, `delete_project`
- Tasks: `get_task`, `list_tasks`, `create_task`, `update_task`, `delete_task`
- Events: `get_event`, `list_events`, `create_event`, `update_event`, `delete_event`
- TimeLog: `get_time_log_entry`, `list_time_log`, `create_time_log_entry`, `update_time_log_entry`, `delete_time_log_entry`

`update_task` is responsible for maintaining the shadow VEVENT. Writes that affect a task with `scheduled_start` set update both the VTODO and the shadow VEVENT in one logical operation.

## Status

Superseded by adr-016 with respect to the UID format (section 4, Identity). Other rules remain in effect.

Supersedes parts of ADR-008 with respect to Task and Project storage layout and entity field definitions, including the addition of `priority`, `scheduled_duration`, and the introduction of Event and TimeLogEntry entities. ADR-008's Status Values section remains in effect.

## Consequences

### Positive

- Four-collection layout is fixed and discoverable. No more `project:<uid>` per-project calendars.
- Project metadata no longer collides with calendar rendering. Storing projects as VTODOs in a single collection means off-the-shelf clients see a clean task list for projects, not stray VEVENT entries.
- Appointments and pomodoros are first-class entities. The four primitives match the design intent.
- Client interop for time-blocked tasks is correct. Shadow VEVENTs render in standard CalDAV clients.
- Concurrency is correct. The delete-then-create anti-pattern goes away. ETag negotiation is delegated to the library.

### Negative

- Existing data in `project:<uid>` calendars is orphaned. This round wipes and starts fresh.
- Custom property parsing must be defensive. The `icalendar` library is permissive; we must enforce `X-PROJECT-ID`, `X-ASSIGNED-DATE`, `X-TASK-ID`, `X-SCHEDULED-DURATION` as single-value strings, not as iCal lists.
- Shadow VEVENT writes are coupled to task writes. Any code that mutates a task with `scheduled_start` set must maintain the shadow, which increases the surface area for bugs.
- No migration tool. Anyone with existing data must re-enter it.

### Neutral

- CardDAV and per-project markdown are still deferred. They are separate ADRs when their time comes.
- The mock backend in `tests/mock_caldav.py` must mirror the four-collection model. Out of scope for this ADR; covered in the implementation plan.

## References

- [ADR-008: CalDAV Storage](./adr-008-caldav-storage.md) — superseded in part by this ADR
- [ADR-009: Radicale CalDAV Server](./adr-009-radicale-caldav-server.md)
- [ADR-010: CalDAV Multi-User Authentication](./adr-010-caldav-multi-user-auth.md)
- [ADR-011: Test Strategy](./adr-011-test-strategy.md)
