# 018. HealthKit ingest endpoint

## Context

The productivity system stores tasks, projects, journals, and time
logs in CalDAV + markdown. There is no channel today for telemetry
about the user themselves: sleep, steps, heart rate, body weight,
audio exposure, etc.

The user wants an iOS Shortcut on their phone to push HealthKit
samples to the productivity server a few times per day (over VPN),
so the agent has a richer picture of their physical state and can
correlate it with the work they do.

This is an architectural change because:

- It is a new storage shape. Tasks and projects are addressable
  entities (UUIDs); HealthKit produces a high-volume time series of
  small samples. CalDAV is wrong (it is iCalendar-shaped) and the
  journal markdown layer is wrong (one file per human-edited note).
  SQLite is the natural fit, and SQLite is the historical storage
  the previous (`genesis-scaffolding`) system used.
- It is a new module boundary. The project rules require a facade
  per module, with import direction one-way and no cycle.
- It is a new API surface. The agent API exposes productivity
  endpoints under `/productivity/*` behind a bearer token. Health
  endpoints sit beside those under `/health/*` behind the same
  bearer, so the iPhone Shortcut can reuse the OAuth2 password
  grant that already exists.
- It introduces an external writer. Until now every writer has been
  the user through the UI, the CLI, or the agent extension. Health
  adds a fourth: an unattended iOS Shortcut that can fire on a
  schedule, on cellular, with intermittent connectivity. Idempotency
  and per-request bounds matter.

## Decision

### Module

A new package `src/genesis/health/` with its own facade and
SQLite-backed storage. Mirrors the existing `journal/` package in
shape (markdown files + Facade) but uses SQLite instead of files
because the data is small, append-heavy, and queried by metric and
time range.

```
src/genesis/health/
  __init__.py
  enums.py          MetricType enum (the canonical identifier set)
  schemas.py        internal Sample pydantic model
  storage.py        SQLite DAL (schema migrations, upsert, queries)
  facade.py         HealthFacade: thin wrapper over storage
```

The `Facade` core facade (`src/genesis/core/facade.py`) gains one
new property:

```python
@property
def health(self) -> HealthFacade:
    if self._health is None:
        from ..health.facade import HealthFacade
        cfg = self.configs
        self._health = HealthFacade(cfg.paths.data / "health" / "health.sqlite")
    return self._health
```

Import direction: `genesis.health` imports from `genesis.configs`
and `genesis.tz` only. It does not import from `genesis.api`,
`genesis.caldav`, `genesis.journal`, `genesis.ui`, or
`genesis.stats`. The agent will consume it via the API, not by
direct import.

### Storage

SQLite has no native timezone type, so the application layer
enforces a strict invariant: every timestamp stored is tz-aware
UTC. Specifically:

- Wire datetimes (`start`, `end`) must be tz-aware. Naive datetimes
  are rejected at the ingest endpoint with HTTP 422 and a message
  naming the offending field. This is stricter than the productivity
  endpoints (which accept naive datetimes and assume UTC), because
  health data is automated (no human to catch a silent miss), the
  source device's timezone can differ from the server's, and the
  Shortcut can be configured to emit ISO 8601 with `Z`.
- `HealthStorage.upsert` normalizes every datetime to UTC via
  `genesis.tz.to_utc` and writes it as ISO 8601 with Z suffix.
  There is no code path that writes a naive or non-UTC value to
  the database.
- `HealthStorage.list_samples` / `summary` parse the stored strings
  back into tz-aware UTC datetimes using `datetime.fromisoformat`
  after reattaching the Z suffix.
- The summary endpoint's `day` query parameter is a calendar date.
  Its UTC bounds are computed in the user's local timezone via
  `genesis.tz.to_local`, consistent with `genesis.stats.period_bounds`.
  A query for `day=2024-09-10` on a server running in Sydney returns
  the samples with local start times between `2024-09-10 00:00 +10`
  and `2024-09-11 00:00 +10`.

The README of `genesis.health.storage` documents these rules as
load-bearing. A future per-region or per-user TZ preference is a
separate ADR; in v1 the server's local timezone is the single
source of truth for "what is a day".

SQLite database per user at `paths.data / "health" / "health.sqlite"`.
Per-user keeps the existing CalDAV pattern (one server, multiple
principals) and means a future multi-user story (ADR-006) drops in
without a data migration. The DB is opened with a connection pool of
size 1 inside the facade; SQLite + a single FastAPI worker per user
does not benefit from pooling, and the Facade is short-lived per
request today.

Schema:

```sql
CREATE TABLE samples (
    id            TEXT PRIMARY KEY,        -- caller-supplied UUID from HKUUID, or hash fallback
    metric        TEXT NOT NULL,           -- HKQuantityTypeIdentifier string
    value         REAL,                    -- NULL for category samples
    unit          TEXT,                    -- HKUnit string ("count", "m", "kcal", "bpm", ...)
    start_utc     TEXT NOT NULL,           -- ISO 8601 with Z suffix
    end_utc       TEXT,                    -- nullable; categories with no end
    source        TEXT,                    -- bundle id of the source app / device
    ingested_at   TEXT NOT NULL,           -- server-side wall clock, ISO 8601 UTC
    raw_json      TEXT                     -- full original JSON for unmapped / future fields
);

CREATE INDEX idx_samples_metric_start ON samples(metric, start_utc);
```

