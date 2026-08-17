# ADR-011: Test Strategy

## Status

Accepted

## Context

We need a testing strategy that enables fast iteration during development while ensuring the full system works correctly. Our CalDAV backend involves multiple components:

1. Genesis facade code (our business logic)
2. caldav library (Python client)
3. Radicale server (running as subprocess)
4. iCal serialization (data format)

Running full integration tests for every code change slows down development significantly (~56 seconds per run).

## Decision

### Two-Tier Testing Approach

We maintain two types of tests with different characteristics:

| Type | Speed | Dependencies | Purpose |
|------|-------|--------------|---------|
| Unit tests | ~0.3s | None | Fast iteration, catch logic bugs |
| Integration tests | ~56s | Radicale server | Verify full stack works |

### Unit Tests

**Implementation:**
- Use `MockCalDAVFacade` - in-memory mock that stores data in Python dicts
- Located in `tests/test_caldav_unit.py`
- No network calls, no subprocess, no external dependencies

**What they test:**
- CRUD operations (create, read, update, delete)
- Filtering and search
- Sorting
- Business logic

**What they don't test:**
- iCal serialization/deserialization
- HTTP communication
- Radicale server behavior

### Integration Tests

**Implementation:**
- Use real `CalDAVFacade` connected to actual Radicale server
- Located in `tests/test_caldav.py`
- Marked with `@pytest.mark.integration`

**What they test:**
- Everything unit tests cover
- Plus: iCal format correctness
- Plus: HTTP communication with Radicale
- Plus: File-based storage

### Marking Tests

Use pytest markers to distinguish test types:

```python
@pytest.mark.integration
class TestTaskCRUD:
    ...
```

Configure markers in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests that require a real Radicale server",
]
```

### Running Tests

Via Makefile:

```bash
make test              # Unit tests only (~0.3s) - default for development
make test-integration  # Integration tests only (~56s)
make test-all          # All tests (~56s)
```

Via pytest directly:

```bash
pytest -m "not integration"   # Exclude integration tests
pytest -m integration          # Only integration tests
```

## Consequences

### Positive

- **Fast iteration**: Developers can run unit tests in under a second
- **Full coverage**: Integration tests verify the complete system
- **Clear convention**: Markers make it obvious which tests need external dependencies
- **Low barrier**: No need to understand the full stack to write basic tests

### Negative

- **Duplication**: Similar tests exist in both unit and integration test files
- **Maintenance burden**: Changes to facade interface require updating both test files and mock
- **Missing coverage**: iCal-specific bugs won't be caught by unit tests

## Future Considerations

### Consolidate Test Files

Currently we have separate files for unit and integration tests. Future improvement:

```python
# All tests in one file, marker determines fixture
@pytest.mark.integration
class TestCalDAV:
    def test_create_task(self, facade):  # fixture returns right implementation
        ...
```

### Protocol-Based Mocking

Define a `CalDAVBackend` protocol:

```python
class CalDAVBackend(Protocol):
    def create_task(self, data: TaskCreate) -> Task: ...
```

Then implement:
- `MockCalDAVBackend` - in-memory, for unit tests
- `RadicaleCalDAVBackend` - real implementation, for integration tests

This reduces duplication and makes the mock a proper abstraction.

## References

- [Pytest Markers Documentation](https://docs.pytest.org/en/stable/mark.html)
- [ADR-009: Radicale + CalDAV Server](./adr-009-radicale-caldav-server.md)
- [ADR-010: CalDAV Multi-User Authentication](./adr-010-caldav-multi-user-auth.md)
