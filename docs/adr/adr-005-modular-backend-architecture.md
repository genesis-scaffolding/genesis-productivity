# 001. Modular Backend Architecture

## Context

We are building a productivity system that manages:
- Tasks and projects (CalDAV)
- Calendar events (CalDAV)
- Contacts (CardDAV)
- Journal entries (markdown files)

The system must expose data for:
- AI agents (programmatic access)
- Human users (web UI)

The template uses a facade pattern with XDG paths. We must decide how to structure the modules for each data type.

Forces:
- Each data type has different storage backends (CalDAV, CardDAV, filesystem)
- Agents and UI need consistent access patterns
- The architecture should be extensible for future data types
- Configuration must support both file-based and server-based setups

## Decision

We will use a modular architecture with a facade per data type.

```
genesis/
  core/           # Shared infrastructure
    facade.py     # Root facade aggregating all modules
  caldav/         # Tasks, projects, calendar
    facade.py     # CalDAVFacade entry point
  carddav/        # Contacts
    facade.py     # CardDAVFacade entry point
  journal/        # Journal entries
    facade.py     # JournalFacade entry point
  cli/            # Command-line interface
  ui/             # Streamlit web interface
```

Each module:
- Has a facade as its public entry point
- Uses Pydantic models for data
- Is independently testable
- Can be replaced or extended

The root Facade aggregates all module facades and provides a single entry point for simple use cases.

```
genesis = Facade()
genesis.caldav.get_tasks()
genesis.journal.get_entry(date)
```

## Status

Accepted

## Consequences

- Clear module boundaries based on data type
- Each module can evolve independently
- Agents access data through consistent facade interfaces
- Configuration includes DAV server settings and journal path
- Future data types (e.g., notes, files) can be added as new modules
