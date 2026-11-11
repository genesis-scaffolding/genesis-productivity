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

Pages: Dashboard, Projects, Tasks, Calendar, Journal, Contacts, Statistics.
Data scope is per user (see ADR-010, ADR-019).

A new account is walked through a setup wizard on its first sign-in:
what Genesis Productivity is for, a bit about yourself, and then your
projects, tasks, journal entries, and contacts. The wizard owns the
session until it is finished or skipped, so no other page is
reachable while it runs. It can be run again at any time from
Settings (see ADR-032).

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

## Recurrent tasks

A recurrent task is a series of ordinary tasks. The current
occurrence renews itself: the next occurrence appears by itself, so
"Pay rent" no longer needs four manually cloned tasks. See ADR-033.

- **Create**: the task dialog has a "Recurring task" switch with the
  frequency, the interval, the anchor date field, and "Future
  occurrences to keep ready" (the materialized horizon, default 1).
- **Edit scope**: the edit dialog asks whether the change applies to
  this instance or to all future instances. The task table has the
  same choice for inline edits.
- **Cancel future**: the edit dialog can cancel every future
  occurrence of the series. The current occurrence and the completed
  history stay.
- **Horizon**: the number of future occurrences kept ready can be
  changed per series. The global default for new series lives in
  `configs.yaml`:

```yaml
recurrence:
  default_materialized_future_count: 1
```

  or in the environment as
  `GENESIS__RECURRENCE__DEFAULT_MATERIALIZED_FUTURE_COUNT`. Changing
  the global default affects new series only; existing series keep
  their own count and can be resized from the task dialog or the API.

The API mirrors the same operations:
`POST /productivity/tasks` with a `recurrence` object,
`GET /productivity/tasks?series_uid=...`,
`GET /productivity/recurrences/{series_uid}`,
`PATCH /productivity/recurrences/{series_uid}`, a `scope` field on
`PATCH /productivity/tasks/{uid}`, and
`POST /productivity/tasks/{uid}/cancel-future`.

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