`id` is the dedupe key. The iOS Shortcut passes the `UUID` Apple
attaches to each `HKSample` (already a string, globally unique on
the device). If the client omits it, the server hashes
`(metric, start_utc, end_utc, value, source)` into a UUID5 for
upsert. Re-sending a sample is a no-op (`INSERT OR REPLACE` on the
PK). This makes the ingest endpoint idempotent across retries,
duplicate Shortcut runs, and the same sample appearing in two
sends.

`raw_json` stores the full original sample record as JSON. It costs
a few hundred bytes per sample but means future metric additions or
type changes do not need a re-send. Reads do not depend on it; the
indexed columns are the query path.

### Metrics scope (curated enum + raw passthrough)

A `MetricType` StrEnum covers the canonical set we know we want
(see ADR Context). Validators reject values outside the enum unless
the request also sets `raw_passthrough: true` on the sample, in
which case the metric string is stored as-is and `raw_json` carries
the rest. This is the "best of both" shape: known metrics get
typed queries, unknown metrics do not block ingest.

The enum values are the HealthKit identifier strings themselves
(`stepCount`, `heartRate`, `sleepAnalysis`, `bodyMass`,
`activeEnergyBurned`, etc.) so the wire and storage agree with the
Apple docs without a translation table.

### Recommended initial Shortcut contents

The iOS Shortcut is built incrementally. v1 ships with the
activity family only — six Find Health Samples actions — because
movement and energy are the strongest single-signal proxy for
"how the user's day is going" and they are the easiest to wire
without permission surprises:

| Shortcut action | MetricType value | Kind |
| --- | --- | --- |
| Find Health Samples: Step Count | `stepCount` | cumulative |
| Find Health Samples: Walking + Running Distance | `distanceWalkingRunning` | cumulative |
| Find Health Samples: Active Energy | `activeEnergyBurned` | cumulative |
| Find Health Samples: Flights Climbed | `flightsClimbed` | cumulative |
| Find Health Samples: Exercise Time | `appleExerciseTime` | cumulative |
| Find Health Samples: Stand Hours | `appleStandHour` | cumulative |

Each Find Health Samples uses a 12-hour lookback window so the
two-daily sends (morning + evening) cover a full 24 hours with
overlap. The final action is one Get Contents of URL that POSTs
the assembled `IngestIn` body to `/health/ingest` with the bearer
header.

Other metrics (`heartRate`, `restingHeartRate`, `sleepAnalysis`,
etc.) are accepted by the endpoint from day one but are added to
the Shortcut only after the user has confirmed samples land
correctly. The Shortcut itself is a separate file outside this
repo; the recommendation lives here so the agent can answer "what
should I send next" without guessing.

### API

```
POST /health/ingest         body: { samples: [Sample], idempotency_key?: str }
GET  /health/metrics        ?metric=...&start=...&end=...&limit=...&offset=...
GET  /health/summary        ?day=YYYY-MM-DD         per-day per-metric rollup
GET  /health/metrics        ?metric=heartRate&start=...&end=...   (read path)
```

The new router lives in
`src/genesis/api/routers/health.py` and is included from
`genesis.api.app` next to the productivity router. CORS and auth
rules are unchanged from the productivity surface: bearer token
required, origins allowlist unchanged.

### Wire shape

```python
class SampleIn(BaseModel):
    id: str | None = None                  # HKUUID, or None for hash fallback
    metric: str                            # HealthKit identifier
    value: float | None = None             # NULL for category samples
    unit: str | None = None
    start: UtcDateTime                     # when the sample started
    end: UtcDateTime | None = None
    source: str | None = None              # bundle id of source app/device
    raw_passthrough: bool = False          # if True, metric not enum-validated
    extra: dict[str, Any] | None = None    # goes into raw_json verbatim


class IngestIn(BaseModel):
    samples: list[SampleIn]
    idempotency_key: str | None = None     # optional client-supplied nonce


class IngestOut(BaseModel):
    inserted: int                          # new rows
    updated: int                           # rows that replaced existing ids
    ignored: int                           # rejected by validator (bad metric, etc.)
    request_id: str                        # server-generated uuid, for log correlation


class SampleOut(BaseModel):
    id: str
    metric: str
    value: float | None
    unit: str | None
    start: UtcDateTime
    end: UtcDateTime | None
    source: str | None
    ingested_at: UtcDateTime
```

The `SampleOut` includes `ingested_at` but not `raw_json` (keeps
the common read path small; full raw is available on request).

### Summary rollup

`GET /health/summary?day=YYYY-MM-DD` returns:

