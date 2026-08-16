# 004. Module Boundaries and Facade Pattern

## Context

The application has multiple subpackages (core, cli, ui). Without clear boundaries, modules become tightly coupled and hard to test, extend, or replace.

## Decision

We will use the facade pattern and enforce directional imports.

### Package Layout

```
src/<app_name>/
├── configs.py     # Data models (Pydantic), ConfigsLoader, path defaults
├── core/          # The library. Facade lives here. No dependencies on cli or ui.
├── cli/           # Typer CLI. Depends on core only.
└── ui/            # Streamlit UI. Depends on core only.
```

Each subpackage has a clear role:
- **core** — the library. Contains facades, business logic, data models.
- **cli** — command-line entry point. Imports from core.
- **ui** — web UI entry point. Imports from core.

**core is the library. cli and ui are consumers of the library.**

### Facade

Each subpackage exposes a single facade class as its entry point. Callers use the facade. They do not reach into internal classes.

### Import Direction

Import direction must be one-way. If A imports B, B must not import A.

```
# Allowed
cli -> core
ui -> core

# Forbidden
core -> cli
core -> ui
cli -> ui
ui -> cli
```

### Shared Types

For types shared across module boundaries, define them in `configs.py` (the root data model). No separate contracts package unless the project grows to justify it.

### Extension Points

Use abstract base classes (ABC) for extension points and plugins.

### Data Input

Use Pydantic for all data input (env vars, YAML, CLI args, API payloads).

## Consequences

- Modules can be tested in isolation via their facades.
- Modules can be replaced without breaking callers.
- Clear public vs private API is enforced by import direction.
- Adding a new subpackage requires a facade and must respect import direction.
- cli and ui can be removed or replaced independently without touching core.
