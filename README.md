# Genesis Productivity System

A productivity system that manages tasks, projects, calendar, contacts, and journal entries. Data is exposed for AI agents and human users.

## Data Backends

- **Tasks, Projects, Calendar**: CalDAV
- **Contacts**: CardDAV
- **Journal Entries**: Markdown files on disk

## Quick Start

```
make install-dev   # install with dev tools
make help          # see available commands
make test          # run tests
```

## Architecture

- **core**: Facade and shared infrastructure
- **caldav**: Tasks, projects, calendar via CalDAV
- **carddav**: Contacts via CardDAV
- **journal**: Markdown journal entries
- **cli**: Command-line interface (Typer)
- **ui**: Web interface (Streamlit)

## Docs

- **AGENTS.md** — development rules and workflow for AI agents
- **docs/adr/** — architectural decision records (ADRs)

Read AGENTS.md before making any significant changes.
