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
make ui            # http://127.0.0.1:3000
genesis ui --port 9000   # different port
```

The UI shows a login screen on first visit. Sign in with an existing
account or use the Create Account tab to add a new one. Each user
sees only their own data; switching accounts is a logout + sign-in.

Headless account management:

```
genesis user create alice          # prompts for password
genesis user list
genesis user change-password alice
genesis user delete alice
```

Pages: Dashboard, Projects, Tasks, Calendar, Journal, Statistics.
Data scope is per user (see ADR-010, ADR-019).

### Migrating an existing single-user setup

If you have live data under the legacy ``default`` user
(``~/.local/share/genesis/default/`` etc.), move it to a named
account before first login:

```
genesis user migrate-default alice
genesis user create alice
```

After the legacy migration, take one extra step to re-home the
per-user state into the shared Radicale layout
(``~/.local/share/genesis/radicale/``, ``.../journals/``, and
``.../api_secret.key``) so one Radicale serves every account:

```
genesis user migrate-shared alice
```

Run ``migrate-shared`` once per user. After all users are
migrated, restart Radicale so it picks up the merged htpasswd
(htpasswd is live-read so this is just for housekeeping) and
the freshly-written rights file.

See ADR-019 for the full story.

## Focus session notifications

Focus session timing (countdown auto-stop and 30-minute milestone
notifications) runs in a per-process daemon thread spawned by
the ``Facade``. As long as *some* long-lived process holds a
``Facade`` for your user, notifications fire even when every
browser tab is closed.

- ``make serve`` runs UI + API; both host a scheduler thread.
- ``make ui`` alone (Streamlit only) is enough for the
  scheduler — the daemon thread lives in the Streamlit worker
  process.
- ``genesis scheduler start`` runs the scheduler in the
  foreground for headless / systemd deployments. ``Ctrl+C`` to
  stop.

See ADR-029 for the design.

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
