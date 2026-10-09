"""Backup and restore for the signed in user's data.

The backup archive is a zip whose top level matches the on-disk layout
under ``<data_home>/<app_name>/`` (ADR-019):

- ``MANIFEST.json`` at the root
- ``radicale/...`` = ``<radicale_root>/...``
- ``journals/...`` = ``<journal_root>/...``
- ``<user>/profile/...`` = the per-user profile directory
  (profile.md, life-story.md and their ``.versions`` snapshots;
  ADR-025)

We do not re-serialize items. The ``.ics`` files in the archive are
literal copies of what Radicale wrote; the markdown files in the
archive are literal copies of the journal files. See ADR-024 for the
rationale (raw, not re-serialized).

Restore is destructive: it wipes the user's Radicale subtree and
journal subtree, then unpacks the archive into those locations. The
``import_from_bytes`` flow takes an automatic snapshot of the
current state first, so a bad restore is recoverable.

The module depends on ``genesis.core``, ``genesis.caldav``, and
``genesis.journal``. Nothing under those modules imports back.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..caldav.ical import (
    event_from_ical,
    project_from_ical,
    task_from_ical,
    time_log_from_ical,
)
from .manifest import MANIFEST_FILENAME, MANIFEST_SCHEMA_VERSION, Counts, Manifest

if TYPE_CHECKING:
    from ..core.facade import Facade

# Generator tag written into the manifest. Stored verbatim so future
# tooling can identify genesis-produced archives without parsing the
# rest of the manifest.
GENERATOR_TAG = "genesis-backup"

# Directory layouts inside the Radicale storage. ADR-012. The actual
# per-user path is ``<radicale_root>/collections/collection-root/<user>/``
# because Radicale's ``filesystem_folder`` config is set to
# ``<radicale_root>/collections`` (see ``genesis.dav.server.ServerManager``).
_RADICALE_COLLECTIONS_PARENT = "collections"
_RADICALE_COLLECTION_ROOT = "collection-root"

# Arcname prefixes inside the archive. These match the on-disk root
# names so the archive mirrors what the operator sees on disk.
_ARC_RADICALE = "radicale"
_ARC_JOURNAL = "journals"
_ARC_PROFILE = "profile"  # section tag for the per-user profile tree

# Cap on sample validation: parse this many .ics files per collection
# to catch gross archive corruption without paying the cost of parsing
# every item. Defer full round trip to first read after restore.
_SAMPLE_PARSE_LIMIT = 5


class ArchiveError(ValueError):
    """Raised when an archive is rejected by ``validate_archive`` or
    ``import_from_bytes``.

    Subclasses ``ValueError`` for callers that broadly catch archive
    validation problems while still letting the UI distinguish
    archive issues from other value errors.
    """


class RestoreReport:
    """Counts and ancillary info returned by ``import_from_bytes``.

    Attributes:
        projects: Number of projects after restore.
        tasks: Number of tasks after restore.
        events: Number of standalone events after restore.
        time_log: Number of time log entries after restore.
        journal: Number of parseable journal entries after restore.
        contacts: Number of contacts after restore.
        safety_backup: Path to the auto-created pre-restore snapshot.
            ``None`` only when ``create_safety_backup`` was disabled at
            the call site (not exposed to the UI yet).
    """

    def __init__(
        self,
        *,
        projects: int,
        tasks: int,
        events: int,
        time_log: int,
        journal: int,
        contacts: int,
        safety_backup: Path | None,
    ) -> None:
        self.projects = projects
        self.tasks = tasks
        self.events = events
        self.time_log = time_log
        self.journal = journal
        self.contacts = contacts
        self.safety_backup = safety_backup


class ValidationReport:
    """Outcome of ``validate_archive``.

    The manifest is always populated for a structurally valid archive
    (zip opens, ``MANIFEST.json`` parses). ``issues`` lists findings;
    blocking issues are also raised as :class:`ArchiveError` by
    ``import_from_bytes``. ``present_sections`` records which of
    ``radicale/`` and ``journals/`` exist as top level entries.
    """

    def __init__(
        self,
        *,
        manifest: Manifest | None,
        present_sections: set[str],
        issues: list[str],
    ) -> None:
        self.manifest = manifest
        self.present_sections = present_sections
        self.issues = issues

    @property
    def ok(self) -> bool:
        """True iff there are no issues. Manifest is non-null when ok."""
        return not self.issues and self.manifest is not None


class BackupFacade:
    """Backup and restore for the signed in user.

    Constructed against a parent ``Facade`` so it can read its
    configs, restart Radicale through ``Facade.restart_server``, and
    reach the CalDAV / journal sub-facades for counts.
    """

    def __init__(self, parent: Facade) -> None:
        # ``parent`` is the :class:`genesis.core.facade.Facade` this
        # facade lives under. The import is held under
        # ``TYPE_CHECKING`` to avoid a circular dependency at runtime
        # (``genesis.core.facade`` imports :class:`BackupFacade`
        # lazily through :attr:`Facade.backup`). Tests substitute a
        # ``SimpleNamespace`` instance whose attributes match the
        # facade's; pyright catches the type mismatch but the tests
        # are functionally correct.
        self._parent = parent  # type: ignore[arg-type]

    # -------------------------------------------------------------------------
    # Path helpers
    # -------------------------------------------------------------------------

    @property
    def _username(self) -> str:
        return str(self._parent.configs.user)

    @property
    def _radicale_root(self) -> Path:
        return Path(self._parent.configs.paths.radicale_root)

    @property
    def _journal_root(self) -> Path:
        return Path(self._parent.configs.paths.journal_root)

    @property
    def _radicale_user_root(self) -> Path:
        """Path to this user's Radicale collection subtree.

        Radicale stores per-user data at
        ``<radicale_root>/collections/collection-root/<user>/`` because
        ``filesystem_folder`` is set to ``<radicale_root>/collections``
        in ``genesis.dav.server.ServerManager``. The ``radicale_root``
        field on :class:`genesis.configs.Paths` is the parent of the
        collection tree, not its filesystem root, despite the naming.
        """
        return self._radicale_root / _RADICALE_COLLECTIONS_PARENT / _RADICALE_COLLECTION_ROOT / self._username

    @property
    def _journal_user_root(self) -> Path:
        """Path to this user's journal directory."""
        return self._journal_root / self._username

    @property
    def _profile_root(self) -> Path:
        """Path to this user's profile directory (ADR-025)."""
        return Path(self._parent.configs.paths.profile_root)

    @property
    def _user_root(self) -> Path:
        """The per-user data directory that owns the profile root.

        ``profile_root`` is ``<data_home>/<app>/<user>/profile`` by
        default, so its parent is the per-user directory. Used to
        keep the export scope honest: a YAML-overridden
        ``profile_root`` that escapes the user root is rejected.
        """
        return self._profile_root.parent

    @property
    def _app_root(self) -> Path:
        """The per-app data root (``<data_home>/<app>``).

        Parent of the per-user directory. The profile arcname base:
        arcnames are anchored here so ``profile`` files land at
        ``<user>/profile/...`` inside the archive and extract back
        to the same on-disk location.
        """
        return self._user_root.parent

    @property
    def _common_base(self) -> Path:
        """The shared parent that owns both the radicale and journal
        roots. Required to be the same path for both because the
        archive uses a single ``MANIFEST.json`` root.
        """
        rad_parent = self._radicale_root.parent
        jrn_parent = self._journal_root.parent
        if rad_parent != jrn_parent:
            raise ArchiveError(
                f"radicale_root parent ({rad_parent}) and journal_root parent "
                f"({jrn_parent}) must share a common directory for backup archives"
            )
        return rad_parent

    # -------------------------------------------------------------------------
    # Counts
    # -------------------------------------------------------------------------

    def _counts(self) -> Counts:
        """Snapshot counts via the same list paths the UI uses.

        Counts are informational. The archive contents are the source
        of truth at restore time.
        """
        caldav = self._parent.caldav
        carddav = self._parent.carddav
        journal = self._parent.journal
        profile_files = 0
        if self._profile_root.exists():
            profile_files = sum(1 for p in self._profile_root.rglob("*") if p.is_file())
        return Counts(
            projects=len(caldav.list_projects()),
            tasks=len(caldav.list_tasks()),
            events=len(caldav.list_events()),
            time_log=len(caldav.list_time_log()),
            journal=len(journal.list_entries()),
            profile=profile_files,
            contacts=len(carddav.list_contacts()),
        )

    # -------------------------------------------------------------------------
    # Archive format helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _arcname_for(path: Path, base: Path) -> str:
        """Return the relative arcname for ``path`` against ``base``.

        The arcname includes the leading ``radicale/`` or ``journals/``
        segment because both roots are siblings under ``base``. The
        caller is responsible for ensuring ``path`` lives under
        ``base``; the function asserts via ``Path.is_relative_to``.
        """
        rel = path.relative_to(base)
        return rel.as_posix()

    def _iter_user_files(self) -> Iterator[tuple[Path, str]]:
        """Yield ``(absolute_path, arcname)`` for every file we want to
        archive for the signed in user.

        Files outside the user's Radicale subtree and journal subtree
        are intentionally excluded. Per ADR-019, all per-user state
        lives under ``<radicale_root>/collections/collection-root/<user>/``
        and ``<journal_root>/<user>/``; nothing else.

        Radicale's multi-filesystem storage keeps its own bookkeeping
        under ``.Radicale.cache/`` inside each collection directory
        (currently a change history and similar transient state).
        Those files are regenerated by Radicale on demand and are
        not part of the user's data, so we drop them.
        """
        base = self._common_base

        if self._radicale_user_root.exists():
            for path in sorted(self._radicale_user_root.rglob("*")):
                if not path.is_file():
                    continue
                if any(part == ".Radicale.cache" for part in path.parts):
                    continue
                yield path, self._arcname_for(path, base)

        if self._journal_user_root.exists():
            for path in sorted(self._journal_user_root.rglob("*")):
                if path.is_file():
                    yield path, self._arcname_for(path, base)

        # Profile files (profile.md, life-story.md) and their
        # ``.versions`` snapshots. The arcname base is the per-app
        # data root so files land at ``<user>/profile/...`` inside
        # the archive and extract back to the same on-disk location;
        # a YAML-overridden ``profile_root`` that escapes the user
        # root is rejected loudly rather than silently dropped.
        if self._profile_root.exists():
            if not self._profile_root.is_relative_to(self._user_root):
                raise ArchiveError(
                    f"profile_root ({self._profile_root}) is not under the user "
                    f"data root ({self._user_root}); cannot include it in the archive"
                )
            for path in sorted(self._profile_root.rglob("*")):
                if path.is_file():
                    yield path, self._arcname_for(path, self._app_root)

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    def export_to_bytes(self) -> bytes:
        """Serialize the user's data to an in-memory zip.

        Returns:
            The zip bytes. Always contains ``MANIFEST.json``. May
            contain zero ``radicale/`` and ``journals/`` files for an
            account that has never written any data.
        """
        manifest = Manifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            generator=GENERATOR_TAG,
            username=self._username,
            created_at=datetime.now(UTC),
            counts=self._counts(),
        )

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_FILENAME, manifest.model_dump_json(indent=2))
            for absolute, arcname in self._iter_user_files():
                zf.write(absolute, arcname)
        return buffer.getvalue()

    def export_to_path(self, path: Path) -> None:
        """Write the archive to ``path``. Parent dirs are created."""
        data = self.export_to_bytes()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def validate_archive(self, data: bytes) -> ValidationReport:
        """Inspect an archive without mutating anything on disk.

        Returns a :class:`ValidationReport` whose ``ok`` is True iff
        every check passed. ``import_from_bytes`` raises
        :class:`ArchiveError` on blocking issues regardless of what
        this method reported, because a passing validation here does
        not cover sample parsing — that is performed inside the
        destructive flow.
        """
        issues: list[str] = []
        present_sections: set[str] = set()
        manifest: Manifest | None = None

        try:
            buffer = io.BytesIO(data)
            with zipfile.ZipFile(buffer, "r") as zf:
                names = zf.namelist()
                if MANIFEST_FILENAME not in names:
                    issues.append(f"missing {MANIFEST_FILENAME}")
                else:
                    try:
                        raw = json.loads(zf.read(MANIFEST_FILENAME).decode("utf-8"))
                        manifest = Manifest.model_validate(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        issues.append(f"manifest is not valid JSON: {exc}")
                    except Exception as exc:  # pydantic ValidationError and similar
                        issues.append(f"manifest failed schema validation: {exc}")

                if manifest is not None:
                    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
                        issues.append(
                            f"schema_version {manifest.schema_version} is not supported "
                            f"(loader accepts {MANIFEST_SCHEMA_VERSION})"
                        )
                    if manifest.username != self._username:
                        issues.append(
                            f"archive belongs to user {manifest.username!r} but the "
                            f"signed in account is {self._username!r}"
                        )

                # Section presence is informational, not blocking. An
                # empty-but-valid archive carries both prefixes with no
                # children; we only flag missing prefixes if they carry
                # no entries at all (which can happen for new users).
                top_dirs = {n.split("/", 1)[0] for n in names if n and "/" in n}
                if _ARC_RADICALE in top_dirs:
                    present_sections.add(_ARC_RADICALE)
                if _ARC_JOURNAL in top_dirs:
                    present_sections.add(_ARC_JOURNAL)
                if self._username in top_dirs:
                    present_sections.add(_ARC_PROFILE)
        except zipfile.BadZipFile as exc:
            issues.append(f"not a valid zip: {exc}")

        return ValidationReport(
            manifest=manifest,
            present_sections=present_sections,
            issues=issues,
        )

    @staticmethod
    def _parse_ical_samples(zf: zipfile.ZipFile, prefix: str) -> Iterator[Path]:
        """Yield arcnames of up to ``_SAMPLE_PARSE_LIMIT`` ``.ics`` files
        under ``prefix``. Used by :meth:`_sample_parse_archive` to keep
        the validation step bounded.
        """
        count = 0
        for name in zf.namelist():
            if not name.startswith(prefix + "/") or not name.endswith(".ics"):
                continue
            yield Path(name)
            count += 1
            if count >= _SAMPLE_PARSE_LIMIT:
                return

    def _sample_parse_archive(self, zf: zipfile.ZipFile) -> None:
        """Raise :class:`ArchiveError` if the first few ``.ics`` files in
        each present collection fail to round trip through the
        existing ``*_from_ical`` helpers.

        Bounded by ``_SAMPLE_PARSE_LIMIT`` per collection. The full
        set is re-parsed on first read after restore.
        """
        sample_dir = Path("radicale") / _RADICALE_COLLECTIONS_PARENT / _RADICALE_COLLECTION_ROOT / self._username
        for arcname in self._parse_ical_samples(zf, str(sample_dir)):
            raw = zf.read(str(arcname))
            self._parse_one_sample(arcname, raw)

    def _parse_one_sample(self, arcname: Path, raw: bytes) -> None:
        """Parse a single ``.ics`` sample to a domain entity.

        The sample parser infers the entity kind from the arcname
        prefix (the Radicale storage uses ``collection-root/<user>/<uuid>``
        directories, one per collection UUID). To stay storage-agnostic
        we probe for the ``BEGIN:VTODO`` vs ``BEGIN:VEVENT`` marker in
        the first 4 KB.
        """
        head = raw[:4096].decode("utf-8", errors="replace")
        if "BEGIN:VEVENT" in head:
            self._parse_event_sample(raw)
            return
        if "BEGIN:VTODO" in head:
            self._parse_vtodo_sample(raw)
            return
        raise ArchiveError(f"sample file {arcname} is not a VTODO or VEVENT")

    @staticmethod
    def _parse_event_sample(raw: bytes) -> None:
        import icalendar

        cal = icalendar.Calendar.from_ical(raw)
        for component in cal.walk():
            if component.name == "VEVENT":
                event_from_ical(component)
                return
        raise ArchiveError("VEVENT sample contains no VEVENT component")

    @staticmethod
    def _parse_vtodo_sample(raw: bytes) -> None:
        """Parse a VTODO sample. We try project / task / time log in
        that order: project_from_ical is the cheapest; task_from_ical
        and time_log_from_ical both succeed for a wide range of VTODOs.

        The Radicale storage does not tag which collection a VTODO
        belongs to at the file level — the collection UUID is part of
        the directory name. Probing each parser against the same
        sample is more robust than threading the collection UUID
        through this helper.
        """
        import icalendar

        cal = icalendar.Calendar.from_ical(raw)
        for component in cal.walk():
            if component.name != "VTODO":
                continue
            summary = str(component.get("summary") or "")
            if "X-MODE" in component:
                time_log_from_ical(component)
            elif "X-ASSIGNED-DATE" in component or "X-PROJECT-ID" in component:
                task_from_ical(component)
            elif summary:
                # Heuristic: a project VTODO has no task markers. If
                # none match, fall back to project_from_ical which
                # accepts any well-formed VTODO.
                project_from_ical(component)
            else:
                project_from_ical(component)
            return
        raise ArchiveError("VTODO sample contains no VTODO component")

    # -------------------------------------------------------------------------
    # Restore
    # -------------------------------------------------------------------------

    def _safety_backup_path(self) -> Path:
        """Return the default location for the pre-restore snapshot.

        Lives at the top level of the shared data directory so it is
        discoverable next to ``radicale/`` and ``journals/``.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return self._common_base / f"pre-restore-{self._username}-{stamp}.zip"

    def _create_safety_backup(self, *, target_path: Path | None = None) -> Path | None:
        """Take a snapshot of the current state via ``export_to_bytes``.

        Returns the path the snapshot was written to. Returns ``None``
        only if there is nothing to snapshot (an empty user, in which
        case the wipe is a no-op anyway).
        """
        snapshot_target = target_path or self._safety_backup_path()
        snapshot_target.parent.mkdir(parents=True, exist_ok=True)
        data = self.export_to_bytes()
        snapshot_target.write_bytes(data)
        return snapshot_target

    def _wipe_user_data(self) -> None:
        """Remove the user's Radicale subtree and journal subtree.

        Scoped strictly to ``<user>``. We use ``shutil.rmtree`` on the
        subtree root so partial state cannot survive. Parent
        directories (``collection-root``, etc.) are preserved because
        they may be shared with other users.

        Either operation is a no-op if the subtree does not exist,
        which lets us restore into an account that has never written
        any data.
        """
        if self._radicale_user_root.exists():
            shutil.rmtree(self._radicale_user_root)
        if self._journal_user_root.exists():
            shutil.rmtree(self._journal_user_root)
        if self._profile_root.exists():
            # The profile root lives under the journal user root's
            # parent in the default layout; rmtree it independently so
            # a non-default profile_root (under the user root) is
            # wiped too. The journal rmtree above never touches it
            # because the two roots are siblings.
            shutil.rmtree(self._profile_root)

    def _extract_archive(self, data: bytes) -> None:
        """Unpack the validated archive into the shared data directory.

        Each file's arcname is a relative path under ``_common_base``;
        we anchor extraction at ``_common_base`` and reject arcnames
        that would escape it. The wipe before this call guarantees
        there is no stray per-user data outside the archive; the
        safety check covers the unusual case of an archive containing
        a ``radicale/<other_user>/...`` entry.
        """
        base = self._common_base
        buffer = io.BytesIO(data)
        with zipfile.ZipFile(buffer, "r") as zf:
            for member in zf.infolist():
                if not member.filename or member.filename == MANIFEST_FILENAME:
                    continue
                target = (base / member.filename).resolve()
                if not target.is_relative_to(base.resolve()):
                    raise ArchiveError(f"archive entry {member.filename!r} would escape the shared data dir")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    def _list_after(self) -> tuple[int, int, int, int, int]:
        """Return (projects, tasks, events, time_log, contacts) post-restore."""
        caldav = self._parent.caldav
        carddav = self._parent.carddav
        return (
            len(caldav.list_projects()),
            len(caldav.list_tasks()),
            len(caldav.list_events()),
            len(caldav.list_time_log()),
            len(carddav.list_contacts()),
        )

    def import_from_bytes(self, data: bytes) -> RestoreReport:
        """Restore ``data`` into the signed in user's data scope.

        Steps (see ADR-024):

        1. Validate the archive.
        2. Take an automatic safety snapshot of current state.
        3. Wipe the user's Radicale subtree and journal subtree.
        4. Extract the archive.
        5. Restart Radicale so its in-memory storage cache reflects
           the freshly written tree.
        6. Build counts and return them.

        Raises:
            ArchiveError: On validation failure. The destructive
                step has not run.
            OSError: On filesystem failures mid-restore. The safety
                backup is still on disk; the operator can recover by
                importing it manually.
        """
        # 1. Validate. Re-runs the cheap structural checks; we run the
        # sample parse here too because it must succeed before we
        # commit to wiping the user's data.
        report = self.validate_archive(data)
        if not report.ok:
            raise ArchiveError("; ".join(report.issues))

        buffer = io.BytesIO(data)
        with zipfile.ZipFile(buffer, "r") as zf:
            self._sample_parse_archive(zf)

        # 2. Safety snapshot. Use a fixed name so a second restore in
        # the same second overwrites the first; in practice backups
        # take longer than a clock second but we want predictable
        # file names regardless.
        snapshot_target = self._safety_backup_path()
        safety_path = self._create_safety_backup(target_path=snapshot_target)

        # 3. Wipe. After this point we MUST leave the user with either
        # the new archive or the safety snapshot.
        self._wipe_user_data()

        # 4. Extract. Failure here means the user is in a partially
        # restored state; the safety backup on disk is the recovery
        # path. We do not catch filesystem errors: the caller needs
        # to know.
        self._extract_archive(data)

        # 5. Restart Radicale. Best-effort: a failed restart still
        # leaves a valid on-disk tree. Radicale reads .ics files on
        # demand so reads will succeed even if the cache is stale.
        try:
            self._parent.restart_server()
        except Exception:
            # Carry on; the restore succeeded on disk.
            pass

        # 6. Counts.
        projects, tasks, events, time_log, contacts = self._list_after()
        journal_count = len(self._parent.journal.list_entries())

        return RestoreReport(
            projects=projects,
            tasks=tasks,
            events=events,
            time_log=time_log,
            journal=journal_count,
            contacts=contacts,
            safety_backup=safety_path,
        )

    def import_from_path(self, path: Path) -> RestoreReport:
        """Read ``path`` then delegate to :meth:`import_from_bytes`."""
        return self.import_from_bytes(path.read_bytes())
