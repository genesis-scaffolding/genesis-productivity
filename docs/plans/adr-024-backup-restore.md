# Plan for adr-024

## Steps

### 1. Branch

Already on `feature/backup-restore` (off master).

### 2. New package `src/genesis/backup/`

- `src/genesis/backup/__init__.py`: re-export `BackupFacade`, `Manifest`,
  `ValidationReport`, `RestoreReport`.
- `src/genesis/backup/manifest.py`: pydantic models `Manifest` and
  `Counts`. Frozen schema with `model_config = {"extra": "ignore"}`.
- `src/genesis/backup/facade.py`: `BackupFacade` class with the
  methods below.

### 3. `BackupFacade`

Constructor takes the parent `Facade` so it can reuse:

- `self._facade.configs.user`
- `self._facade.configs.paths.radicale_root`
- `self._facade.configs.paths.journal_root`
- `self._facade.caldav` (counts only)
- `self._facade.journal` (list entries)
- `self._facade.restart_server()`

Public surface:

- `export_to_bytes() -> bytes`
- `export_to_path(path: Path) -> None` (writes via `Path.write_bytes`)
- `validate_archive(data: bytes) -> ValidationReport`
- `import_from_bytes(data: bytes) -> RestoreReport`
- `import_from_path(path: Path) -> RestoreReport` (reads then delegates)

Private helpers:

- `_radicale_user_root() -> Path` returning
  `<radicale_root>/collections/collection-root/<user>`.
- `_journal_user_root() -> Path` returning `<journal_root>/<user>`.
- `_counts() -> Counts`
- `_write_archive(zf: zipfile.ZipFile) -> None`
- `_read_manifest(zf: zipfile.ZipFile) -> Manifest`
- `_wipe_user_data() -> None`
- `_safety_backup_path() -> Path`
- `_create_safety_backup() -> Path`

Manifest validation uses the pydantic model. The sample parse step in
`validate_archive` reads up to 5 .ics files from each present
collection and runs them through the existing `*_from_ical` helpers;
first failure aborts with a `ValueError` whose message names the
offending file.

The destructive restore does not wrap everything in one transaction.
The order is fixed and each step is small enough that an
intermediate failure leaves the previous safety backup intact and
gives the caller a raised exception with a precise step name.

### 4. `Facade.backup` property

`src/genesis/core/facade.py`: import `BackupFacade` lazily inside the
property, cache on `self._backup`. Symmetric with `self._caldav` and
`self._journal`.

### 5. Settings page

`src/genesis/ui/pages/settings.py`:

- `facade = get_facade()`
- Title "Settings", caption "Account and data management."
- Two `st.expander` blocks: "Download backup" and "Restore from
  backup".
- Download expander:
  - Optional filename hint input.
  - "Prepare backup" button -> builds bytes, stores in
    `st.session_state["settings_backup_bytes"]`, exposes a
    `st.download_button` next to it.
- Restore expander:
  - Reminder text "We recommend downloading the current state
    before restoring."
  - `st.file_uploader("Choose a backup zip", type=["zip"])`.
  - On upload: read bytes, run `facade.backup.validate_archive`,
    render manifest + issue list.
  - If validation has blocking issues, show a `st.error` and skip
    the rest of the section.
  - `st.checkbox` for explicit destructive consent.
  - "Restore from this archive" primary button. On click: call
    `facade.backup.import_from_bytes`, render the `RestoreReport`
    counts and the path of the safety backup with a copy button.
- Logout button placement at the bottom matches the existing logout
  affordance on every other page.

No edits to other pages. The Settings page is a fresh file.

### 6. Sidebar registration

`src/genesis/ui/app.py`: add a second nav section "Account":

```
"Account": [
    _page(_PAGES / "settings.py", "Settings", ":material/settings:", "settings"),
],
```

The existing "Productivity" section is unchanged.

### 7. Tests

- `tests/test_backup_unit.py`: mock backend round trip + validation
  rejection cases.
- `tests/test_backup.py`: integration with real Radicale + temp
  journal, marked `@pytest.mark.integration`.

Concrete unit cases:

- `test_export_then_import_preserves_counts`: seed projects, tasks,
  events, time log, journal; export to bytes; wipe state; import;
  assert counts match.
- `test_validate_rejects_wrong_username`: archive whose manifest
  declares `bob`, restore attempted as `alice` -> `ValueError`.
- `test_validate_rejects_bad_schema_version`: `schema_version = 2`
  -> `ValueError`.
- `test_validate_rejects_missing_sections`: zip with no
  `radicale/` or no `journal/` -> issue in `ValidationReport`.
- `test_safety_backup_created_before_destructive_write`: capture
  the safety backup file path before restore, assert it exists
  with content after the restore completes.
- `test_export_writes_manifest_with_zero_counts_for_empty_user`:
  empty facade exports a valid archive with all zeros.

Concrete integration cases (single test, broad assertions):

- `test_round_trip_real_radicale`: seed across all four collections
  plus journal; export to bytes; wipe user data; import; assert
  `list_*` counts match and spot check a project name and a
  journal title round trip.

### 8. Quality gates

Run `make lint`, `make typecheck`, `make test`, `make test-all`.

Fix any failures. Final diff: only files listed above are touched.

### 9. Hand off

Stop, summarize the work for the developer, do not commit.
