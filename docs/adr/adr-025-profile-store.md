# 025. Per-user profile and life-story markdown store

## Context

An agent assisting the user needs persistent context about who the
user is and what their history looks like. Today that data lives in
plain markdown files managed by an out-of-tree tool (`personalize`
CLI in my-agent-skills). That tool is single-user, talks to disk
directly, and has no concept of the genesis multi-user auth model.
Two users sharing a machine would have their profile files collide
on whatever path the tool was pointed at.

This ADR moves the storage layer for two of those targets
(`profile`, `life_story`) into genesis so:

- Each authenticated user gets their own profile and life-story file,
  scoped to the same per-user data tree as their journals.
- Edits are versioned so any change can be rolled back.
- A REST API exposes read / write / edit / history / restore so
  remote agents can speak to genesis over HTTP, while the existing
  personalization tooling stays untouched on the client side and
  wires itself to the new endpoints.

Out of scope for this ADR: `persona` and `info_hierarchy`. They
remain managed by the out-of-tree tool; bringing them into genesis
is a future ADR.

## Decision

### Module

New package `src/genesis/profile/`:

```
src/genesis/profile/
  __init__.py     re-export ProfileFacade, ProfileTarget, ProfileContent, ...
  paths.py        pure target/filename/path resolution (no I/O)
  storage.py      read / write / edit over the canonical file
  versions.py     snapshot lifecycle (create / list / restore) with sha256 dedup
  facade.py       ProfileFacade: thin orchestrator on top of the above
```

Wired on `Facade` as `facade.profile`, parallel to `facade.journal`
and `facade.backup`.

Dependencies:

- `genesis.configs` for the per-user data path.
- Standard library only for everything else (`hashlib`, `json`,
  `dataclasses`, `pathlib`).

No new third-party dependencies.

### Targets

Two targets this round:

- `profile` — the user's stable profile (name, role, timezone, etc.).
- `life_story` — the user's narrative history.

Both are versioned. Both share the same storage layer; the target
name is the only thing that differs.

Candidate filenames and cwd-vs-project scope from the out-of-tree
tool are dropped. There is one canonical filename per target per
user, and exactly one location. This matches the server-side
reality (per-user) and avoids inheriting filesystem complexity that
has no analogue in a multi-user API.

### Storage layout

Per user, under `<data_home>/genesis/<user>/`:

```
profile/
  profile.md          # canonical, trimmed on read
  life-story.md       # canonical, trimmed on read
  .versions/
    profile/
      <UTC-ISO>.md           # snapshot content
      <UTC-ISO>.meta.json    # snapshot metadata sidecar
    life-story/
      <UTC-ISO>.md
      <UTC-ISO>.meta.json
```

Layout mirrors the out-of-tree tool's `.pi/personalize-versions/`
shape (same UTC-ISO filename, same meta sidecar fields) so the
client side can speak the same on-disk format if it ever wants to
read snapshots locally. Snapshots stay per-user so alice's history
never appears in bob's directory tree.

### Snapshot format

`<UTC-ISO>.md` — the file's bytes at that instant. UTC-ISO form is
`YYYY-MM-DDTHHMMSSfffZ` (no colons, no period — lex-sortable).

`<UTC-ISO>.meta.json` — companion sidecar:

```json
{
  "ts": "2026-01-16T091500000Z",
  "command": "write" | "edit" | "restore-pre",
  "filename": "profile.md",
  "canonical_path": "/abs/path/to/profile/profile.md",
  "prev_sha": "<sha256 or null>",
  "this_sha": "<sha256>",
  "note": "<optional user-supplied note>"
}
```

Millisecond collisions (same UTC instant, two writes) get a `-N`
counter suffix before `.md` / `.meta.json`, matching the out-of-tree
tool.

### Dedup

Snapshot creation is skipped when:

1. The new content's sha256 matches the **previous canonical file's**
   sha256 (no-op edit / write), OR
2. The new content's sha256 matches the **most recent snapshot's**
   sha256 (same content twice in a row).

Restore skips dedup: it always takes a pre-restore snapshot of the
current canonical file before applying, so restore is itself
reversible.

### API

Mounted on the existing FastAPI app under `/profile`, JWT-protected
like the rest of `/productivity`. Endpoints:

| Method | Path                                | Purpose                                    |
|--------|-------------------------------------|--------------------------------------------|
| GET    | `/profile`                          | List both targets with active path + existence + size + versioned flag |
| GET    | `/profile/{target}`                 | Read the current canonical content (trimmed) |
| PUT    | `/profile/{target}`                 | Overwrite content (creates snapshot)       |
| PATCH  | `/profile/{target}`                 | Surgical edit (`old`, `new`, optional `global_replace`) |
| GET    | `/profile/{target}/history`         | List snapshots (newest first, optional `limit`) |
| POST   | `/profile/{target}/restore`         | Restore from snapshot (`version`, optional `note`) |

Error mapping:

- `404` — target not found on disk (read), or no snapshot matching `--version`.
- `400` — invalid target name, empty `old`, `oldText not found`, `oldText matches N places`, ambiguous version prefix, target not versioned (history/restore).
- `401` — missing / invalid bearer token.
- `200` / `201` — success.

The per-request handler builds a `ProfileFacade` from the JWT subject
so one request can only touch one user's data. There is no
cross-user API.

### Wire schemas

Lives in `genesis.api.schemas` alongside the existing models.
`Status` enum is reused where it applies; a new
`ProfileTarget(StrEnum)` defines `profile` / `life_story`.
Datetime fields use the existing `UtcDateTime` annotation.

### Config

Add a single new field to `genesis.configs.Paths`:

```python
profile_root: Path = Field(description="Per-user profile directory. Resolves to <data_home>/genesis/<user>/profile.")
```

Resolved in `_build_paths` the same way `data` is: under
`<xdg_data>/<app_name>/<user>/profile`. Per-user because the data
itself is per-user; no shared root needed.

### Non-goals

- No CLI subcommand. The agent-facing surface is HTTP only.
- No edits to my-agent-skills. Their tool can call our endpoints.
- No `persona` or `info_hierarchy` support.
- No concurrent-edit file locking. Matches the out-of-tree tool's
  no-lock behaviour; a future ADR can add it if collisions are seen
  in production.
- No snapshot pruning. Append-only. A `prune --older-than <days>`
  command can come later if disk use matters.
- No global (`~/.config/genesis/profile/`) fallback. Per-user only.

## Consequences

Positive:

- Profile and life-story data lives next to the user's other genesis
  data and inherits multi-user isolation for free.
- The agent client stays decoupled. Any HTTP-speaking client can
  read/write/rollback.
- Versioning matches the on-disk format the existing client already
  speaks, so debugging across the boundary is straightforward.

Negative:

- Two sources of truth for profile/life-story files (the legacy
  out-of-tree tool and genesis). Until the legacy tool is retired,
  editing one side will not update the other. Mitigation: the
  legacy tool is single-user, so any user who has moved to
  multi-user genesis should switch to the new path. A future ADR
  can retire the legacy path once the migration is complete.
- Storage layout is per-user, so the server cannot bulk-export
  profile snapshots the way it bulk-exports journals (which share
  a root). Backup (ADR-024) already walks the per-user data tree,
  so this is covered.

Neutral:

- Adding a new target later (e.g. `persona`) means a new file in
  this package and a new enum value, no schema migration.
