# Plan for adr-025

## Steps

1. Add `profile_root: Path` to `genesis.configs.Paths` and resolve
   it under `<xdg_data>/genesis/<user>/profile` in `_build_paths`.
2. Create `src/genesis/profile/paths.py` with `ProfileTarget`
   enum, `TARGETS` tuple, `resolve_location(target, profile_root)`
   returning `{path, dir, filename}`. Pure, no I/O.
3. Create `src/genesis/profile/storage.py` with `read_location`,
   `write_location`, `edit_location`, `list_targets`, an injected
   `FsOps` protocol, and a `DefaultFsOps`. Errors returned via
   `ok=False` result dataclasses (matches the out-of-tree tool's
   shape and avoids exceptions for user mistakes).
4. Create `src/genesis/profile/versions.py` with snapshot creation
   (`create_snapshot` with sha256 dedup + force flag for restore),
   `list_snapshots`, and `load_snapshot_content` (matches by exact
   ts or unique prefix, raises ValueError on ambiguous / not
   found). Reuse a small `dates.py` for UTC ISO filename helpers
   (no period, no colon, lex-sortable).
5. Create `src/genesis/profile/facade.py` with `ProfileFacade`
   wrapping the above. Methods: `read`, `write`, `edit`, `list`,
   `history`, `restore`. Constructor takes `profile_root` (a
   `Path`). Each write/edit/restore returns a result dataclass
   suitable for the API to translate.
6. Wire `ProfileFacade` into `genesis.core.facade.Facade` as a new
   `profile` property, lazy-initialized like `journal` / `backup`.
   Reuses the per-user `Configs.user` to resolve `profile_root`
   the same way the journal facade does.
7. Add wire schemas to `genesis.api.schemas`: `ProfileTarget`,
   `ProfileContentOut`, `ProfileListOut`, `ProfileWriteIn`,
   `ProfileEditIn`, `ProfileSnapshotOut`, `ProfileHistoryOut`,
   `ProfileRestoreIn`.
8. Add `src/genesis/api/routers/profile.py` with handlers for the
   six endpoints (GET list, GET read, PUT write, PATCH edit,
   GET history, POST restore). Each handler:
   - Validates the bearer token via `current_subject`.
   - Builds a per-request `ProfileFacade` from the JWT subject.
   - Maps facade results to HTTP errors (404 / 400).
9. Register the profile router in `genesis.api.app.create_app`.
10. Add `tests/test_profile_unit.py` covering paths, storage,
    versions, and facade — fast, no server. Use a `FakeFs` for
    storage + versions tests so the snapshot dedup logic is
    exercised without touching disk.
11. Add `tests/test_profile_api.py` integration-marked tests
    covering the full HTTP round trip: list, read, write, edit,
    history, restore, plus auth (401) and per-user isolation
    (alice's token can't read bob's profile).
12. Run `make format`, `make lint`, `make typecheck`, `make test`.

## File map

New files:

- `src/genesis/profile/__init__.py`
- `src/genesis/profile/paths.py`
- `src/genesis/profile/storage.py`
- `src/genesis/profile/versions.py`
- `src/genesis/profile/dates.py`
- `src/genesis/profile/facade.py`
- `src/genesis/api/routers/profile.py`
- `tests/test_profile_unit.py`
- `tests/test_profile_api.py`
- `docs/adr/adr-025-profile-store.md`  (this ADR)
- `docs/plans/adr-025-profile-store.md` (this plan)

Modified files:

- `src/genesis/configs.py` — add `profile_root` to `Paths`,
  resolve it in `_build_paths`.
- `src/genesis/core/facade.py` — add `profile` property.
- `src/genesis/api/schemas.py` — add profile wire models.
- `src/genesis/api/app.py` — include the profile router.
