"""Facade for profile and life-story storage.

Per-user, on-disk, versioned. The facade is the only entry point
callers should use: it owns the FsOps instance, threads it through
:mod:`storage` and :mod:`versions`, and translates the layered
results into one shape per public method.

There is no file locking. Matches the out-of-tree personalize
CLI's no-lock contract; concurrent edits from the same user are
out of scope for this ADR.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import (
    TARGET_FILENAME,
    TARGETS,
    ProfileTarget,
    parse_target,
    resolve_location,
    versions_dir,
)
from .storage import (
    DefaultFsOps,
    EditOpts,
    EditResult,
    FsOps,
    ReadResult,
    WriteResult,
    edit_location,
    read_location,
    write_location,
)
from .storage import (
    list_targets as storage_list_targets,
)
from .versions import (
    SnapshotEntry,
    SnapshotMeta,
    SnapshotResult,
    create_snapshot,
    list_snapshots,
    load_snapshot_content,
)

# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileRead:
    """Outcome of :meth:`ProfileFacade.read`."""

    ok: bool
    target: ProfileTarget | None = None
    path: str = ""
    content: str = ""
    error: str = ""


@dataclass(frozen=True)
class ProfileWrite:
    """Outcome of :meth:`ProfileFacade.write` and :meth:`ProfileFacade.edit`."""

    ok: bool
    target: ProfileTarget | None = None
    path: str = ""
    bytes_written: int = 0
    replacements: int = 0
    snapshot: SnapshotResult | None = None
    error: str = ""


@dataclass(frozen=True)
class ProfileSnapshot:
    """A snapshot row returned by :meth:`ProfileFacade.history` and restore."""

    ts: str
    filename: str
    meta: SnapshotMeta | None
    size: int


@dataclass(frozen=True)
class ProfileHistory:
    """Outcome of :meth:`ProfileFacade.history`."""

    ok: bool
    target: ProfileTarget | None = None
    snapshots: list[ProfileSnapshot] = None  # type: ignore[assignment]
    total: int = 0
    error: str = ""


@dataclass(frozen=True)
class ProfileRestore:
    """Outcome of :meth:`ProfileFacade.restore`."""

    ok: bool
    target: ProfileTarget | None = None
    applied_filename: str = ""
    applied_size: int = 0
    pre_restore_snapshot: SnapshotResult | None = None
    error: str = ""


@dataclass(frozen=True)
class ProfileListRow:
    """One row of :meth:`ProfileFacade.list`."""

    target: ProfileTarget
    path: str
    exists: bool
    bytes: int


@dataclass(frozen=True)
class ProfileList:
    """Outcome of :meth:`ProfileFacade.list`."""

    rows: list[ProfileListRow]
    profile_root: str


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class ProfileFacade:
    """Per-user facade for the two profile targets.

    The constructor takes a ``profile_root`` :class:`Path`. The
    caller is responsible for resolving the per-user root (typically
    via ``Configs.paths.profile_root`` from the user's
    :class:`genesis.core.facade.Facade`).
    """

    def __init__(
        self,
        profile_root: Path,
        *,
        fs: FsOps | None = None,
    ) -> None:
        self._profile_root = Path(profile_root)
        self._fs = fs or DefaultFsOps()

    @property
    def profile_root(self) -> Path:
        """The per-user profile directory this facade operates on."""
        return self._profile_root

    # ----- Reads ---------------------------------------------------------

    def read(self, target: ProfileTarget) -> ProfileRead:
        """Read the current canonical content for ``target`` (trimmed)."""
        r: ReadResult = read_location(target, self._profile_root, self._fs)
        if not r.ok:
            return ProfileRead(ok=False, error=r.error)
        assert r.location is not None
        return ProfileRead(
            ok=True,
            target=target,
            path=str(r.location.path),
            content=r.content,
        )

    def list(self) -> ProfileList:
        """List every target with its active path + existence + size."""
        rows = storage_list_targets(self._profile_root, self._fs)
        return ProfileList(
            rows=[
                ProfileListRow(
                    target=row.target,
                    path=str(row.location.path) if row.location is not None else "",
                    exists=row.exists,
                    bytes=row.bytes,
                )
                for row in rows
            ],
            profile_root=str(self._profile_root),
        )

    # ----- Writes --------------------------------------------------------

    def write(
        self,
        target: ProfileTarget,
        content: str,
        *,
        note: str | None = None,
    ) -> ProfileWrite:
        """Overwrite the canonical file. Auto-snapshots after success."""
        prev = self._read_existing_or_none(target)
        wr: WriteResult = write_location(target, self._profile_root, content, self._fs)
        if not wr.ok:
            return ProfileWrite(ok=False, error=wr.error, target=target)
        assert wr.location is not None
        snap = create_snapshot(
            target,
            self._profile_root,
            filename=wr.location.filename,
            canonical_path=wr.location.path,
            new_content=content,
            prev_content=prev,
            command="write",
            note=note,
            fs=self._fs,
        )
        return ProfileWrite(
            ok=True,
            target=target,
            path=str(wr.location.path),
            bytes_written=wr.bytes_written,
            snapshot=snap,
        )

    def edit(
        self,
        target: ProfileTarget,
        old_text: str,
        new_text: str,
        *,
        global_replace: bool = False,
        note: str | None = None,
    ) -> ProfileWrite:
        """Surgical edit. Auto-snapshots after success.

        The result uses the same :class:`ProfileWrite` shape as
        ``write``; ``replacements`` carries the edit match count.
        """
        prev = self._read_existing_or_none(target)
        er: EditResult = edit_location(
            target,
            self._profile_root,
            old_text,
            new_text,
            EditOpts(global_replace=global_replace),
            self._fs,
        )
        if not er.ok:
            return ProfileWrite(ok=False, error=er.error, target=target)
        assert er.location is not None
        # The edit already wrote the new content; re-read it for
        # snapshot hashing.
        try:
            new_content = self._fs.read(er.location.path)
        except OSError:
            new_content = None
        snap: SnapshotResult | None = None
        if new_content is not None:
            snap = create_snapshot(
                target,
                self._profile_root,
                filename=er.location.filename,
                canonical_path=er.location.path,
                new_content=new_content,
                prev_content=prev,
                command="edit",
                note=note,
                fs=self._fs,
            )
        return ProfileWrite(
            ok=True,
            target=target,
            path=str(er.location.path),
            bytes_written=er.bytes_written,
            replacements=er.replacements,
            snapshot=snap,
        )

    # ----- History + restore --------------------------------------------

    def history(
        self,
        target: ProfileTarget,
        *,
        limit: int | None = None,
    ) -> ProfileHistory:
        """List snapshots for ``target`` (newest first)."""
        try:
            entries: list[SnapshotEntry] = list_snapshots(target, self._profile_root, self._fs, limit=limit)
        except OSError as e:
            return ProfileHistory(ok=False, target=target, error=f"Failed to list snapshots: {e}")
        snapshots = [
            ProfileSnapshot(
                ts=e.ts,
                filename=e.filename,
                meta=e.meta,
                size=e.size,
            )
            for e in entries
        ]
        # ``total`` is the unfiltered count so the caller can paginate.
        if limit is not None:
            try:
                total = len(list_snapshots(target, self._profile_root, self._fs, limit=None))
            except OSError:
                total = len(snapshots)
        else:
            total = len(snapshots)
        return ProfileHistory(ok=True, target=target, snapshots=snapshots, total=total)

    def restore(
        self,
        target: ProfileTarget,
        version: str,
        *,
        note: str | None = None,
    ) -> ProfileRestore:
        """Restore ``target`` from snapshot ``version``.

        Snapshots the current canonical content (restore-pre,
        forced) before applying, so this restore is itself
        reversible.
        """
        prev = self._read_existing_or_none(target)
        try:
            entry, snapshot_content = load_snapshot_content(
                target,
                self._profile_root,
                version,
                self._fs,
            )
        except ValueError as e:
            return ProfileRestore(ok=False, target=target, error=str(e))

        location = resolve_location(target, self._profile_root)

        pre_restore: SnapshotResult | None = None
        if prev is not None:
            pre_restore = create_snapshot(
                target,
                self._profile_root,
                filename=location.filename,
                canonical_path=location.path,
                new_content=prev,
                prev_content=prev,
                command="restore-pre",
                note=(note + " (pre-restore)") if note else "pre-restore",
                fs=self._fs,
                force=True,
            )

        try:
            self._fs.mkdir(location.dir, parents=True, exist_ok=True)
            self._fs.write(location.path, snapshot_content)
        except OSError as e:
            return ProfileRestore(
                ok=False,
                target=target,
                error=f"Failed to apply restore: {e}",
            )

        return ProfileRestore(
            ok=True,
            target=target,
            applied_filename=entry.filename,
            applied_size=entry.size,
            pre_restore_snapshot=pre_restore,
        )

    # ----- Internal ------------------------------------------------------

    def _read_existing_or_none(self, target: ProfileTarget) -> str | None:
        """Return the current canonical content (untrimmed) or None.

        Used as the ``prev_content`` input to :func:`create_snapshot`
        so sha256 dedup sees the actual bytes on disk (not the
        trimmed variant returned by :func:`read_location`).
        """
        location = resolve_location(target, self._profile_root)
        if not self._fs.exists(location.path):
            return None
        try:
            return self._fs.read(location.path)
        except OSError:
            return None


__all__ = [
    "ProfileFacade",
    "ProfileHistory",
    "ProfileList",
    "ProfileListRow",
    "ProfileRead",
    "ProfileRestore",
    "ProfileSnapshot",
    "ProfileWrite",
    "ProfileTarget",
    "TARGETS",
    "TARGET_FILENAME",
    "parse_target",
    "resolve_location",
    "versions_dir",
]
