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

## UI

The web interface is a multi-page Streamlit app. Start it with:

```
make ui            # default user on http://127.0.0.1:3000
genesis ui --user alice   # a named user
genesis ui --user alice --port 9000   # different port
```

Pages: Dashboard, Projects, Project, Tasks, Calendar, Journal, Journal Entry. Data scope is per user (see ADR-010); restarting the UI with a different `--user` switches scope.

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
