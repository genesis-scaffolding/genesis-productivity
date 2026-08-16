# 002. XDG-Based Paths

## Context

The application needs to store and read files in standard locations. Using the XDG base directories convention ensures the app follows Linux conventions and behaves predictably across systems.

## Decision

We will use the four standard XDG base directories. Each resolves to `<xdg_base>/<app_name>/`.

| Variable | Default | Purpose |
|---|---|---|
| XDG_CONFIG_HOME | ~/.config | Config files |
| XDG_DATA_HOME | ~/.local/share | Persistent data |
| XDG_STATE_HOME | ~/.local/state | Logs, runtime state |
| XDG_CACHE_HOME | ~/.cache | Cache files |

These are overridable via environment variables. A custom app name changes the subdirectory in all paths.

## Consequences

- Config files live in ~/.config/<app_name>/
- Logs and state live in ~/.local/state/<app_name>/
- All paths are configurable via env vars without code changes.
