"""Integration tests for :class:`genesis.backup.BackupFacade`.

These spin up a real Radicale subprocess so the round trip
covers Radicale's on-disk storage format, not just our
in-memory representations. Slow: ~1 s of subprocess startup
per test, plus the few hundred milliseconds each backup /
restore needs to HTTP PUT / GET through Radicale's
calendar-query.

Marked ``@pytest.mark.integration`` so the fast ``make test``
run skips them. See ``tests/conftest.py`` for the project's
testing model.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from genesis.backup import BackupFacade
from genesis.caldav.facade import CalDAVFacade
from genesis.configs import DAV
from genesis.dav.server import ServerManager
from genesis.entities import (
    ProjectCreate,
    TaskCreate,
    TimeLogEntryCreate,
    TimeLogMode,
    TimeLogStatus,
)
from genesis.journal.facade import Entry, EntryType, JournalFacade


@pytest.fixture(scope="function")
def radicale_layout() -> Generator[SimpleNamespace, None, None]:
    """Provision a real Radicale server plus a journal directory.

    Yields a namespace with:

    - ``common``: the shared parent of ``radicale_root`` and ``journal_root``
    - ``rad_root`` / ``jrn_root``: the storage roots
    - ``server_manager``: the running ``ServerManager``; tests should
      call ``manager.stop()`` themselves if they stop it mid test
      (most tests do not)
    - ``username`` / ``password``: the credentials the seeded
      htpasswd has
    - ``jrn_user``: the user's journal directory

    Uses ``tempfile.TemporaryDirectory`` so the data is wiped at the
    end of the test no matter what.
    """
    host = "127.0.0.1"
    port = 39243
    username = "backup-test"
    password = "backup-test-pw"

    with tempfile.TemporaryDirectory(prefix="genesis-backup-int-") as tmpdir:
        data_path = Path(tmpdir) / "data"
        rad_root = data_path / "radicale"
        jrn_root = data_path / "journals"
        rad_root.mkdir(parents=True)
        jrn_root.mkdir(parents=True)
        (jrn_root / username).mkdir()

        dav_config = DAV(
            host=host,
            port=port,
            username=username,
            password=password,
        )
        manager = ServerManager(
            dav_config=dav_config,
            data_path=rad_root,
            startup_timeout=15,
        )
        manager.start()
        manager.ensure_user(username)
        time.sleep(0.3)  # let the server bind before first request

        try:
            yield SimpleNamespace(
                common=data_path,
                rad_root=rad_root,
                jrn_root=jrn_root,
                jrn_user=jrn_root / username,
                username=username,
                password=password,
                server_url=f"http://{host}:{port}",
                server_manager=manager,
            )
        finally:
            manager.stop()


@pytest.fixture
def backup_facade(
    radicale_layout: SimpleNamespace,
) -> Generator[SimpleNamespace, None, None]:
    """Yield a wired-up :class:`BackupFacade` against the real
    Radicale server plus the on-disk journal directory.
    """
    caldav = CalDAVFacade(
        server_url=radicale_layout.server_url,
        username=radicale_layout.username,
        password=radicale_layout.password,
    )
    journal = JournalFacade(str(radicale_layout.jrn_user))

    def _restart_radicale() -> None:
        """Mirror ``Facade.restart_server`` against the test's
        ServerManager: stop the running process, start a fresh one,
        and re-add the user to the htpasswd so CalDAV lookups keep
        working.
        """
        manager = radicale_layout.server_manager
        manager.stop()
        manager.start()
        manager.ensure_user(radicale_layout.username)
        # Brief settle so the next HTTP request doesn't race against
        # the port rebind.
        time.sleep(0.2)

    backup = BackupFacade(
        SimpleNamespace(  # type: ignore[arg-type]
            configs=SimpleNamespace(
                user=radicale_layout.username,
                paths=SimpleNamespace(
                    radicale_root=str(radicale_layout.rad_root),
                    journal_root=str(radicale_layout.jrn_root),
                ),
            ),
            caldav=caldav,
            journal=journal,
            restart_server=_restart_radicale,
        )
    )

    yield SimpleNamespace(
        backup=backup,
        caldav=caldav,
        journal=journal,
        layout=radicale_layout,
    )


@pytest.mark.integration
def test_round_trip_real_radicale(backup_facade: SimpleNamespace) -> None:
    """Seed across all four collections plus journal entries, export,
    wipe, import, and confirm ``list_*`` returns the same counts.

    This is the single integration test that proves the round trip
    works through Radicale's storage format.
    """
    caldav = backup_facade.caldav
    journal = backup_facade.journal
    backup = backup_facade.backup
    layout = backup_facade.layout

    # Seed: 1 project, 2 tasks (one of which drives a shadow VEVENT),
    # 1 standalone event, 1 time log entry, 2 journal entries.
    project = caldav.create_project(ProjectCreate(name="Round trip"))
    caldav.create_task(TaskCreate(title="Plain task", project_id=project.uid))
    caldav.create_task(
        TaskCreate(
            title="Scheduled task",
            project_id=project.uid,
            scheduled_start=datetime(2026, 9, 5, 9, 0, 0, tzinfo=UTC),
            scheduled_duration=45,
        )
    )
    caldav.create_time_log_entry(
        TimeLogEntryCreate(
            label="Focus block",
            start=datetime(2026, 9, 5, 11, 0, 0, tzinfo=UTC),
            mode=TimeLogMode.COUNTDOWN,
            due=datetime(2026, 9, 5, 11, 30, 0, tzinfo=UTC),
            completed_at=datetime(2026, 9, 5, 11, 30, 0, tzinfo=UTC),
            status=TimeLogStatus.COMPLETED,
            task_id=None,
        )
    )
    journal.save_entry(
        Entry(
            date=__import__("datetime").date(2026, 9, 1),
            title="Day one",
            type=EntryType.DAILY,
            content="Reflecting\n",
        )
    )
    journal.save_entry(
        Entry(
            date=__import__("datetime").date(2026, 9, 2),
            title="Day two",
            type=EntryType.DAILY,
            content="More\n",
        )
    )

    # Export.
    archive = backup.export_to_bytes()

    # Wipe via Radicale DELETE on each item, then drop any leftover
    # files from the journal directory. Going through the public API
    # proves the destructive flow leaves a fully empty state.
    for p in caldav.list_projects():
        caldav.delete_project(p.uid)
    for t in caldav.list_tasks():
        caldav.delete_task(t.uid)
    for e in caldav.list_events():
        caldav.delete_event(e.uid)
    for tl in caldav.list_time_log():
        caldav.delete_time_log_entry(tl.uid)
    for entry in list(journal.list_entries()):
        journal.delete_entry(entry.uid)

    # The CalDAV collections may emit some 404s mid-walk; a sync at
    # the end confirms the per-user subtree is now empty.
    assert caldav.list_projects() == []
    assert caldav.list_tasks() == []
    assert caldav.list_events() == []
    assert caldav.list_time_log() == []
    assert journal.list_entries() == []

    # Restore.
    report = backup.import_from_bytes(archive)
    assert report.projects == 1
    assert report.tasks == 2
    # The task-driven shadow VEVENT is one event; no standalone event.
    assert report.events == 1
    assert report.time_log == 1
    assert report.journal == 2
    assert report.safety_backup is not None
    assert report.safety_backup.exists()

    # Read-back via the public API to confirm the data is intact.
    projects_back = caldav.list_projects()
    assert len(projects_back) == 1
    assert projects_back[0].name == "Round trip"

    tasks_back = caldav.list_tasks()
    assert len(tasks_back) == 2
    assert {t.title for t in tasks_back} == {"Plain task", "Scheduled task"}

    # The scheduled task's shadow VEVENT is still in events.
    events_back = caldav.list_events()
    assert len(events_back) == 1
    assert events_back[0].task_id is not None

    # Journal entries round trip through a fresh facade.
    fresh_journal = JournalFacade(str(layout.jrn_user))
    titles = sorted(e.title or "" for e in fresh_journal.list_entries())
    assert titles == ["Day one", "Day two"]


@pytest.mark.integration
def test_import_against_already_empty_user(backup_facade: SimpleNamespace, tmp_path: Path) -> None:
    """Restoring an archive from one Radicale user into a brand new,
    never-written-to user works without errors."""
    backup = backup_facade.backup
    caldav = backup_facade.caldav

    # Seed a single project so the archive has Radicale data.
    caldav.create_project(ProjectCreate(name="One project"))
    archive = backup.export_to_bytes()

    # Wipe so the user is genuinely empty.
    for p in caldav.list_projects():
        caldav.delete_project(p.uid)
    assert caldav.list_projects() == []

    report = backup.import_from_bytes(archive)
    assert report.projects == 1
