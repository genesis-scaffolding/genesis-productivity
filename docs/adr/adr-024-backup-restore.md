# 024. Backup and restore via raw disk zip

## Context

A user with a logged in account has no way to download their own data
for safekeeping, move to a different machine, or recover from an
accidental destructive action. The system stores per-user state on
disk in two well known locations (ADR-019):

- Radicale collections: `<xdg_data>/genesis/radicale/collections/collection-root/<user>/`
- Journal entries: `<xdg_data>/genesis/journals/<user>/`

We want a single round-trippable archive that covers exactly the
signed in user's data, plus a Settings page in the web UI that exposes
both directions.

Goals:

- One zip per archive. Self describing. Usable offline against the
  data on disk without talking to Radicale, the journal code, or any
  other runtime.
- Symmetric pair of operations: export produces a zip, import
  restores from a zip.
- Restore is destructive: the user explicitly asked for "overwrite"
  semantics. The restore flow must require explicit confirmation
  and must take an automatic pre-restore snapshot so the action is
  reversible.
- UI only for now. The Facade is the natural reuse boundary; adding
  CLI subcommands or API routes later is a thin layer on top.

Non goals:

- Cross user migrations (export from alice, import under bob). The
  archive is single user. Restoring an archive whose declared
  username does not match the signed in account is rejected.
- Cross version round trips. Archives are intended for use against
  the same genesis version that produced them. We tag them with a
  schema version so a warning is possible later, but we do not
  promise compatibility across major upgrades.
- Cardinality over users table, htpasswd, or shared secrets. Account
  state is deliberately out of scope.

## Decision

### Module

New package `src/genesis/backup/` exposing a single `BackupFacade`:

```
src/genesis/backup/
  __init__.py     re-export BackupFacade, ValidationReport, RestoreReport, Manifest
  manifest.py     pydantic models for MANIFEST.json
  facade.py       BackupFacade: export, validate, import
```

Wired up on `Facade` as a new property `facade.backup`, parallel to
`facade.caldav` and `facade.journal`.

Dependencies:

- `genesis.core.facade` for the facade, configs, and `restart_server`.
- `genesis.caldav.facade` to read all four collections and to learn
  the on-disk storage root (Radicale collection tree).
- `genesis.journal.facade` to read every journal entry.
- Standard library only: `zipfile`, `shutil`, `json`, `pathlib`.

Import direction: `genesis.backup` depends on `genesis.core`,
`genesis.caldav`, `genesis.journal`. None of those import back. This
matches the project rule that `if A imports B, B must not import A`
(AGENTS.md).

### Archive format

A zip whose root contains:

```
MANIFEST.json
radicale/...   = <radicale_root>/...
journals/...   = <journal_root>/...
```

The two top level entries share `<data_home>/genesis/` as a common
parent (ADR-019). That makes extraction a straight unpack into the
same parent and lets export walk both roots with the same relative
arcname computation. The section names match the on-disk storage
root names ("radicale", "journals").

`MANIFEST.json` is a pydantic model with these fields:

- `schema_version` (int, currently `1`)
- `generator` (`"genesis-backup"`)
- `username` (string, the user the archive belongs to)
- `created_at` (ISO 8601 UTC string)
- `counts` (object with `projects`, `tasks`, `events`, `time_log`,
  `journal` integers, computed at export time)

We do not re serialize items. The .ics files in the archive are
literal copies of what Radicale wrote; the markdown files in the
archive are literal copies of the journal files. This avoids
introducing a parallel serialization layer that must stay in lockstep
with the four existing ones.

The cost of the raw approach is coupling to Radicale's on-disk
layout. We accept that: the archive is a checkpoint for the same
genesis version that wrote it, not a portable interchange format.

### Export

`BackupFacade.export_to_bytes() -> bytes`:

1. Resolve paths: `radicale_user_root` =
   `<radicale_root>/collections/collection-root/<user>`, `journal_user_root`
   = `<journal_root>/<user>`.
2. Walk the four CalDAV collections (projects, tasks, events, time
   log) to count items for the manifest. Use the same list functions
   the rest of the system uses, so the counts match the UI numbers.
3. Walk the journal directory for a journal count. Use
   `JournalFacade.list_entries()` so legacy date-named files are
   excluded in the same way the journal page excludes them.
4. Build the zip in memory. Manifest goes at the archive root. The
   user's Radicale subtree is added as `radicale/...` and the
   journal subtree as `journal/<user>/...`.
5. Return the bytes.

If the user has nothing on disk yet, the zip still contains
`MANIFEST.json` with zeros and empty subtrees. Empty archive is a
valid concept (matches an empty account) and avoids special casing.

The journal count is best-effort: a file that fails to parse is
counted as 1 file but excluded from the manifest count so we never
misreport a "5 journal entries" archive that lacks one of those
files on import.

### Validate

`BackupFacade.validate_archive(data: bytes) -> ValidationReport`:

Pure read only function. No disk mutation.

Checks:

- Zip opens.
- `MANIFEST.json` parses into the pydantic model.
- `schema_version == 1` (rejects anything else with a clear error).
- `manifest.username == self._username`. A bob archive cannot be
  restored as alice.
- The expected top level entries exist (`radicale/`, `journal/`).
- One item from each present CalDAV collection round trips through
  the existing `*_from_ical` helpers. We do not parse every item on
  every call: parse cost scales with data size and we re parse on
  first read after restore anyway.

`ValidationReport` carries: manifest, present sections, parsed
sample items per section, and an `issues` list with severity per
finding. Used by the Settings page before showing the destructive
button.

### Restore

`BackupFacade.import_from_bytes(data: bytes) -> RestoreReport`:

