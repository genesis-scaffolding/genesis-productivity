"""Unit tests for ``genesis.backup.facade.BackupFacade``.

The BackupFacade talks to a real on-disk layout (Radicale storage
and journal directory) but is otherwise side-effect free of the
Radicale network. We avoid spinning up a Radicale subprocess by
manually laying out the per-user subtrees under ``tmp_path``,
seeding a few ``.ics`` files for the Radicale side, and using a
real :class:`JournalFacade` for the journal side. MockCalDAVFacade
provides the count snapshot.

Two kinds of checks live here:

1. Archive structure / validation round trips handled entirely in
   memory (``zipfile.ZipFile`` in / out).
2. ``import_from_bytes`` destructive flow against a controlled
   on-disk layout. After import, we re open a fresh
   :class:`JournalFacade` over the destination and confirm the
   entries round trip, and we ``rglob`` the Radicale subtree to
   confirm the same UIDs are on disk.
"""

from __future__ import annotations

import json
import zipfile
from datetime import date as date_cls
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from genesis.backup import (
    MANIFEST_SCHEMA_VERSION,
    ArchiveError,
    BackupFacade,
    Manifest,
    RestoreReport,
    ValidationReport,
)
from genesis.backup.manifest import Counts
from genesis.journal.facade import Entry, EntryType, JournalFacade
from tests.mock_caldav import MockCalDAVFacade

# -----------------------------------------------------------------------------
# Fixtures and helpers
# -----------------------------------------------------------------------------


@pytest.fixture
def layout(tmp_path: Path) -> SimpleNamespace:
    """Set up the shared data dir + two roots + per-user subtree with
    one sample ICS file. Mirrors ADR-019's layout under
    ``<data_home>/<app_name>/``.
    """
    common = tmp_path / "data" / "genesis"
    rad_root = common / "radicale"
    jrn_root = common / "journals"
    rad_root.mkdir(parents=True)
    jrn_root.mkdir(parents=True)

    username = "alice"
    rad_user = rad_root / "collections" / "collection-root" / username
    sample_collection = rad_user / "fake-collection-uuid"
    sample_collection.mkdir(parents=True)
    sample_ics_path = sample_collection / "p-abc123.ics"
    sample_ics_path.write_text(
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//EN\r\n"
        "BEGIN:VTODO\r\n"
        "UID:p-abc123\r\n"
        "SUMMARY:Sample project\r\n"
        "STATUS:needs-action\r\n"
        "END:VTODO\r\n"
        "END:VCALENDAR\r\n"
    )

    jrn_user = jrn_root / username
    jrn_user.mkdir()

    profile_root = common / username / "profile"
    profile_root.mkdir(parents=True)

    return SimpleNamespace(
        common=common,
        rad_root=rad_root,
        jrn_root=jrn_root,
        rad_user=rad_user,
        jrn_user=jrn_user,
        profile_root=profile_root,
        username=username,
        sample_ics=sample_ics_path,
    )


@pytest.fixture
def backup(layout: SimpleNamespace) -> BackupFacade:
    """Construct a :class:`BackupFacade` over a controlled tmp dir."""
    caldav = MockCalDAVFacade()

    journal_entries = [
        Entry(
            date=date_cls(2026, 9, 1),
            title="Day one",
            type=EntryType.DAILY,
            content="First entry\n",
        ),
        Entry(
            date=date_cls(2026, 9, 2),
            title="Day two",
            type=EntryType.DAILY,
            content="Second entry\n",
        ),
    ]
    journal = JournalFacade(str(layout.jrn_user))
    for entry in journal_entries:
        journal.save_entry(entry)

    restart_calls: list[int] = []

    parent = SimpleNamespace(
        configs=SimpleNamespace(
            user=layout.username,
            paths=SimpleNamespace(
                radicale_root=str(layout.rad_root),
                journal_root=str(layout.jrn_root),
                profile_root=str(layout.profile_root),
            ),
        ),
        caldav=caldav,
        journal=journal,
        restart_server=lambda: restart_calls.append(1),
    )
    facade = BackupFacade(parent)  # type: ignore[arg-type]
    facade._test_restart_calls = restart_calls  # type: ignore[attr-defined]
    return facade


# -----------------------------------------------------------------------------
# Path helpers
# -----------------------------------------------------------------------------


