# Plan for adr-015

## Steps

### 1. Rename `Task.summary` to `Task.title`

- `src/genesis/entities/task.py`: rename `summary` field to `title`
  in `Task`, `TaskCreate`, `TaskUpdate`.
- `src/genesis/caldav/ical.py`: `task_from_ical` reads from
  `component.get("summary")` (unchanged — iCal property stays
  `SUMMARY`) and assigns to `Task.title`. `task_to_ical` writes
  `component.add("summary", task.title)`.
- `src/genesis/caldav/tasks.py`: update field accesses.
- `src/genesis/ui/_render.py`, `_table.py`, `pages/*`: update
  field accesses and the input label "Summary" to "Title".
- `tests/test_caldav.py`, `tests/mock_caldav.py`,
  `tests/test_caldav_unit.py`: update field accesses.
- `scripts/migrate_from_legacy_sqlite.py`: update field accesses.

Leave `Event.summary` alone. Leave `TaskStatus` /
`ProjectStatus` / `TimeLogStatus` values alone.

Gate: `make lint && make typecheck && make test && make test-integration`.

### 2. Add FastAPI dependency and console script

- `pyproject.toml`: add `fastapi>=0.111` to `dependencies`.
- `pyproject.toml`: register `genesis-api = "genesis.api.cli:main"`
  under `[project.scripts]`.

Gate: `uv sync` succeeds.

### 3. Implement `src/genesis/api/`

- `__init__.py`: package marker.
- `schemas.py`: Pydantic wire models for `Project`, `Task`, `Journal`,
  `Status` (StrEnum), `JournalType` (StrEnum), plus
  `TaskListResponse`, `Token`, `TokenRefresh`. `UtcDateTime` shared
  Pydantic annotation with the `...Z`-with-3-decimals serializer.
- `security.py`: `create_access_token(subject, ttl)`,
  `create_refresh_token(subject, ttl)`, `decode_token(token, expected_type)`,
  `verify_password(plain, expected)`. HS256 via stdlib `hmac` and
  `hashlib`. Secret loaded from
  `~/.local/share/genesis/default/api_secret.key`; on first read,
  generate a 32-byte random key, write it to that path with mode
  0600, and use it.
- `auth.py`: `POST /auth/login` (form, returns Token) and
  `POST /auth/refresh` (JSON, returns Token). FastAPI
  `OAuth2PasswordRequestForm` and `Body(...)` dependencies.
- `routers/productivity.py`: one router with all productivity
  endpoints. Each endpoint constructs a per-request `Facade` and
  calls the underlying entity method; the entity's Pydantic model
  is converted to the wire model via a small adapter
  (`Task.from_entity()` style or inline `model_validate`).
- `app.py`: `FastAPI(...)` with `lifespan`, `CORSMiddleware`
  (localhost origins only), and router includes.
- `cli.py`: `uvicorn.run(app, host=..., port=...)` reading
  `GENESIS_API_HOST` (default `0.0.0.0`) and `GENESIS_API_PORT`
  (default `8765`).

Gate: `uv run python -c "from genesis.api.app import app; print(app)"`.

### 4. Add Makefile targets

- `api`: `uv run genesis-api`.
- `serve`: echo the URLs, set traps, background both `genesis-api`
  and `genesis-ui`, `wait`. Mirror the reference
  `genesis-agent-backend/Makefile` recipe verbatim.
- Update `make help` to list the new targets.

### 5. Tests

- `tests/test_api_security.py` (unit): JWT roundtrip with bad
  signature, expired token, wrong `type`, missing claims.
  Password verify accepts the default and rejects the wrong value.
- `tests/test_api_schemas.py` (unit): `UtcDateTime` serializes as
  `...Z` with 3 decimals. `Status` values round-trip.
- `tests/test_api.py` (integration, `@pytest.mark.integration`):
  real Radicale + `fastapi.testclient.TestClient`. Login.
  Create project. Create task linked to project. Read task (UID,
  title, status, project_id all correct). List tasks (envelope
  shape). Unlink task (`clear_project_id: true`). Delete task.
  Delete project. Create journal. Read journal. Patch journal.
  Delete journal. Auth required: every productivity route returns
  401 without a bearer token. Login rejects wrong password.

### 6. End-to-end smoke

- Start Radicale.
- Start the API on a free port.
- Curl `/auth/login`, capture access token.
- Curl `POST /productivity/projects` with the bearer token.
- Curl `POST /productivity/tasks` with `project_id`.
- Curl `GET /productivity/tasks` — confirm envelope shape and
  that `uid` strings are present.
- Curl `PATCH /productivity/tasks/{uid}` with
  `project_id: null, clear_project_id: true`.
- Stop the server.

### 7. Lint, typecheck, all tests

`make lint && make typecheck && make test && make test-integration`.

### 8. Commit, push, PR

One branch (`feature/api-server`), commits per step above.
Push to `origin/feature/api-server`. Open a PR with the ADR as
description context.