```python
class SummaryOut(BaseModel):
    day: datetime.date
    metrics: list[MetricSummary]

class MetricSummary(BaseModel):
    metric: str
    count: int
    sum: float | None         # for cumulative metrics (steps, energy)
    mean: float | None        # for instantaneous metrics (heart rate, weight)
    min: float | None
    max: float | None
    unit: str | None          # dominant unit (modes of the day's samples)
```

Sum-vs-mean is driven by a per-metric attribute (`MetricType.kind`
in the enum): `cumulative` (steps, distance, energy, flights,
exercise minutes, mindful minutes, stand hour) -> `sum`; the rest ->
`mean`. `sleepAnalysis` is category-valued and gets a custom
treatment (`count` of asleep samples + total minutes computed from
end - start). New metrics only need an enum entry to participate.

### Auth

Reuse the existing JWT. The iPhone Shortcut stores the refresh
token in the Shortcut's secure storage, refreshes once a day
(10-hour access TTL), and posts with the bearer header. No new
auth surface in v1.

Per-device long-lived tokens (without password grant) are a future
ADR. The current OAuth2 password grant is fine because the
password never leaves the device and is rotated only by re-running
the bootstrap shortcut.

### Request bounds

- `POST /health/ingest`: cap body size at 5 MB and per-call sample
  count at 1000. A daily send is typically a few hundred samples;
  1000 is generous headroom. Larger sends return 413.
- `GET /health/metrics`: `limit` defaults to 1000, hard cap at
  5000 per request.
- Rate limiting: none in v1. VPN-only reach + the bearer requirement
  is sufficient. Per-IP rate limiting is a future ADR.

### Endpoint host

`/health/*` lives on the same port as `/productivity/*` (8765).
No new port. CORS allowlist unchanged.

### Dependencies

- `sqlite3` (stdlib, already present).
- No new third-party packages.

### Tests

Unit tests under `tests/test_health_unit.py` using a per-test
temp SQLite file. Coverage:

- Upsert dedupe: same `id` sent twice -> one row, second send
  reports `updated=1`.
- Hash-fallback id: identical `(metric, start, end, value, source)`
  re-sent -> one row.
- Idempotency-key collision: two distinct sends with the same
  `idempotency_key` -> second send returns the first send's counts
  (a no-op replay).
- Curated enum rejects unknown metric when `raw_passthrough=False`.
- Curated enum accepts unknown metric when `raw_passthrough=True`
  (stored as raw string).
- `start`/`end` parsing in UTC, naive datetimes rejected.
- `summary` aggregation: cumulative metric gets `sum`, instantaneous
  gets `mean`, `sleepAnalysis` gets minutes-from-end-minus-start.
- Pagination on `GET /health/metrics`.

Integration test under `tests/test_health.py` (real FastAPI +
TestClient, mock storage) covers: bearer-required, full round trip
of an ingest then a read.

## Status

Proposed.

## Consequences

Positive:

- The agent extension has a single new endpoint family to ask
  "what was the user's physical state on day X" or "what does the
  heart-rate trend look like over the last 30 days". Combined with
  the existing time log rollup, the agent can produce statements
  like "sleep was 5h, focus minutes dropped 40% on those days" with
  no new tools.
- Storage is local-only, per-user, no third-party telemetry
  pipeline. Health data stays inside the user's machine.
- Idempotent ingest is robust to retries (cellular drops, Shortcut
  rerun, automation double-fire).
- Curated enum + raw passthrough keeps the common path typed
  without foreclosing future HealthKit additions.

Negative:

- New persistence layer (SQLite). Adds operational surface:
  backups now include the SQLite file alongside the Radicale data
  dir and the journal markdown. The Makefile and any future backup
  scripts need to know about `paths.data / "health"`.
- Timezone safety is an explicit invariant the application must
  enforce. SQLite's lack of a TZ type means a stray call to
  `str(naive_datetime)` would silently corrupt the data. The plan
  documents the invariant and adds a dedicated test for the
  naive-rejection path so it cannot regress.
- New wire model + endpoint contract. The agent extension does not
  consume these endpoints in v1, so there is no immediate consumer
  to break. The wire contract is the source of truth in the OpenAPI
  spec at `/openapi.json`.
- The curated enum must be kept in sync with the iOS Shortcut.
  Stale enum values cause silent no-op ingests (metric accepted,
  zero rows matched in summary). Mitigation: per-metric
  documentation in the enum docstring with the matching Shortcut
  action name.
- `raw_json` is a future-proofing cost on every sample. Acceptable
  for the expected volume (hundreds per day) but worth noting if
  ingest volume ever grows by orders of magnitude.

Neutral:

- New module boundary. `genesis.health` is a new public package;
  only `genesis.core.facade` and `genesis.api` import from it.
- New API router. The router count under `genesis.api.routers`
  grows by one. The auth surface does not.
- Reuses the existing bearer token, so no new auth code, but also
  no new auth features (long-lived device tokens, per-device
  scoping) — those remain future ADRs.