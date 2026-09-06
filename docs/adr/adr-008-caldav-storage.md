# 008. CalDAV Storage for Tasks and Projects

## Context

We need to store tasks and projects persistently. We chose direct .ics file storage over a CalDAV server for simplicity. We need to decide the storage layout, data model, and serialization approach.

Forces:
- Tasks can be appointments (with start time) or plain todos
- Tasks optionally belong to one project (no many-to-many)
- Projects can exist without tasks (placeholders)
- Need to manage projects directly
- Use standard iCalendar format for interoperability

## Decision

### Storage Layout

```
<data>/caldav/
  projects/
    <uid>.ics          # Project metadata (VTODO)
  tasks/
    <uid>.ics          # Task (VTODO)
```

Each .ics file contains one VCALENDAR with one VTODO component.

### Task Entity

| Field | Type | iCalendar |
|-------|------|-----------|
| uid | str | UID |
| summary | str | SUMMARY |
| description | str | DESCRIPTION |
| hard_deadline | datetime | DUE |
| assigned_date | date | X-ASSIGNED-DATE |
| scheduled_start | datetime | DTSTART + DURATION |
| status | str | STATUS (needs-action, in-progress, completed, canceled) |
| project_id | str | X-PROJECT-ID |
| completed_at | datetime | LAST-MODIFIED (set when completed) |
| created | datetime | CREATED |
| modified | datetime | LAST-MODIFIED |

### Project Entity

| Field | Type | iCalendar |
|-------|------|-----------|
| uid | str | UID |
| name | str | SUMMARY |
| description | str | DESCRIPTION |
| start_date | date | DTSTART |
| deadline | date | DUE |
| status | str | STATUS |
| created | datetime | CREATED |
| modified | datetime | LAST-MODIFIED |

### Status Values

Tasks and projects use the same lifecycle:
- `needs-action` — default, not started
- `in-progress` — being worked on
- `completed` — done
- `canceled` — abandoned

### Serialization

Use `icalendar` library for parsing and writing .ics files.

### Facade Operations

**Tasks:**
- `get_task(uid)` → Task | None
- `list_tasks(
    project_id=None,
    status=None,
    due_before=None,
    due_after=None,
    assigned_before=None,
    assigned_after=None,
    search=None,
    sort_by=["created"],
    sort_order="desc",
  )` → list[Task]
- `create_task(data)` → Task
- `update_task(uid, data)` → Task
- `delete_task(uid)` → None

**Projects:**
- `get_project(uid)` → Project | None
- `list_projects(
    status=None,
    deadline_before=None,
    deadline_after=None,
    search=None,
    sort_by=["created"],
    sort_order="desc",
  )` → list[Project]
- `create_project(data)` → Project
- `update_project(uid, data)` → Project
- `delete_project(uid)` → None
  - Removes project_id from all tasks that reference this project before deletion

**Search and Filtering:**
- `search` parameter matches against summary and description (case-insensitive)
- Date filters support range queries (before/after)
- Multiple filters combine with AND logic
- Sorting supports multiple fields (applied in order), ascending or descending

## Status

Superseded by adr-012

## Consequences

- Tasks and projects stored as .ics files
- Standard format, can migrate to CalDAV server later
- Task-to-project link via X-PROJECT-ID (single project per task)
- Projects must be empty before deletion
- Journal remains as markdown files (separate module)