Destructive. Caller is responsible for obtaining user consent.

Steps in order:

1. Run `validate_archive` first. Refuse to start destructive work on
   a known bad archive.
2. Take an automatic safety backup of the current state to the same
   parent dir as the on disk working copy, file name
   `pre-restore-<user>-<timestamp>.zip`. This always runs first and
   itself catches `OSError` so a read-only filesystem does not block
   restore from a backup on a writable filesystem (the user
   receives an error in that case; we do not silently skip the
   safety net).
3. Restart Radicale (`facade.restart_server()`) so its in-memory
   state matches the post-restore disk layout. Restoring without
   restarting would leave Radicale reading from a path tree that no
   longer exists in its in-memory config.
4. Wipe the user's Radicale subtree and the user's journal subtree.
   Both wipes are scoped strictly to `<user>` so we cannot delete
   another account's data even if the manifest lied about username.
5. Extract the zip into the configured shared roots: `radicale/`
   entries land under `<radicale_root>` and `journals/` entries land
   under `<journal_root>`.
6. Restart Radicale once so its in-memory storage cache reflects the
   freshly written tree. The first restart is not needed for
   correctness — Radicale reads the on-disk `.ics` files on every
   request — but the cache invalidation is cheap and removes a
   class of "stale after wipe" surprises.
7. Build counts by listing each collection and the journal entries.
   Return a `RestoreReport` carrying per-section counts and the
   path of the safety backup so the UI can show it.

Wipe and replace matches the user's "overwrite" intent. A UID keyed
merge was the alternative and it has too many edge cases around
deleted items and FKs (task.project_id pointing at a project the
archive does not include).

### UI

New page `src/genesis/ui/pages/settings.py`. New sidebar section
"Account" in `src/genesis/ui/app.py` with a single entry "Settings"
(icon: `:material/settings:`). The Settings page itself hosts two
expanders:

- Download backup
  - Free text input for an optional filename hint. Default
    `genesis-backup-<user>-<timestamp>.zip`.
  - One click button that reads the bytes from
    `facade.backup.export_to_bytes()` and hands them to
    `st.download_button`.
- Restore from backup
  - `st.file_uploader(type="zip")`.
  - On upload, run `facade.backup.validate_archive(uploaded_bytes)`
    in the page body and show manifest + validation report in
    place.
  - If validation finds blocking issues (`username mismatch`,
    `schema_version` mismatch, missing sections), show an error
    and disable restore.
  - Show a typed confirmation checkbox:
    `I understand this will overwrite my current data`. Until this
    is checked, the primary action is disabled.
  - The primary action button calls `facade.backup.import_from_bytes`
    and shows the resulting `RestoreReport` (counts per section +
    the path of the auto created safety backup).
  - A short "Download the current state first" call to action sits
    above the uploader as a non blocking reminder.

The page also gets the existing "Logout" placement so the user has a
single Account related surface.

### Tests

- Unit tests on mocks (`MockCalDAVFacade` plus an
  `InMemoryJournalFacade` or tempdir journal):
  - `export_to_bytes -> import_from_bytes` preserves counts.
  - `validate_archive` rejects manifest with wrong `username`,
    wrong `schema_version`, and missing required sections.
  - Safety backup is created before any destructive write.
  - Restore against an archive claiming user `bob` raises when the
    facade is constructed for user `alice`.

- Integration test (real Radicale + temp journal dir):
  - Seed a known mixed data set across all four collections plus
    several journal entries.
  - `export_to_bytes`.
  - `import_from_bytes` against a wiped state.
  - Assert `list_*` returns the same counts and a spot checked
    task field round trips.

UI tests are out of scope; we follow the project's convention of
contract tests on the underlying Facade.

## Consequences

Positive:

- Round-trippable personal backup that covers the full data set.
- Restore is reversible: an automatic safety backup makes the
  destructive step undoable.
- The archive format is one zip with one manifest. No new on disk
  schema, no new serialization code path.
- The `BackupFacade` is reusable. CLI or API exposure is a thin
  layer added later without rework.
- UI lives under a new "Account" sidebar section that can host
  future account level concerns (password change, account deletion,
  data export history).

Neutral:

- We add a new package `genesis.backup`. It crosses the existing
  module boundaries and is therefore the kind of decision that
  needs this ADR. Its dependencies (`genesis.core`, `genesis.caldav`,
  `genesis.journal`) are all already established.
- The safety backup lives next to whatever the caller is restoring
  from. We pick the parent directory of the Radicale root, so the
  file appears at `<xdg_data>/genesis/pre-restore-<user>-<timestamp>.zip`.
  No new shared state is introduced.

Negative:

- The raw archive format couples to Radicale's on disk layout. If
  Radicale changes its multi-filesystem layout (or we switch
  backends), archives from the previous version may not restore. We
  tag the schema version so we can detect and warn when this
  happens.
- Sample validation on the import path is partial. A truly broken
  .ics file deeper in the archive would only be noticed on first
  read. We accept this because the safety backup makes recovery
  trivial and full parse is unnecessary work on large archives.
- Restore restarts Radicale twice. The two restarts are needed for
  correctness (wipe + re-extract), and restarting is fast (~10 ms
  plus already-running fast paths). No real cost.
- One new top level page and one new sidebar section. Users have to
  navigate to it. Worth it because backup is an account level
  action, not a daily productivity action.

## References

- ADR-006, ADR-019: per-user data scoping and the shared Radicale
  layout.
- ADR-008, ADR-012: CalDAV storage and the four collection model.
- ADR-017: journal markdown format with YAML frontmatter.
- AGENTS.md: module boundaries, import direction, testing model.
