"""Snapshot lifecycle for the profile subpackage.

Every write / edit creates a snapshot of the **new** content.
Restore creates a snapshot of the **current** canonical file
(restore-pre) before applying the restore, so restore is itself
reversible.

Snapshot directory: ``<profile_root>/.versions/<target>/``
Filename: ``<UTC-ISO>.md`` + ``<UTC-ISO>.meta.json`` sidecar.
UTC-ISO form is ``YYYY-MM-DDTHHMMSSfffZ`` (see :mod:`dates`).
On a millisecond collision, a ``-N`` counter is appended before
the suffix.

Dedup (sha256): skip the snapshot when the new content's hash
matches the **previous canonical file's** hash, OR the **most
recent snapshot's** hash. Restore-pre snapshots skip dedup so
the restore is always reversible even when the post-restore
content matches a recent history entry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from .dates import now_iso_utc_millis, parse_iso_filename, split_counter
from .paths import ProfileTarget, versions_dir
from .storage import DefaultFsOps, FsOps

# ---------------------------------------------------------------------------
# Result + meta types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of :func:`create_snapshot`."""

    ok: bool
    ts: str = ""
    filename: str = ""
    meta_filename: str = ""
    canonical_path: str = ""
    skipped_reason: str = ""


@dataclass(frozen=True)
class SnapshotMeta:
    """Decoded meta sidecar payload.

    Schema is documented in ADR-025. ``prev_sha`` is ``None`` when
    the canonical file did not exist before the operation.
    """

    ts: str
    command: str  # "write" | "edit" | "restore-pre"
    filename: str
    canonical_path: str
    prev_sha: str | None
    this_sha: str
    note: str | None = None

    def to_json(self) -> str:
        """Render as a stable JSON string for the sidecar."""
        payload: dict[str, Any] = {
            "ts": self.ts,
            "command": self.command,
            "filename": self.filename,
            "canonical_path": self.canonical_path,
            "prev_sha": self.prev_sha,
            "this_sha": self.this_sha,
            "note": self.note,
        }
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> SnapshotMeta:
        """Decode a meta sidecar JSON document.

        Raises :class:`ValueError` on missing required fields.
        """
        data = json.loads(raw)
        return cls(
            ts=data["ts"],
            command=data["command"],
            filename=data["filename"],
            canonical_path=data["canonical_path"],
            prev_sha=data.get("prev_sha"),
            this_sha=data["this_sha"],
            note=data.get("note"),
        )


@dataclass(frozen=True)
class SnapshotEntry:
    """One row in :func:`list_snapshots`."""

    ts: str
    filename: str
    meta_filename: str
    meta: SnapshotMeta | None
    size: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256(content: str) -> str:
    """sha256-hex digest of UTF-8 encoded ``content``."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _allocate_filename(ts: str, target_dir: Path, fs: FsOps) -> str:
    """Pick a unique snapshot ts+counter, returning the bare filename.

    Starts with the bare timestamp. If ``<target_dir>/<ts>.md``
    already exists, increments a ``-N`` suffix until a free slot
    is found.
    """
    candidate = f"{ts}.md"
    if not fs.exists(target_dir / candidate):
        return candidate
    n = 1
    while True:
        candidate = f"{ts}-{n}.md"
        if not fs.exists(target_dir / candidate):
            return candidate
        n += 1


def _last_snapshot_content_sha(
    target: ProfileTarget,
    profile_root: Path,
    fs: FsOps,
) -> str | None:
    """sha256 of the most recent snapshot's content (``None`` if no snapshots)."""
    target_dir = versions_dir(profile_root, target)
    if not fs.exists(target_dir):
        return None
    md_files = [n for n in fs.list_dir(target_dir) if n.endswith(".md") and not n.endswith(".meta.json")]
    if not md_files:
        return None
    md_files.sort(reverse=True)  # Lex-sortable timestamps make this safe.
    try:
        content = fs.read(target_dir / md_files[0])
    except OSError:
        return None
    return sha256(content)


# ---------------------------------------------------------------------------
# Snapshot creation
# ---------------------------------------------------------------------------


