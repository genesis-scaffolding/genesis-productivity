"""File I/O for the profile subpackage.

Read / write / edit over the two canonical files (``profile.md``,
``life-story.md``) under the per-user profile root.

Errors that are *user mistakes* (file missing, file empty, edit
match-zero, edit match-many, invalid target) come back via
``ok=False`` result dataclasses so the API layer can translate to
4xx responses without exception handling. Infrastructure errors
(permission denied, encoding failure) bubble up because the caller
cannot usefully recover.

Snapshotting is NOT done here. The facade calls into :mod:`versions`
after a successful write/edit/restore.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .paths import (
    TARGETS,
    ProfileTarget,
    ResolvedLocation,
    resolve_location,
)

# ---------------------------------------------------------------------------
# Filesystem protocol
# ---------------------------------------------------------------------------


class FsOps(Protocol):
    """Filesystem surface the storage layer depends on.

    The shape is the minimum needed to read / write / list the
    canonical files and the snapshot directory. Tests pass an
    in-memory fake; production uses :class:`DefaultFsOps`.
    """

    def exists(self, path: Path) -> bool: ...
    def read(self, path: Path) -> str: ...
    def write(self, path: Path, content: str) -> None: ...
    def mkdir(self, path: Path, *, parents: bool, exist_ok: bool) -> None: ...
    def list_dir(self, path: Path) -> list[str]: ...
    def stat_size(self, path: Path) -> int: ...


class DefaultFsOps:
    """Real-fs adapter wiring :mod:`pathlib` and :mod:`os`."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    def mkdir(self, path: Path, *, parents: bool, exist_ok: bool) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def list_dir(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return sorted(os.listdir(path))

    def stat_size(self, path: Path) -> int:
        return path.stat().st_size


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadResult:
    """Outcome of :func:`read_location`."""

    ok: bool
    content: str = ""
    location: ResolvedLocation | None = None
    error: str = ""


@dataclass(frozen=True)
class WriteResult:
    """Outcome of :func:`write_location`."""

    ok: bool
    location: ResolvedLocation | None = None
    bytes_written: int = 0
    error: str = ""


@dataclass(frozen=True)
class EditResult:
    """Outcome of :func:`edit_location`."""

    ok: bool
    location: ResolvedLocation | None = None
    replacements: int = 0
    bytes_written: int = 0
    error: str = ""


@dataclass(frozen=True)
class EditOpts:
    """Options for :func:`edit_location`."""

    global_replace: bool = False


@dataclass(frozen=True)
class TargetRow:
    """One row in the listing returned by :func:`list_targets`."""

    target: ProfileTarget
    location: ResolvedLocation | None
    exists: bool
    bytes: int


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def read_location(
    target: ProfileTarget,
    profile_root: Path,
    fs: FsOps | None = None,
) -> ReadResult:
    """Read the canonical file for ``target``.

    The returned content is **trimmed** of leading / trailing
    whitespace. Matching the out-of-tree tool's contract — most
    consumers are LLM prompts that don't care about the bytes
    around the content, and a file with only whitespace is
    indistinguishable from an empty one.
    """
    fs = fs or DefaultFsOps()
    location = resolve_location(target, profile_root)
    if not fs.exists(location.path):
        return ReadResult(
            ok=False,
            location=location,
            error=f"No {target.value} file at {location.path} (file does not exist)",
        )
    try:
        raw = fs.read(location.path)
    except OSError as e:
        return ReadResult(
            ok=False,
            location=location,
            error=f"Failed to read {target.value} file at {location.path}: {e}",
        )
    trimmed = raw.strip()
    if not trimmed:
        return ReadResult(
            ok=False,
            location=location,
            error=f"{target.value} file at {location.path} is empty",
        )
    return ReadResult(ok=True, content=trimmed, location=location)


