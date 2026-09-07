# Plan: migrate legacy sqlite data into the new CalDAV + markdown backend

## Goal

Move historical data from the previous productivity system's sqlite
database into the new ``genesis-productivity`` system so the user
has a single source of truth going forward.

## Source layout (already inspected)

Path: ``/home/gentran1991/genesis-scaffolding/user_directories/1/.genesis/user_private.db``
Local copy: ``/tmp/migration-data/user_private.db``

| Table | Rows | Date range |
|---|---|---|
| project | 20 | 2026-03-18 .. 2026-06-30 |
| task | 400 | 2026-03-17 .. 2026-09-05 |
| projecttasklink | 339 | (FK join table) |
| journalentry | 114 | reference 2026-01-01 .. 2026-09-05 |

Notable status distributions:

- Project: TODO 9, IN_PROGRESS 3, COMPLETED 4, CANCELED 4
- Task: BACKLOG 12, TODO 76, IN_PROGRESS 2, COMPLETED 277, CANCELED 33
- Journal: DAILY 73, GENERAL 2, MONTHLY 5, PROJECT 16, WEEKLY 17, YEARLY 1

73 daily entries, no duplicate dates -> the new system's
"one daily per date" rule is satisfied on import.

61 of 400 tasks are not linked to any project (orphan). Their
``project_id`` field will be left ``None``.

## Target layout

The new system stores project, task, time-log in CalDAV (Radicale)
and journal entries as markdown files under the user's data path.
No mysql / sqlite database is involved on the new side. Migration
is a one-shot pull, not a continuous sync.

The script uses the same public Facade API the CLI / UI consumes:

- ``Facad.caldav.create_project(ProjectCreate(...))``
- ``Facad.caldav.create_task(TaskCreate(...))``
- ``Facad.journal.save_entry(Entry(...))``

## Schema mapping

### Project

| Legacy | New | Notes |
|---|---|---|
| ``project.id`` (int) | ``project.uid`` (UUID4 hex @genesis) | Fresh UID, kept in memory map |
| ``name`` | ``name`` | Direct |
| ``description`` | ``description`` | Direct |
| ``start_date`` (DATE) | ``start_date`` (date) | Direct; ``to_utc`` not needed (date, not datetime) |
| ``deadline`` (DATE) | ``deadline`` (date) | Same |
| ``status`` | ``status`` | TODO -> NEEDS_ACTION, IN_PROGRESS -> IN_PROGRESS, COMPLETED -> COMPLETED, CANCELED -> CANCELED |

### Task

| Legacy | New | Notes |
|---|---|---|
| ``task.id`` | ``task.uid`` | Fresh UID, kept in map |
| ``title`` | ``summary`` | Renamed |
| ``description`` | ``description`` | Direct |
| ``created_at`` | ``created`` | Naive -> coerced via ``to_utc`` |
| ``completed_at`` | ``completed_at`` | Same |
| ``hard_deadline`` | ``hard_deadline`` | Same |
| ``assigned_date`` | ``assigned_date`` | date, not datetime |
| ``scheduled_start`` | ``scheduled_start`` | Naive -> ``to_utc`` |
| ``duration_minutes`` | ``scheduled_duration`` | Renamed |
| ``status`` | ``status`` | See below |
| (joined) ``project_id`` | ``project_id`` | remapped via old-id -> new-uid table |

Task status mapping:

| Legacy | New | Notes |
|---|---|---|
| TODO | NEEDS_ACTION | |
| IN_PROGRESS | IN_PROGRESS | |
| COMPLETED | COMPLETED | |
| CANCELED | CANCELED | |
| BACKLOG | NEEDS_ACTION | With optional ``[BACKLOG]`` title prefix -- see decisions |

### Journal entry

| Legacy | New | Notes |
|---|---|---|
| ``id`` | ``uid`` | Fresh UUID4 hex |
| ``entry_type`` | ``type`` | See below |
| ``title`` | ``title`` | Direct |
| ``content`` | ``content`` | Direct |
| ``reference_date`` | ``date`` | Renamed |
| ``created_at`` / ``updated_at`` | (filesystem mtime) | New file's mtime is import time; legacy timestamps are dropped |
| ``project_id`` | (none) | New journal model has no project link. See decisions. |

Journal type mapping:

| Legacy | New |
|---|---|
| DAILY | DAILY |
| WEEKLY | WEEKLY |
| MONTHLY | MONTHLY |
| YEARLY | YEARLY |
| PROJECT | PROJECT |
| GENERAL | MISC |