def create_snapshot(
    target: ProfileTarget,
    profile_root: Path,
    *,
    filename: str,
    canonical_path: Path,
    new_content: str,
    prev_content: str | None,
    command: str,
    note: str | None,
    fs: FsOps | None = None,
    now_iso_fn=now_iso_utc_millis,
    force: bool = False,
) -> SnapshotResult:
    """Snapshot ``new_content`` after a successful write/edit/restore.

    Returns :class:`SnapshotResult` with ``ok=False`` when the
    snapshot was skipped (dedup), and ``ok=True`` when a new
    snapshot file + meta were written.

    ``prev_content`` is the content of the canonical file BEFORE
    the operation (``None`` if the file did not exist; ``""`` if
    it existed but was empty). Used to compute ``prev_sha``.

    ``force=True`` skips dedup — used by restore-pre so the
    restore is always reversible even when the post-restore
    content matches a recent history entry.
    """
    fs = fs or DefaultFsOps()
    this_sha = sha256(new_content)
    if not force and prev_content is not None and sha256(prev_content) == this_sha:
        return SnapshotResult(
            ok=False,
            skipped_reason="dedup-previous",
            canonical_path=str(canonical_path),
        )
    if not force:
        last = _last_snapshot_content_sha(target, profile_root, fs)
        if last is not None and last == this_sha:
            return SnapshotResult(
                ok=False,
                skipped_reason="dedup-last-snapshot",
                canonical_path=str(canonical_path),
            )

    target_dir = versions_dir(profile_root, target)
    fs.mkdir(target_dir, parents=True, exist_ok=True)
    ts = now_iso_fn()
    bare_filename = _allocate_filename(ts, target_dir, fs)
    full_path = target_dir / bare_filename
    meta_filename = bare_filename[: -len(".md")] + ".meta.json"
    meta_path = target_dir / meta_filename

    fs.write(full_path, new_content)
    prev_sha: str | None = sha256(prev_content) if prev_content is not None else None
    meta = SnapshotMeta(
        ts=ts,
        command=command,
        filename=filename,
        canonical_path=str(canonical_path),
        prev_sha=prev_sha,
        this_sha=this_sha,
        note=note,
    )
    fs.write(meta_path, meta.to_json())

    return SnapshotResult(
        ok=True,
        ts=ts,
        filename=bare_filename,
        meta_filename=meta_filename,
        canonical_path=str(canonical_path),
    )


# ---------------------------------------------------------------------------
# Snapshot listing
# ---------------------------------------------------------------------------


def list_snapshots(
    target: ProfileTarget,
    profile_root: Path,
    fs: FsOps | None = None,
    *,
    limit: int | None = None,
) -> list[SnapshotEntry]:
    """Return every snapshot for ``target`` (newest first).

    Entries whose meta.json sidecar is missing or corrupt are
    still returned with ``meta=None`` so the listing never silently
    hides a snapshot because of a sidecar mishap.
    """
    fs = fs or DefaultFsOps()
    target_dir = versions_dir(profile_root, target)
    if not fs.exists(target_dir):
        return []
    md_files = [n for n in fs.list_dir(target_dir) if n.endswith(".md") and not n.endswith(".meta.json")]

    # Sort by parsed instant, not lex (counter suffix would lex-sort
    # before the bare timestamp).
    from datetime import datetime

    def _sort_key(name: str) -> tuple[datetime, int]:
        bare_ts, counter = split_counter(name[: -len(".md")])
        try:
            instant = parse_iso_filename(bare_ts)
        except ValueError:
            instant = datetime.min.replace(tzinfo=UTC)
        return (instant, counter)

    md_files.sort(key=_sort_key, reverse=True)
    if limit is not None and limit >= 0:
        md_files = md_files[:limit]

    out: list[SnapshotEntry] = []
    for filename in md_files:
        full_path = target_dir / filename
        meta_filename = filename[: -len(".md")] + ".meta.json"
        meta_path = target_dir / meta_filename
        meta: SnapshotMeta | None = None
        if fs.exists(meta_path):
            try:
                meta = SnapshotMeta.from_json(fs.read(meta_path))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                meta = None
        try:
            size = fs.stat_size(full_path)
        except OSError:
            size = 0
        ts = filename[: -len(".md")]
        out.append(
            SnapshotEntry(
                ts=ts,
                filename=filename,
                meta_filename=meta_filename,
                meta=meta,
                size=size,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Snapshot restoration
# ---------------------------------------------------------------------------


def _match_snapshot_prefix(
    entries: list[SnapshotEntry],
    prefix: str,
    target: ProfileTarget,
) -> SnapshotEntry:
    """Find a unique snapshot whose ts starts with ``prefix``.

    Raises :class:`ValueError` on no-match or ambiguous-match.
    """
    matches = [e for e in entries if e.ts.startswith(prefix)]
    if not matches:
        raise ValueError(f"No snapshot of {target.value} matches prefix {prefix!r}")
    if len(matches) > 1:
        names = [e.filename for e in matches]
        raise ValueError(f"Prefix {prefix!r} matches {len(matches)} {target.value} snapshots: {', '.join(names)}")
    return matches[0]


def load_snapshot_content(
    target: ProfileTarget,
    profile_root: Path,
    ts_or_prefix: str,
    fs: FsOps | None = None,
) -> tuple[SnapshotEntry, str]:
    """Return ``(entry, content)`` for the snapshot matching ``ts_or_prefix``.

    Accepts the full ts, a unique prefix of it, the bare ``.md``
    filename, or the bare ``.meta.json`` filename. Raises
    :class:`ValueError` on no-match or ambiguous-match.
    """
    fs = fs or DefaultFsOps()
    entries = list_snapshots(target, profile_root, fs)
    if not entries:
        raise ValueError(f"No snapshots exist for {target.value!r}")

    cleaned = ts_or_prefix
    for suffix in (".meta.json", ".md"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break

    # Exact match first (cheapest).
    for e in entries:
        if e.ts == cleaned:
            return e, fs.read(versions_dir(profile_root, target) / e.filename)

    # Then prefix match.
    selected = _match_snapshot_prefix(entries, cleaned, target)
    return selected, fs.read(versions_dir(profile_root, target) / selected.filename)


__all__ = [
    "SnapshotEntry",
    "SnapshotMeta",
    "SnapshotResult",
    "create_snapshot",
    "list_snapshots",
    "load_snapshot_content",
    "sha256",
]