def write_location(
    target: ProfileTarget,
    profile_root: Path,
    content: str,
    fs: FsOps | None = None,
) -> WriteResult:
    """Write ``content`` to the canonical file, creating parent dirs.

    ``content`` is written verbatim — no trimming, no trailing
    newline added. The caller decides what's in the file.
    """
    fs = fs or DefaultFsOps()
    location = resolve_location(target, profile_root)
    try:
        fs.mkdir(location.dir, parents=True, exist_ok=True)
        fs.write(location.path, content)
    except OSError as e:
        return WriteResult(
            ok=False,
            location=location,
            error=f"Failed to write {target.value} file at {location.path}: {e}",
        )
    return WriteResult(
        ok=True,
        location=location,
        bytes_written=len(content.encode("utf-8")),
    )


def edit_location(
    target: ProfileTarget,
    profile_root: Path,
    old_text: str,
    new_text: str,
    edit_opts: EditOpts,
    fs: FsOps | None = None,
) -> EditResult:
    """Replace ``old_text`` with ``new_text`` in the canonical file.

    Match-count errors are surfaced as ``ok=False``:

    - empty ``old_text`` -> "oldText is empty; nothing to replace"
    - 0 matches         -> "oldText not found"
    - >1 matches, no ``global_replace`` -> "oldText matches N places"

    Mirrors the out-of-tree tool's contract so the agent-side CLI
    can surface identical error messages.
    """
    fs = fs or DefaultFsOps()
    location = resolve_location(target, profile_root)

    if old_text == "":
        return EditResult(
            ok=False,
            location=location,
            error="oldText is empty; nothing to replace",
        )

    try:
        current = fs.read(location.path)
    except FileNotFoundError:
        return EditResult(
            ok=False,
            location=location,
            error=f"No {target.value} file at {location.path} (file does not exist)",
        )
    except OSError as e:
        return EditResult(
            ok=False,
            location=location,
            error=f"Failed to read {target.value} file at {location.path}: {e}",
        )

    occurrences = current.count(old_text)
    if occurrences == 0:
        return EditResult(
            ok=False,
            location=location,
            error=f"oldText not found in {target.value} file at {location.path}",
        )
    if occurrences > 1 and not edit_opts.global_replace:
        return EditResult(
            ok=False,
            location=location,
            error=(
                f"oldText matches {occurrences} places in {target.value} file; set global_replace=true to replace all"
            ),
        )

    if edit_opts.global_replace:
        updated = current.replace(old_text, new_text)
    else:
        idx = current.find(old_text)
        updated = current[:idx] + new_text + current[idx + len(old_text) :]

    try:
        fs.mkdir(location.dir, parents=True, exist_ok=True)
        fs.write(location.path, updated)
    except OSError as e:
        return EditResult(
            ok=False,
            location=location,
            error=f"Failed to write {target.value} file at {location.path}: {e}",
        )

    return EditResult(
        ok=True,
        location=location,
        replacements=occurrences,
        bytes_written=len(updated.encode("utf-8")),
    )


def list_targets(
    profile_root: Path,
    fs: FsOps | None = None,
) -> list[TargetRow]:
    """Return one :class:`TargetRow` per known target.

    Order is :data:`paths.TARGETS` (stable). Bytes is 0 when the
    file doesn't exist so the caller can render a consistent row
    without conditional logic.
    """
    fs = fs or DefaultFsOps()
    rows: list[TargetRow] = []
    for target in TARGETS:
        location = resolve_location(target, profile_root)
        if fs.exists(location.path):
            try:
                size = fs.stat_size(location.path)
            except OSError:
                size = 0
            rows.append(TargetRow(target=target, location=location, exists=True, bytes=size))
        else:
            rows.append(TargetRow(target=target, location=location, exists=False, bytes=0))
    return rows


__all__ = [
    "DefaultFsOps",
    "EditOpts",
    "EditResult",
    "FsOps",
    "ReadResult",
    "TargetRow",
    "WriteResult",
    "edit_location",
    "list_targets",
    "read_location",
    "write_location",
]