## File / module layout

New directory: ``scripts/migrate_from_legacy_sqlite.py`` (one file).
CLI entry point, run as ``uv run python scripts/migrate_from_legacy_sqlite.py ...``.

Internal structure of the script:

1. ``argparse`` CLI: ``--db PATH``, ``--apply`` (without this, dry run), ``--preserve-backlog`` (see decisions), ``--wipe-target`` (see decisions), ``--verbose``.
2. ``read_projects(conn) -> Iterator[LegacyProject]``
3. ``read_tasks(conn) -> Iterator[LegacyTask]``
4. ``read_journal_entries(conn) -> Iterator[LegacyJournal]``
5. ``MigrationContext``: holds the ``old_id -> new_uid`` maps for projects and tasks.
6. ``migrate_projects(ctx, facade) -> int`` (rows inserted)
7. ``migrate_tasks(ctx, facade) -> int``
8. ``migrate_journal(ctx, facade) -> int``
9. ``main()``: prints summary table at the end regardless of mode.

## Pass order

1. Projects -> populate ``project_id_map``.
2. Tasks -> look up project in ``project_id_map``; populate ``task_id_map`` (for completeness, not strictly needed since journal does not carry a task link).
3. Journal entries -> independent of the maps. Writes rely on the markdown facade.

## Safety properties

- Default run is dry-run. No writes unless ``--apply`` is passed.
- Per-row try / except. One bad row prints an error and continues.
- Summary printed at the end with per-table inserted / skipped / failed counts.
- Idempotency: each row's content identity is not currently checked
  (the new UID is fresh each run, so re-running will create duplicates).
  Re-running is therefore discouraged; the user should wipe and re-run,
  or restart with a fresh sqlite snapshot.

## Decisions needed before coding

1. **BACKLOG tasks (12 rows)**: default = fold into NEEDS_ACTION with
   no special marking. Optionally, ``--preserve-backlog`` rewrites the
   title to ``[BACKLOG] {original_title}`` so the legacy meaning is visible.
   Pick a default. I lean toward *no prefix*, since the new task UI
   has no backlog concept and adding a prefix everywhere clutters titles.

2. **Journal ``project_id`` link** (16 PROJECT-type entries + 2 DAILY
   entries with project_id set, plus others): the new journal model
   has no project link. Options:

   - (a) Drop silently. The entry's title / content is preserved.
   - (b) Append a markdown line ``> Project: {name}`` at the bottom of
     the entry body.
   - (c) Prepend to the title: ``"[Project: X] {title}"``.

   Pick one. I lean toward (b) — visible only if the user opens the
   entry, doesn't change the title (which the user uses as a label).

3. **Wipe the local Radicale + journal before migrate**: the test
   data we put in during focus-timer development is still there. Wipe
   first so the migration is a clean baseline. **Explicit confirmation
   required** (irreversible). The script will refuse to apply without
   ``--wipe-target`` if it detects existing rows.

4. **Existing rows already in new system (across runs / partial migration)**:
   refuse to apply if counts look non-trivial (say > 5) without
   ``--wipe-target``. Dry-run always runs regardless.

## Out of scope

- CardDAV / contacts (not in old sqlite).
- Time logs / focus sessions (not in old sqlite). Those will start fresh
  in the new system.
- Event / calendar entries (not in old sqlite).
- Multi-user migration. Old sqlite only covers one user.

## Verification

After ``--apply``:

- ``make test`` still passes (sanity).
- Spot-check the new caldav server:
  ``uv run python -c "from genesis.core.facade import Facade; f = Facade(); print(len(f.caldav.list_projects())); print(len(f.caldav.list_tasks()))"``
  -> expect 20 projects, 400 tasks.
- ``ls ~/.local/share/genesis/default/journal/`` should show 114 markdown files (or 114 - N if a few failed and were skipped).
- Open Streamlit: projects page has 20, tasks page has 400, calendar empty, journal has 114. Sanity match against the source DB.

## File diff estimate

- New file: ``scripts/migrate_from_legacy_sqlite.py`` (~250 lines including type hints, careful error handling, summary printer).
- New file: ``docs/plans/migrate-legacy-sqlite.md`` (this doc).
- No tests: one-shot script, no unit-test target.

## Open question to user before I start coding

Please decide on items 1, 2, 3, 4 above.
