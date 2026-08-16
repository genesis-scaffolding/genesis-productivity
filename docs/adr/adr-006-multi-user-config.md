# 006. Multi-User Config Support

## Context

Genesis runs on a shared Linux account. Multiple humans (e.g., user and partner) need isolated instances. We cannot rely on OS user separation because only one Linux account is available.

We need to avoid data collision between users while keeping the architecture simple. No authentication layer.

## Decision

We will support multiple named users, each with isolated config and data directories.

### Directory Structure

```
~/.config/genesis/<user>/configs.yaml
~/.local/share/genesis/<user>/
~/.local/state/genesis/<user>/
~/.cache/genesis/<user>/
```

Where `<user>` defaults to `"default"`.

### CLI Resolution

The CLI accepts `--user <name>` to select a user. Resolution:

```
genesis --user alisa tasks list
  --> ~/.config/genesis/alisa/configs.yaml

genesis tasks list
  --> ~/.config/genesis/default/configs.yaml
```

The CLI resolves `--user` to a config path before passing to Facade.

### Facade Layer

Facade remains unchanged. It accepts `yaml_path` only. User resolution happens at the CLI layer.

### Configs Layer

Configs.py updates `_build_paths()` to prepend `genesis/<user>/` to all XDG base paths. User is derived from the yaml path (extracted from directory name).

## Status

Accepted

## Consequences

- Each user has isolated data and config
- No shared state between users
- No authentication — relies on trust or filesystem permissions
- Escape hatch: `--config <path>` still works for custom locations
- Facade stays clean, user concept is CLI-only
