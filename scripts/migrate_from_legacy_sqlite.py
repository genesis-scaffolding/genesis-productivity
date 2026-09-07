"""Migrate legacy sqlite data into the new CalDAV + markdown backend.

Source: a single user_private.db from the previous productivity
system. Four tables: project, task, projecttasklink, journalentry.

Target: CalDAV via Facade.caldav (three collections: projects,
tasks, events) and journal via Facade.journal (markdown files).

Usage
-----
Read the legacy DB and print a per-row plan without writing::

    uv run python scripts/migrate_from_legacy_sqlite.py \\
        --db /tmp/migration-data/user_private.db

Apply the migration (wipes local Radicale + journal first)::

    uv run python scripts/migrate_from_legacy_sqlite.py \\
        --db /tmp/migration-data/user_private.db \\
        --wipe-target --apply

Idempotency
-----------
The script always wipes local Radicale + journal before applying,
so re-running on top of a partially-migrated state is safe. Each
run starts from a clean target.

Fidelity notes (losses)
-----------------------
- Task ``created_at`` from legacy is lost; the new system sets
  ``created`` to migration time at task creation. Migration captures
  ``completed_at`` is not directly settable via TaskUpdate.
- Journal ``created_at`` / ``updated_at`` from legacy are dropped;
  the new system's filesystem mtime reflects import time.
- Task status ``BACKLOG`` is folded into ``NEEDS_ACTION``. By user
  decision, no title prefix is added.
- Journal ``project_id`` is preserved as a footer line in the entry
  body: ``> Project: {name}``.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from genesis.core.facade import Facade
from genesis.entities import (
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)
from genesis.journal.facade import Entry, EntryType


# -----------------------------------------------------------------------------
# Status / type mappings
# -----------------------------------------------------------------------------


PROJECT_STATUS_MAP: dict[str, ProjectStatus] = {
    "TODO": ProjectStatus.NEEDS_ACTION,
    "IN_PROGRESS": ProjectStatus.IN_PROGRESS,
    "COMPLETED": ProjectStatus.COMPLETED,
    "CANCELED": ProjectStatus.CANCELED,
}

TASK_STATUS_MAP: dict[str, TaskStatus] = {
    "TODO": TaskStatus.NEEDS_ACTION,
    "IN_PROGRESS": TaskStatus.IN_PROGRESS,
    "COMPLETED": TaskStatus.COMPLETED,
    "CANCELED": TaskStatus.CANCELED,
    # BACKLOG folded into NEEDS_ACTION by user decision.
    "BACKLOG": TaskStatus.NEEDS_ACTION,
}

JOURNAL_TYPE_MAP: dict[str, EntryType] = {
    "DAILY": EntryType.DAILY,
    "WEEKLY": EntryType.WEEKLY,
    "MONTHLY": EntryType.MONTHLY,
    "YEARLY": EntryType.YEARLY,
    "PROJECT": EntryType.PROJECT,
    "GENERAL": EntryType.MISC,
}


# -----------------------------------------------------------------------------
# Legacy dataclasses
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacyProject:
    id: int
    name: str
    description: str | None
    start_date: date | None
    deadline: date | None
    status: str


@dataclass(frozen=True)
class LegacyTask:
    id: int
    title: str
    description: str | None
    created_at: datetime
    completed_at: datetime | None
    hard_deadline: datetime | None
    assigned_date: date | None
    scheduled_start: datetime | None
    duration_minutes: int | None
    status: str
    project_id: int | None


@dataclass(frozen=True)
class LegacyJournal:
    id: int
    entry_type: str
    title: str | None
    content: str
    project_id: int | None
    reference_date: date


# -----------------------------------------------------------------------------
# Parsers
# -----------------------------------------------------------------------------


def _parse_dt(s: object) -> datetime | None:
    """Parse a sqlite datetime string. Returns None if missing or unparseable."""
    if not s or not isinstance(s, str):
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f000",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_date(s: object) -> date | None:
    """Parse a sqlite date string."""
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# -----------------------------------------------------------------------------
# SQLite readers
# -----------------------------------------------------------------------------


def read_projects(conn: sqlite3.Connection) -> list[LegacyProject]:
    cur = conn.execute(
        "SELECT id, name, description, start_date, deadline, status FROM project"
    )
    rows = cur.fetchall()
    out: list[LegacyProject] = []
    for r in rows:
        out.append(
            LegacyProject(
                id=r["id"],
                name=r["name"] or "",
                description=r["description"],
                start_date=_parse_date(r["start_date"]),
                deadline=_parse_date(r["deadline"]),
                status=r["status"] or "TODO",
            )
        )
    return out


def read_tasks(conn: sqlite3.Connection) -> list[LegacyTask]:
    """Read tasks with their project_id (if any) via the link table."""
    sql = """
        SELECT t.id, t.title, t.description, t.created_at, t.completed_at,
               t.hard_deadline, t.assigned_date, t.scheduled_start,
               t.duration_minutes, t.status, l.project_id
        FROM task t
        LEFT JOIN projecttasklink l ON l.task_id = t.id
    """
    cur = conn.execute(sql)
    rows = cur.fetchall()
    out: list[LegacyTask] = []
    for r in rows:
        created = _parse_dt(r["created_at"]) or datetime(1970, 1, 1)
        out.append(
            LegacyTask(
                id=r["id"],
                title=r["title"] or "",
                description=r["description"],
                created_at=created,
                completed_at=_parse_dt(r["completed_at"]),
                hard_deadline=_parse_dt(r["hard_deadline"]),
                assigned_date=_parse_date(r["assigned_date"]),
                scheduled_start=_parse_dt(r["scheduled_start"]),
                duration_minutes=r["duration_minutes"],
                status=r["status"] or "TODO",
                project_id=r["project_id"],
            )
        )
    return out


def read_journal_entries(conn: sqlite3.Connection) -> list[LegacyJournal]:
    cur = conn.execute(
        "SELECT id, entry_type, title, content, project_id, reference_date "
        "FROM journalentry"
    )
    rows = cur.fetchall()
    out: list[LegacyJournal] = []
    for r in rows:
        ref_date = _parse_date(r["reference_date"]) or date(1970, 1, 1)
        out.append(
            LegacyJournal(
                id=r["id"],
                entry_type=r["entry_type"] or "GENERAL",
                title=r["title"],
                content=r["content"] or "",
                project_id=r["project_id"],
                reference_date=ref_date,
            )
        )
    return out


# -----------------------------------------------------------------------------
# Migration passes
# -----------------------------------------------------------------------------


def migrate_projects(
    projects: list[LegacyProject],
    id_map: dict[int, str],
    facade: Facade,
    *,
    apply: bool,
) -> tuple[int, int]:
    inserted = 0
    failed = 0
    for p in projects:
        try:
            new_status = PROJECT_STATUS_MAP.get(p.status, ProjectStatus.NEEDS_ACTION)
            data = ProjectCreate(
                name=p.name,
                description=p.description,
                start_date=p.start_date,
                deadline=p.deadline,
            )
            if apply:
                new = facade.caldav.create_project(data)
                if new.status != new_status:
                    facade.caldav.update_project(
                        new.uid, ProjectUpdate(status=new_status)
                    )
                id_map[p.id] = new.uid
            else:
                print(
                    f"  [proj]  {p.name!r}  status={new_status.value} "
                    f"start={p.start_date} deadline={p.deadline}"
                )
            inserted += 1
        except Exception as e:
            print(f"  ! project id={p.id} {p.name!r}: {e}")
            failed += 1
    return inserted, failed


def migrate_tasks(
    tasks: list[LegacyTask],
    project_id_map: dict[int, str],
    facade: Facade,
    *,
    apply: bool,
) -> tuple[int, int]:
    inserted = 0
    failed = 0
    for t in tasks:
        try:
            new_status = TASK_STATUS_MAP.get(t.status, TaskStatus.NEEDS_ACTION)
            project_uid = project_id_map.get(t.project_id) if t.project_id else None
            data = TaskCreate(
                title=t.title,
                description=t.description,
                hard_deadline=t.hard_deadline,
                assigned_date=t.assigned_date,
                scheduled_start=t.scheduled_start,
                scheduled_duration=t.duration_minutes,
                project_id=project_uid,
            )
            if apply:
                new = facade.caldav.create_task(data)
                if new.status != new_status:
                    facade.caldav.update_task(
                        new.uid, TaskUpdate(status=new_status)
                    )
            else:
                has_proj = bool(project_uid)
                print(
                    f"  [task]  {t.title!r}  status={new_status.value} "
                    f"project={'yes' if has_proj else 'no'} "
                    f"deadline={t.hard_deadline} "
                    f"scheduled={t.scheduled_start} dur={t.duration_minutes}m"
                )
            inserted += 1
        except Exception as e:
            print(f"  ! task id={t.id} {t.title!r}: {e}")
            failed += 1
    return inserted, failed


def migrate_journal(
    entries: list[LegacyJournal],
    project_name_map: dict[int, str],
    facade: Facade,
    *,
    apply: bool,
) -> tuple[int, int]:
    inserted = 0
    failed = 0
    for e in entries:
        try:
            new_type = JOURNAL_TYPE_MAP.get(e.entry_type)
            if new_type is None:
                print(f"  ! journal id={e.id} unknown type {e.entry_type!r}")
                failed += 1
                continue

            content = e.content
            if e.project_id and e.project_id in project_name_map:
                proj_name = project_name_map[e.project_id]
                content = content.rstrip() + f"\n\n> Project: {proj_name}\n"

            if apply:
                entry = Entry(
                    date=e.reference_date,
                    title=e.title,
                    type=new_type,
                    content=content,
                )
                facade.journal.save_entry(entry)
            else:
                proj_note = (
                    f" -> Project: {project_name_map[e.project_id]}"
                    if e.project_id in project_name_map
                    else ""
                )
                print(
                    f"  [jour] type={new_type.value} date={e.reference_date} "
                    f"title={e.title!r}{proj_note}"
                )
            inserted += 1
        except Exception as ex:
            print(f"  ! journal id={e.id} {e.title!r}: {ex}")
            failed += 1
    return inserted, failed


# -----------------------------------------------------------------------------
# Target wiping and Radicale management
# -----------------------------------------------------------------------------


def _radicale_data_path() -> Path:
    return Path.home() / ".local" / "share" / "genesis" / "default" / "radicale"


def _journal_path() -> Path:
    return Path.home() / ".local" / "share" / "genesis" / "default" / "journal"


def _radicale_config_path() -> Path:
    return _radicale_data_path() / "config"


def _radicale_binary() -> Path:
    import sys as _sys

    candidate = Path(_sys.executable).parent / "radicale"
    if candidate.exists():
        return candidate
    import shutil

    found = shutil.which("radicale")
    if found:
        return Path(found)
    raise FileNotFoundError("radicale executable not found on PATH")


def _spawn_radicale() -> subprocess.Popen:
    """Start a fresh Radicale as a child process.

    Caller is responsible for terminating the returned object.

    Implementation detail: we deliberately manage Radicale ourselves
    rather than going through ``Facad`` (whose ``ServerManager.start``
    has a known race condition where is_running() can see a TIME_WAIT
    socket right after a kill and short-circuit, leaving the
    migration with a dead-but-port-bound server).
    """
    cfg = _radicale_config_path()
    if not cfg.exists():
        raise RuntimeError(
            f"Radicale config not found at {cfg}. "
            "Run a Facade() once with ensure_server=True to bootstrap it."
        )
    proc = subprocess.Popen(
        [_radicale_binary(), "-C", str(cfg)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_radicale_ready()
    return proc


def _wait_for_radicale_ready() -> None:
    """Block until Radicale answers an HTTP probe on port 5232."""
    import socket
    import time

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(("127.0.0.1", 5232))
        except (TimeoutError, OSError):
            time.sleep(0.25)
            continue
        # Connected. Try a real HTTP roundtrip with auth.
        try:
            import urllib.request

            req = urllib.request.Request("http://127.0.0.1:5232/")
            import base64

            creds = base64.b64encode(b"default:genesis-local").decode()
            req.add_header("Authorization", f"Basic {creds}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status in (200, 302):
                    return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("Radicale did not become ready within 15s")


def wipe_target() -> None:
    """Wipe local Radicale + journal and verify Radicale has been booted.

    We do not run Radicale here; the caller (``main``) explicitly
    starts a child process right after wiping so the lifecycle is
    fully under our control.
    """
    cfg_path = _radicale_data_path()
    journal_path = _journal_path()

    print(f"Wiping Radicale data: {cfg_path}")
    if cfg_path.exists():
        shutil.rmtree(cfg_path)

    print(f"Wiping journal: {journal_path}")
    if journal_path.exists():
        shutil.rmtree(journal_path)
    journal_path.mkdir(parents=True, exist_ok=True)


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy sqlite user_private.db to the new "
        "CalDAV + markdown system.",
    )
    parser.add_argument("--db", required=True, help="Path to source user_private.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to target system. Without this, dry-run only.",
    )
    parser.add_argument(
        "--wipe-target",
        action="store_true",
        help="Wipe local Radicale + journal before applying.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Per-row output during dry-run (apply is always quiet).",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: db not found: {db_path}", file=sys.stderr)
        return 1
    if args.apply and not args.wipe_target:
        print("ERROR: --apply requires --wipe-target", file=sys.stderr)
        return 1

    apply = args.apply
    verbose = args.verbose or not apply

    print(f"=== Migration {'(APPLY)' if apply else '(DRY RUN)'} ===")
    print(f"Source: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        projects = read_projects(conn)
        tasks = read_tasks(conn)
        journals = read_journal_entries(conn)
    finally:
        conn.close()

    print(
        f"\nRead {len(projects)} projects, {len(tasks)} tasks, "
        f"{len(journals)} journal entries."
    )
    print(
        f"Project statuses: {', '.join(sorted({p.status for p in projects}))}"
    )
    print(
        f"Task statuses: {', '.join(sorted({t.status for t in tasks}))}"
    )
    print(
        f"Journal types: {', '.join(sorted({j.entry_type for j in journals}))}"
    )

    project_id_map: dict[int, str] = {}
    project_name_map: dict[int, str] = {p.id: p.name for p in projects}

    facade: Facade | None = None
    radicale_proc: subprocess.Popen | None = None

    if apply:
        # Bootstrap config once if missing (the first-ever install has
        # no config file; running Facade() is the canonical way to
        # generate one). This is a no-op on subsequent runs.
        if not _radicale_config_path().exists():
            Facade(ensure_server=True).shutdown()
        wipe_target()
        # Re-bootstrap config (wipe removed it) and start a fresh
        # Radicale subprocess we own.
        Facade(ensure_server=True).shutdown()
        print("Starting Radicale child process...")
        radicale_proc = _spawn_radicale()
        facade = Facade(ensure_server=False)
    else:
        if verbose and not apply:
            print(
                "\nNote: dry-run + --verbose prints per-row plan. "
                "No facade is instantiated.\n"
            )

    print()
    if verbose:
        print("--- Projects ---")
    p_ok, p_fail = migrate_projects(
        projects, project_id_map, facade, apply=apply
    )

    if verbose:
        print("--- Tasks ---")
    t_ok, t_fail = migrate_tasks(tasks, project_id_map, facade, apply=apply)

    if verbose:
        print("--- Journal ---")
    j_ok, j_fail = migrate_journal(
        journals, project_name_map, facade, apply=apply
    )

    print()
    print("=== Summary ===")
    print(f"Projects: {p_ok} inserted, {p_fail} failed")
    print(f"Tasks:    {t_ok} inserted, {t_fail} failed")
    print(f"Journal:  {j_ok} inserted, {j_fail} failed")
    print()
    if not apply:
        print("This was a dry run. Pass --apply --wipe-target to actually migrate.")
    if radicale_proc is not None:
        print("Stopping Radicale child process...")
        radicale_proc.terminate()
        try:
            radicale_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            radicale_proc.kill()
            radicale_proc.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
