# 007. Pydantic Settings Refactor

## Context

The current `ConfigsLoader` reimplement pieces of Pydantic Settings functionality:
- YAML file loading
- Environment variable overrides
- Field resolution with nested delimiters

This is unnecessary complexity and duplication. We are maintaining code that Pydantic provides more reliably.

## Decision

We will use Pydantic Settings directly. Changes:

### Configs Class

- Inherit from `BaseSettings`
- Load YAML in `__init__` before calling parent
- Rely on `SettingsConfigDict` for env var handling
- Keep custom `_extract_user()` for user-from-path resolution

### Remove ConfigsLoader

`ConfigsLoader` is no longer needed. `Configs` handles YAML loading directly.

### Priority Chain (unchanged)

```
1. YAML file          (lowest priority)
2. Environment vars    (e.g., GENESIS__DAV__PASSWORD)
3. Explicit args       (highest priority, via __init__)
```

## Status

Accepted

## Consequences

- Removes `ConfigsLoader` class
- Reduces custom code in `configs.py`
- Uses standard Pydantic behavior for env var handling
- Facade simplifies (no lazy loading, just caches Configs instance)
- User extraction remains custom (Pydantic Settings doesn't support this)

## Relationship

Extends ADR-001 (module boundaries) and ADR-006 (multi-user config) by simplifying the configuration module implementation.
