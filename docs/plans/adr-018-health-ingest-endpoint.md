# Plan for adr-018

## Steps

1. **`src/genesis/health/__init__.py`** — package marker, re-export `HealthFacade` and `MetricType`.

2. **`src/genesis/health/enums.py`**
   1. `MetricType(StrEnum)` covering: `stepCount`, `distanceWalkingRunning`, `activeEnergyBurned`, `basalEnergyBurned`, `flightsClimbed`, `appleExerciseTime`, `appleMoveTime`, `appleStandHour`, `heartRate`, `restingHeartRate`, `heartRateVariabilitySDNN`, `oxygenSaturation`, `respiratoryRate`, `bodyTemperature`, `walkingHeartRateAverage`, `bodyMass`, `bodyMassIndex`, `bodyFatPercentage`, `leanBodyMass`, `headphoneAudioExposure`, `environmentalAudioExposure`, `mindfulSession`, `sleepAnalysis`.
   2. Each member carries a `kind` (`MetricKind.CUMULATIVE | INSTANTANEOUS | CATEGORY`) as an enum attribute on the enum class, looked up via `MetricType.kind_for(metric_str)`.
   3. Module docstring calls out the **recommended v1 Shortcut starting set** (six cumulative activity metrics — `stepCount`, `distanceWalkingRunning`, `activeEnergyBurned`, `flightsClimbed`, `appleExerciseTime`, `appleStandHour`) as a clearly-marked section so it is the first thing a future reader sees. Other metrics are listed under a separate "available, not in v1 Shortcut yet" section.

3. **`src/genesis/health/schemas.py`**
   1. `Sample` Pydantic model: `id`, `metric`, `value`, `unit`, `start` (UTC datetime), `end` (UTC datetime | None), `source` (str | None), `ingested_at` (set by storage on insert).
   2. `RawSample` model carrying the original wire payload for `raw_json` storage.

4. **`src/genesis/health/storage.py`**
   1. `HealthStorage` class wrapping a sqlite3 `Connection`. Constructor opens `paths.data / "health" / "health.sqlite"`, runs schema migrations idempotently, sets `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`.
   2. Module-level invariant documented in the file docstring: every datetime written to the DB is tz-aware UTC, formatted as ISO 8601 with Z suffix. `upsert` rejects tz-naive datetimes with `ValueError("datetime must be tz-aware UTC")` before any SQL runs. Reads parse back into tz-aware UTC via `datetime.fromisoformat` (Z-suffix string is accepted by Python 3.11+).
   3. Private `_format_utc(dt: datetime) -> str` helper that asserts `dt.tzinfo is not None`, calls `genesis.tz.to_utc(dt)`, and renders `YYYY-MM-DDTHH:MM:SS.ffffffZ`. Single source of truth for the on-disk format; both `upsert` and the idempotency-key table use it.
   4. `upsert(samples: list[Sample]) -> tuple[int, int, int]` returns `(inserted, updated, ignored)` based on `INSERT ... ON CONFLICT(id) DO UPDATE`. Missing `id` is filled by hashing `(metric, start, end, value, source)` into a uuid5 string.
   5. `list_samples(metric, start, end, limit, offset) -> list[Sample]`.
   6. `summary(day: date) -> list[MetricSummary]` — one entry per metric present on that day, with `count`, `sum`/`mean`/`min`/`max` based on `MetricKind`.
   7. Private helper `_idempotency_seen(key: str) -> bool` backed by a small `idempotency_keys (key TEXT PRIMARY KEY, recorded_at TEXT)` table; called before insert for any sample-batch with `idempotency_key` set.
   8. Close helper. `__enter__` / `__exit__` to integrate with the existing `Facade.shutdown` pattern.

5. **`src/genesis/health/facade.py`**
   1. `HealthFacade` thin wrapper over `HealthStorage` with the same `(path)` constructor as `JournalFacade`.
   2. `upsert(samples)`, `list_samples(...)`, `summary(day)` delegate to storage. Translate `MetricType` strings to / from raw strings.
   3. `__enter__` / `__exit__` for context-manager use.

6. **`src/genesis/core/facade.py`**
   1. Add `_health: HealthFacade | None = None` field and `health` property mirroring `journal` (lazy construction, builds `cfg.paths.data / "health" / "health.sqlite"`).
   2. No changes to `shutdown` beyond nulling `_health`.