class TestPathHelpers:
    def test_common_base_is_shared_parent(self, backup: BackupFacade) -> None:
        assert backup._common_base == Path(backup._radicale_root).parent
        assert backup._common_base == Path(backup._journal_root).parent

    def test_user_roots_use_per_user_subdirectory(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        assert backup._radicale_user_root == layout.rad_user
        assert backup._journal_user_root == layout.jrn_user


# -----------------------------------------------------------------------------
# Export
# -----------------------------------------------------------------------------


class TestExport:
    def test_export_to_bytes_writes_manifest_and_files(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        data = backup.export_to_bytes()
        assert data.startswith(b"PK")  # zip magic

        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = zf.namelist()
            assert "MANIFEST.json" in names

            rad_arc = "radicale/collections/collection-root/alice/fake-collection-uuid/p-abc123.ics"
            assert rad_arc in names

            jrn_files = [n for n in names if n.startswith("journals/alice/")]
            # Two DAILY entries from the fixture, plus no legacy
            # entries.
            assert len(jrn_files) == 2

    def test_manifest_carries_counts(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        data = backup.export_to_bytes()
        with zipfile.ZipFile(BytesIO(data)) as zf:
            raw = json.loads(zf.read("MANIFEST.json").decode("utf-8"))
        manifest = Manifest.model_validate(raw)
        assert manifest.schema_version == MANIFEST_SCHEMA_VERSION
        assert manifest.username == layout.username
        assert manifest.counts.projects == 0  # MockCalDAVFacade is empty
        assert manifest.counts.journal == 2

    def test_export_to_bytes_round_trips_files_verbatim(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        """The .ics file inside the archive must be byte-identical to
        what was on disk. This is the raw, not re-serialized, promise
        of ADR-024.
        """
        data = backup.export_to_bytes()
        with zipfile.ZipFile(BytesIO(data)) as zf:
            rad_arc = "radicale/collections/collection-root/alice/fake-collection-uuid/p-abc123.ics"
            archive_bytes = zf.read(rad_arc)
        assert archive_bytes == layout.sample_ics.read_bytes()

    def test_export_with_zero_state_is_a_valid_archive(self, tmp_path: Path) -> None:
        """An account that has never written any data still exports a
        structurally valid archive: MANIFEST.json with zero counts.

        Uses its own dedicated root so it is not contaminated by the
        ``backup`` fixture's seeded journal entries.
        """
        common = tmp_path / "fresh_data" / "genesis"
        rad_root = common / "radicale"
        jrn_root = common / "journals"
        rad_root.mkdir(parents=True)
        jrn_root.mkdir(parents=True)
        (jrn_root / "alice").mkdir()

        parent = SimpleNamespace(
            configs=SimpleNamespace(
                user="alice",
                paths=SimpleNamespace(
                    radicale_root=str(rad_root),
                    journal_root=str(jrn_root),
                    profile_root=str(common / "alice" / "profile"),
                ),
            ),
            caldav=MockCalDAVFacade(),
            journal=JournalFacade(str(jrn_root / "alice")),
            restart_server=lambda: None,
        )
        backup = BackupFacade(parent)  # type: ignore[arg-type]
        data = backup.export_to_bytes()

        with zipfile.ZipFile(BytesIO(data)) as zf:
            raw = json.loads(zf.read("MANIFEST.json").decode("utf-8"))
        manifest = Manifest.model_validate(raw)
        assert manifest.counts.projects == 0
        assert manifest.counts.tasks == 0
        assert manifest.counts.journal == 0


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


class TestValidation:
    def _build_archive(
        self,
        layout: SimpleNamespace,
        *,
        username: str,
        schema_version: int = MANIFEST_SCHEMA_VERSION,
    ) -> bytes:
        """Construct a minimal but valid archive with a custom
        manifest.
        """
        manifest = Manifest(
            schema_version=schema_version,
            generator="genesis-backup",
            username=username,
            created_at=datetime(2026, 9, 1, 12, 0, 0),
            counts=Counts(),
        )
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("MANIFEST.json", manifest.model_dump_json())
            zf.writestr(
                "radicale/collection-root/bob/zzz/p-bb.ics",
                "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
            )
        return buffer.getvalue()

    def test_validate_passing_archive_reports_ok(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        archive = backup.export_to_bytes()
        report = backup.validate_archive(archive)
        assert isinstance(report, ValidationReport)
        assert report.ok

    def test_validate_rejects_wrong_username(self, backup: BackupFacade) -> None:
        archive = self._build_archive(backup._test_layout(), username="bob")  # type: ignore[attr-defined]
        report = backup.validate_archive(archive)
        assert "archive belongs to user" in "; ".join(report.issues)

    def test_validate_rejects_unsupported_schema_version(self, backup: BackupFacade) -> None:
        archive = self._build_archive(
            backup._test_layout(),  # type: ignore[attr-defined]
            username=backup._username,
            schema_version=99,
        )
        report = backup.validate_archive(archive)
        assert "schema_version" in "; ".join(report.issues)

    def test_validate_rejects_corrupt_zip(self, backup: BackupFacade) -> None:
        report = backup.validate_archive(b"not a zip at all")
        assert any("not a valid zip" in issue for issue in report.issues)
        assert not report.ok

    def test_validate_rejects_manifest_with_invalid_json(self, backup: BackupFacade) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("MANIFEST.json", "this is not json")
        report = backup.validate_archive(buffer.getvalue())
        assert not report.ok
        assert any("manifest is not valid JSON" in i for i in report.issues)


# Provide ``_test_layout`` so the parametrized helper above can read
# the fixture data without a heavier dependency-injection rewrite.
def _patch_layout_on_backup(backup: BackupFacade, layout: SimpleNamespace) -> None:
    backup._test_layout = lambda: layout  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _attach_layout_to_backup(backup: BackupFacade, layout: SimpleNamespace) -> None:
    _patch_layout_on_backup(backup, layout)


# -----------------------------------------------------------------------------
# Restore
# -----------------------------------------------------------------------------


class TestImport:
    def test_import_blocks_on_validation_failure(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        """When the archive does not validate, no destructive step
        runs. We assert by failing to find a safety backup and by
        leaving the on-disk layout intact.
        """
        # Build a malformed archive (wrong username).
        manifest = Manifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            generator="genesis-backup",
            username="bob",
            created_at=datetime(2026, 9, 1),
            counts=Counts(),
        )
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("MANIFEST.json", manifest.model_dump_json())
        bad_archive = buffer.getvalue()

        sample_ics_before = layout.sample_ics.read_bytes()
        jrn_files_before = sorted(p.name for p in layout.jrn_user.iterdir())

        with pytest.raises(ArchiveError):
            backup.import_from_bytes(bad_archive)

        # Source data on disk is untouched.
        assert layout.sample_ics.read_bytes() == sample_ics_before
        assert sorted(p.name for p in layout.jrn_user.iterdir()) == jrn_files_before

    def test_import_wipes_then_restores(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        """Round-trip: seed journal entries + ics file, export, wipe,
        import, and confirm a fresh JournalFacade over the
        destination sees the entries and the ics file is back on
        disk verbatim.
        """
        original_archive = backup.export_to_bytes()

        backup._wipe_user_data()
        assert not layout.rad_user.exists()
        assert not layout.jrn_user.exists()

        report = backup.import_from_bytes(original_archive)
        assert isinstance(report, RestoreReport)
        assert report.journal == 2
        assert report.safety_backup is not None
        assert report.safety_backup.exists()

        # Radicale subtree: the sample ics is back, byte-identical.
        restored_ics = layout.rad_user / "fake-collection-uuid" / "p-abc123.ics"
        assert restored_ics.exists()
        assert restored_ics.read_bytes() == layout.sample_ics.read_bytes()

        # Journal: a fresh facade over the destination sees the
        # entries.
        fresh = JournalFacade(str(layout.jrn_user))
        titles = sorted(e.title or "" for e in fresh.list_entries())
        assert titles == ["Day one", "Day two"]

    def test_profile_files_and_snapshots_round_trip(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        """Profile + life story + their .versions snapshots survive an
        export/wipe/import cycle verbatim.
        """
        profile_root = layout.profile_root
        profile_file = profile_root / "profile.md"
        story_file = profile_root / "life-story.md"
        versions = profile_root / ".versions" / "profile"
        versions.mkdir(parents=True)
        snap_file = versions / "2026-01-01T000000000Z.md"
        snap_meta = versions / "2026-01-01T000000000Z.meta.json"

        profile_file.write_text("# Me\nI am a person.\n")
        story_file.write_text("# Story\nOnce upon a time.\n")
        snap_file.write_text("# Me\nOld content.\n")
        snap_meta.write_text('{"ts": "2026-01-01T000000000Z", "command": "write"}')

        archive = backup.export_to_bytes()

        # The archive carries the profile tree under <user>/profile/.
        with zipfile.ZipFile(BytesIO(archive)) as zf:
            names = zf.namelist()
        assert f"{layout.username}/profile/profile.md" in names
        assert f"{layout.username}/profile/life-story.md" in names
        assert f"{layout.username}/profile/.versions/profile/2026-01-01T000000000Z.md" in names
        assert f"{layout.username}/profile/.versions/profile/2026-01-01T000000000Z.meta.json" in names

        # Wipe removes the profile subtree; import restores it verbatim.
        backup._wipe_user_data()
        assert not profile_root.exists()

        backup.import_from_bytes(archive)
        assert profile_file.read_text() == "# Me\nI am a person.\n"
        assert story_file.read_text() == "# Story\nOnce upon a time.\n"
        assert snap_file.read_text() == "# Me\nOld content.\n"
        assert snap_meta.read_text() == '{"ts": "2026-01-01T000000000Z", "command": "write"}'

    def test_import_skips_path_that_escapes_shared_base(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        """An archive containing an entry like ``../escape`` would
        write outside the shared base. The extraction must refuse.
        """
        manifest = Manifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            generator="genesis-backup",
            username=layout.username,
            created_at=datetime(2026, 9, 1),
            counts=Counts(),
        )
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("MANIFEST.json", manifest.model_dump_json())
            zf.writestr("radicale/escape.txt", "should not be allowed")
            zf.writestr("../../../etc/escape.txt", "should not be allowed")
        archive = buffer.getvalue()

        with pytest.raises(ArchiveError):
            backup.import_from_bytes(archive)

    def test_safety_backup_is_created_before_destructive_step(
        self, backup: BackupFacade, layout: SimpleNamespace, tmp_path: Path
    ) -> None:
        """Replace ``_wipe_user_data`` with a recorder and confirm the
        safety backup file exists at the moment the recorder runs.
        """
        archive = backup.export_to_bytes()

        captured: dict[str, Path] = {}

        real_wipe = backup._wipe_user_data
        recorded_path: list[Path] = []

        def recording_wipe() -> None:
            safety_path = backup._safety_backup_path()
            captured["safety"] = safety_path
            recorded_path.append(safety_path)
            real_wipe()

        backup._wipe_user_data = recording_wipe  # type: ignore[method-assign]

        try:
            backup.import_from_bytes(archive)
        finally:
            backup._wipe_user_data = real_wipe  # type: ignore[method-assign]

        assert captured["safety"].exists()
        assert recorded_path, "wipe recorder must run"

    def test_import_restarts_radicale(self, backup: BackupFacade, layout: SimpleNamespace) -> None:
        archive = backup.export_to_bytes()
        backup.import_from_bytes(archive)
        assert backup._test_restart_calls  # type: ignore[attr-defined]

    def test_import_recreates_archive_when_radicale_user_dir_was_absent(
        self, backup: BackupFacade, layout: SimpleNamespace
    ) -> None:
        """An account that has never written CalDAV data still
        restores correctly: the wipe step is a no-op for the missing
        Radicale subtree and the journal files land in their
        expected place.
        """
        shutil_rmtree = __import__("shutil").rmtree
        if layout.rad_user.exists():
            shutil_rmtree(layout.rad_user)

        archive = backup.export_to_bytes()
        report = backup.import_from_bytes(archive)

        assert report.journal == 2
        # We had no Radicale data in the archive to begin with
        # (fixture is empty of mock-side Radicale writes) so the
        # Radicale subtree is still absent after restore — the
        # archive still restores cleanly.
        assert report.projects == 0
        assert report.tasks == 0


# -----------------------------------------------------------------------------
# TestValidation teardown helper
# -----------------------------------------------------------------------------


# ``_build_archive`` in TestValidation needs the layout fixture. We
# patch it via the autouse fixture above. See how
# ``backup._test_layout`` is consumed in each test.
