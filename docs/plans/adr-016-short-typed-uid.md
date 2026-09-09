# Plan for adr-016

## Steps

1. Update `src/genesis/caldav/ical.py`:
   - Replace `new_uid() -> str` with `new_uid(prefix: str) -> str`.
   - Use `secrets.choice` over `[a-z0-9]`, length 6, no suffix.
   - Export `ALPHABET` and `TAIL_LENGTH` as module constants for tests.
2. Update `src/genesis/journal/facade.py`:
   - Import `new_uid` from `genesis.caldav.ical`.
   - Change `Entry.uid` default factory from `uuid.uuid4().hex` to
     `new_uid("j")`.
3. Update UID call sites in `src/genesis/caldav/`:
   - `tasks.py`: `new_uid()` -> `new_uid("t")`
   - `projects.py`: `new_uid()` -> `new_uid("p")`
   - `events.py`: `new_uid()` -> `new_uid("e")`
   - `time_log.py`: `new_uid()` -> `new_uid("l")`
4. Update `tests/mock_caldav.py`:
   - Replace `_new_uid()` with `_new_uid(prefix)` using the same
     algorithm, so mock IDs match the real facade's shape.
5. Update hardcoded UID fixtures in tests:
   - `tests/test_caldav_unit.py` (3 lookups, 3 iCal fixture inserts).
   - `tests/test_caldav.py` (3 lookups).
   - Replace `@genesis` with the new prefix where appropriate so the
     test files match the new format. Keep the same intent (lookups
     for nonexistent items, stored fixtures).
6. Update `docs/adr/adr-012-time-layer-caldav-model.md`:
   - Status line: `Accepted.` -> `Superseded by adr-016 with respect to
     the UID format. Other rules remain in effect.`
7. Run `make test` to verify all unit + integration tests pass.
8. Inspect a generated UID to confirm shape (`t-XXXXXX`).
9. Wipe default user's Radicale data + journal:
   - Use `scripts/migrate_from_legacy_sqlite.py --db /tmp/user_private.db
     --wipe-target --apply`.
10. Spot-check: list projects, list tasks, count journal markdown files.
