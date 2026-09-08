# 015. Agent-facing FastAPI server

## Context

The productivity system is exposed to external agents (e.g. the
pi-coding-agent extension at `~/Documents/my-agent-skills/extensions/genesis/`)
over a REST API. The previous implementation lived in a separate repo
(`genesis-scaffolding/genesis-server`) and wrapped a SQLModel-backed
SQLite database. The new system uses CalDAV (Radicale) for tasks and
projects and markdown files on disk for journals.

We need an in-tree FastAPI server that:

- Exposes the same set of endpoints the agent extension already calls
  (projects, tasks, journals, links).
- Uses the app's domain model (UID strings, `Task.title`, app-native
  field names) rather than re-using the previous server's wire shape.
- Is auth-ready: ships with the same OAuth2 password grant the agent
  extension expects, with a single hardcoded account for v1.

The agent extension will need a v2 update to consume the new wire
shape. That update is out of scope for this ADR.

## Decision

### Module

A new package `src/genesis/api/`. Entry point: `genesis.api.cli:main`
(exposed as the `genesis-api` console script).

```
src/genesis/api/
  app.py            FastAPI app + lifespan + CORS + router include
  auth.py           POST /auth/login, POST /auth/refresh
  security.py       JWT (HS256, stdlib hmac) + password verify
  schemas.py        wire Pydantic models + status enums
  cli.py            console-script entry: uvicorn.run
  routers/
    productivity.py projects, tasks, journals
```

### Wire shape

The wire model mirrors the app's domain model. Field names match the
entity (`title`, `description`, `status`, `uid`). Identifiers are the
entity's `uid` (UUID4 hex string), not integers.

```python
class Status(StrEnum):
    NEEDS_ACTION = "needs-action"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    CANCELED = "canceled"

class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    status: Status = "needs-action"
    assigned_date: date | None = None
    hard_deadline: datetime | None = None
    scheduled_start: datetime | None = None
    duration_minutes: int | None = None
    priority: int | None = None
    project_id: str | None = None
```

Datetimes serialize as `YYYY-MM-DDTHH:MM:SS.mmmZ` (3-decimal Z suffix),
matching the format the existing client parsers handle.

### Status enum values stay iCal-shaped

`TaskStatus.NEEDS_ACTION` keeps the value `needs-action`. The agent
extension already accepts these strings (it inherited them from the
previous server's iCal-derived schema). Renaming the values to `todo`
etc. would require rewriting existing VTODO data on disk, which is
out of scope. The names of the enum members stay as-is.

### `Task.summary` becomes `Task.title`

The entity field `Task.summary` is renamed to `Task.title`. The
reason: `summary` is the iCalendar VTODO property name, but our domain
term is "title". The iCal boundary (`genesis.caldav.ical`) keeps
reading and writing `SUMMARY` on the VTODO component — only the
entity-level field name changes. This is the one translation that
happens at the caldav layer; everything else (dates, status,
project_id, etc.) is already named naturally on both sides.

`Event.summary` is left as-is (the user only flagged task naming).

### No ID translation

The previous server synthesised integer IDs from UIDs to keep its
wire contract. We do not. The wire uses UIDs directly. Any client
that needs integer IDs must do its own mapping (the agent extension
will switch to UIDs in a v2 update).

### No bulk endpoints, no separate link endpoint

- `task_create` and `task_update` accept arrays. The previous server
  exposed `POST /tasks/bulk`; we expose a single `POST /tasks` and
  let the client iterate. (Array-in/array-out is also how the
  extension's `task_create` tool calls it.)
- Linking a task to a project is `PATCH /tasks/{uid}` with
  `project_id`. Unlinking is the same endpoint with
  `project_id: null, clear_project_id: true`. We do not expose
  `/tasks/{uid}/projects/{pid}`.

### Auth (v1)

- One account: username `default`, password from
  `os.environ["GENESIS_API_PASSWORD"]` with fallback `genesis-local`.
- `POST /auth/login` accepts `OAuth2PasswordRequestForm` and returns
  `{access_token, refresh_token, token_type, expires_in}`.
- `POST /auth/refresh` accepts `{refresh_token}` and returns the same
  shape.
- JWTs are HS256, signed with a per-user secret persisted to
  `~/.local/share/genesis/default/api_secret.key` (mode 0600,
  generated on first boot).
- Access token TTL: 10 hours. Refresh TTL: 7 days. Claim `type`
  distinguishes access from refresh.
- All `/productivity/*` endpoints require a valid bearer token.
- No `pyjwt`, no `pwdlib`, no user table. The single password is
  compared directly. A future ADR will replace this with proper
  Argon2id-hashed multi-user accounts against a real backend.

### Port and host

- API: `8765`, override via `GENESIS_API_PORT`. Bound to
  `0.0.0.0` by default for Tailscale reach, override via
  `GENESIS_API_HOST`.
- UI stays on `3000` (override `GENESIS_UI_PORT`).
- Radicale stays on `5232`.

### CORS

Permissive for `localhost` and `127.0.0.1` origins only in v1. The
Streamlit frontend does not call the API (it uses the Facade
directly), so this only matters for the agent extension on a
non-loopback host.

### Make targets

`make api` runs the API alone. `make serve` runs API + UI together
in the background and tears them down on Ctrl+C. The `serve`
recipe uses the same `kill 0` / `exit 130` trap pattern that
`genesis-agent-backend/Makefile` uses (this took 30+ minutes to
nail down in that project; we do not reinvent it).

### Dependencies

Add `fastapi>=0.111` to `pyproject.toml`. `uvicorn` is already a
transitive dependency of Streamlit so it is already in the env;
no new dependency for that.

## Status

Accepted.

## Consequences

- The agent extension must be updated to consume the new wire shape
  (UIDs, `title`, `needs-action` status). That is a separate change
  in a different repo.
- The `Task.summary` rename touches ~30 sites across the entity,
  the caldav layer, the UI, tests, and the migration script. All
  callers are updated in the same commit.
- `genesis.api` is a new public module. It depends on
  `genesis.core.facade`, `genesis.caldav`, `genesis.journal`, and
  `genesis.tz`. It does not import from `genesis.ui`.
- Auth is intentionally trivial. A future ADR will harden it with
  real user storage and Argon2id. The token shape will not change.
- `CANCELED` (single L) is preserved as the application's canonical
  spelling (it differs from iCalendar's `CANCELLED` double-L form).
  We keep what's already in the code.