7. **`src/genesis/api/schemas.py`**
   1. `SampleIn`: wire shape per ADR.
   2. `IngestIn`: `{ samples: list[SampleIn], idempotency_key: str | None }`.
   3. `IngestOut`: `{ inserted, updated, ignored, request_id }`.
   4. `SampleOut`: id, metric, value, unit, start, end, source, ingested_at.
   5. `MetricSummary`: metric, count, sum, mean, min, max, unit.
   6. `SummaryOut`: `{ day: date, metrics: list[MetricSummary] }`.
   7. Reuse `UtcDateTime` annotation.

8. **`src/genesis/api/routers/health.py`**
   1. `POST /health/ingest`: validate each sample (metric in enum unless `raw_passthrough`); reject samples with naive `start` or `end` with HTTP 422 and a message naming the offending field (timezone invariant per ADR); enforce 1000-sample cap and 5 MB body via FastAPI body-size middleware or request guard. Idempotency check via storage. Return `IngestOut`.
   2. `GET /health/metrics`: query params `metric` (required), `start` / `end` (UTC datetimes), `limit` (default 1000, cap 5000), `offset`. Return `list[SampleOut]`.
   3. `GET /health/summary`: query param `day` (required, ISO date). Return `SummaryOut`.
   4. All endpoints protected by `current_subject`.

9. **`src/genesis/api/app.py`**
    1. Import `health_router` from `genesis.api.routers.health`.
    2. `app.include_router(health_router)`.

10. **`tests/mock_caldav.py`** (if needed) — add a `MockHealthStorage` matching the `HealthStorage` surface for unit tests of the router. Keep it alongside the existing `MockCalDAVFacade`.

11. **`tests/test_health_unit.py`**
    1. `TestUpestDedupe`: same `id` sent twice -> 1 row, second send reports `updated=1`.
    2. `TestHashFallbackId`: identical `(metric, start, end, value, source)` re-sent without id -> 1 row.
    3. `TestIdempotencyKey`: two ingests sharing `idempotency_key` -> second is a no-op.
    4. `TestCuratedEnumRejects`: unknown metric with `raw_passthrough=False` -> sample ignored, `ignored += 1`.
    5. `TestRawPassthroughAccepts`: unknown metric with `raw_passthrough=True` -> stored as raw string.
    6. `TestSummaryAggregation`: cumulative metric gets `sum`, instantaneous gets `mean`, `sleepAnalysis` gets minutes-from-end-minus-start.
    7. `TestPagination`: `limit` and `offset` honoured on `list_samples`.
    8. `TestRejectsNaiveDatetime` (storage layer): calling `upsert` with a tz-naive datetime raises `ValueError`. No row written.
    9. `TestStoredValuesAreUtc`: insert a tz-aware non-UTC datetime (`+10:00`), read it back, assert the returned datetime is tz-aware UTC and represents the same instant.
    10. `TestSummaryDayBoundsAreLocal`: insert samples straddling local midnight; query `summary(day=today)`; assert only the post-midnight sample appears (boundary semantics match `genesis.stats`).

12. **`tests/test_health.py`**
    1. `@pytest.mark.integration` full round trip via TestClient: bearer-required, ingest then read same row.
    2. `TestRejectsNaiveDatetime` (HTTP layer): `POST /health/ingest` with a naive `start` returns 422 with a message naming the field.

13. **`tests/conftest.py`**
    1. Add a `temp_health_db` fixture (pytest tmp path + HealthStorage close on teardown) for unit tests.
    2. Add a `mock_health` fixture mirroring `mock_caldav` for the integration test client.

14. **`tests/test_api_coverage.py`**
    1. Extend any coverage map to include the new `/health/*` routes.

15. **`Makefile`**
    1. No new targets. Document that `make api` / `make serve` already pick up the new router via the `genesis.api.app` import.

16. **Documentation**
    1. `HealthFacade` docstring: idempotency, dedupe behaviour, per-user file location.
    2. `MetricType` enum: docstring lists the matching iOS Shortcut "Find Health Samples" identifier for each member.
    3. Reference `docs/adr/adr-018-health-ingest-endpoint.md` in both.
    4. Create `docs/health-shortcut.md` — the iOS Shortcut setup guide. Contains: bearer-token bootstrap steps, the recommended v1 action list (six activity metrics + Get Contents of URL), the 12-hour lookback window rationale, automation trigger setup (morning + evening). Cross-referenced from `README.md`.

17. **Verify**
    1. `make lint`
    2. `make typecheck`
    3. `make test` (unit tests only, fast)
    4. Spot-check the SQLite file with `sqlite3 .schema` and a sample `SELECT`.
    5. OpenAPI spec sanity: `make api` then `curl localhost:8765/openapi.json | jq '.paths | keys'` includes `/health/ingest`, `/health/metrics`, `/health/summary`.