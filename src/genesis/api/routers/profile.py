"""Profile endpoints: per-user profile and life-story markdown store.

ADR-025. Six endpoints under ``/profile``:

- ``GET    /profile``                  list both targets
- ``GET    /profile/{target}``         read
- ``PUT    /profile/{target}``         overwrite
- ``PATCH  /profile/{target}``         surgical edit
- ``GET    /profile/{target}/history`` list snapshots
- ``POST   /profile/{target}/restore`` restore from snapshot

The handler builds a :class:`ProfileFacade` from the JWT subject
on every request so one token can only touch one user's data.
There is no cross-user API.

Error mapping (storage layer returns ``ok=False`` results; we
translate to 4xx):

- File missing / empty   -> 404 (read)
- oldText empty / not found / matches N places -> 400 (edit)
- Unknown target         -> 404 (path param)
- Version not found / ambiguous / none exist -> 400 (history, restore)
- Missing / bad token    -> 401 (auth dep)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...configs import resolve_config_path
from ...core.facade import Facade
from ...profile import ProfileTarget, parse_target
from ..auth import current_subject
from ..schemas import (
    ProfileContentOut,
    ProfileEditIn,
    ProfileHistoryOut,
    ProfileListOut,
    ProfileListRowOut,
    ProfileRestoreIn,
    ProfileRestoreOut,
    ProfileSnapshotOut,
    ProfileWireTarget,
    ProfileWriteIn,
    ProfileWriteOut,
)

router = APIRouter(prefix="/profile", tags=["profile"])


def _facade(subject: str) -> Facade:
    """Build a per-request :class:`Facade` for ``subject``.

    Same pattern as the productivity router: a fresh facade per
    request, scoped to the JWT subject. Only ``facade.profile`` is
    used here; the rest of the Facade (caldav, journal, ...) is
    constructed lazily and never reached.
    """
    return Facade(resolve_config_path(subject))


def _parse_target_path_param(target: str) -> ProfileTarget:
    """Validate the ``{target}`` path segment."""
    try:
        return parse_target(target)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _snapshot_filename(snap) -> str | None:
    """Render the snapshot filename (None when the snapshot was dedup-skipped)."""
    if snap is None or not snap.ok:
        return None
    return snap.filename


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=ProfileListOut)
def list_profiles(
    subject: Annotated[str, Depends(current_subject)],
) -> ProfileListOut:
    """List both profile targets with active path + existence + size."""
    facade = _facade(subject)
    try:
        listed = facade.profile.list()
        return ProfileListOut(
            rows=[
                ProfileListRowOut(
                    target=ProfileWireTarget(row.target.value),
                    path=row.path,
                    exists=row.exists,
                    bytes=row.bytes,
                )
                for row in listed.rows
            ],
            profile_root=listed.profile_root,
        )
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("/{target}", response_model=ProfileContentOut)
def read_profile(
    target: str,
    subject: Annotated[str, Depends(current_subject)],
) -> ProfileContentOut:
    """Read the canonical content for ``target`` (trimmed)."""
    parsed = _parse_target_path_param(target)
    facade = _facade(subject)
    try:
        result = facade.profile.read(parsed)
        if not result.ok:
            # File missing or empty: 404.
            raise HTTPException(status_code=404, detail=result.error)
        return ProfileContentOut(
            target=ProfileWireTarget(result.target.value if result.target is not None else parsed.value),
            path=result.path,
            content=result.content,
        )
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Write (overwrite)
# ---------------------------------------------------------------------------


@router.put("/{target}", response_model=ProfileWriteOut, status_code=status.HTTP_200_OK)
def write_profile(
    target: str,
    body: ProfileWriteIn,
    subject: Annotated[str, Depends(current_subject)],
) -> ProfileWriteOut:
    """Overwrite the canonical file. Creates a snapshot after success."""
    parsed = _parse_target_path_param(target)
    facade = _facade(subject)
    try:
        result = facade.profile.write(parsed, body.content, note=body.note)
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        return ProfileWriteOut(
            target=ProfileWireTarget(result.target.value if result.target is not None else parsed.value),
            path=result.path,
            bytes_written=result.bytes_written,
            snapshot=_snapshot_filename(result.snapshot),
        )
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Edit (surgical)
# ---------------------------------------------------------------------------


@router.patch("/{target}", response_model=ProfileWriteOut, status_code=status.HTTP_200_OK)
def edit_profile(
    target: str,
    body: ProfileEditIn,
    subject: Annotated[str, Depends(current_subject)],
) -> ProfileWriteOut:
    """Replace ``old_text`` with ``new_text`` in the canonical file.

    400 on empty ``old_text``, no match, or >1 match without
    ``global_replace``. 404 on missing target file. 200 on success
    (with the snapshot filename if a snapshot was taken).
    """
    parsed = _parse_target_path_param(target)
    facade = _facade(subject)
    try:
        result = facade.profile.edit(
            parsed,
            body.old_text,
            body.new_text,
            global_replace=body.global_replace,
            note=body.note,
        )
        if not result.ok:
            # Storage-layer errors are user mistakes (empty / not
            # found / ambiguous). 500 is reserved for actual bugs;
            # re-read failures during snapshot are not exercised
            # here because the edit already wrote the file.
            err = result.error
            if "oldText is empty" in err or "oldText not found" in err or "matches" in err:
                raise HTTPException(status_code=400, detail=err)
            if "does not exist" in err:
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=500, detail=err)
        return ProfileWriteOut(
            target=ProfileWireTarget(result.target.value if result.target is not None else parsed.value),
            path=result.path,
            bytes_written=result.bytes_written,
            replacements=result.replacements,
            snapshot=_snapshot_filename(result.snapshot),
        )
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


@router.get("/{target}/history", response_model=ProfileHistoryOut)
def profile_history(
    target: str,
    subject: Annotated[str, Depends(current_subject)],
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> ProfileHistoryOut:
    """List snapshots for ``target`` (newest first).

    Optional ``limit`` caps the number of returned rows; the
    response ``total`` reports the unfiltered count.
    """
    parsed = _parse_target_path_param(target)
    facade = _facade(subject)
    try:
        result = facade.profile.history(parsed, limit=limit)
        if not result.ok:
            raise HTTPException(status_code=500, detail=result.error)
        rows = [
            ProfileSnapshotOut(
                ts=row.ts,
                filename=row.filename,
                sha=row.meta.this_sha[:12] if row.meta is not None else None,
                size=row.size,
                command=row.meta.command if row.meta is not None else None,
                note=row.meta.note if row.meta is not None else None,
            )
            for row in result.snapshots
        ]
        return ProfileHistoryOut(
            target=ProfileWireTarget(result.target.value if result.target is not None else parsed.value),
            rows=rows,
            total=result.total,
        )
    finally:
        facade.shutdown()


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


@router.post("/{target}/restore", response_model=ProfileRestoreOut, status_code=status.HTTP_200_OK)
def profile_restore(
    target: str,
    body: ProfileRestoreIn,
    subject: Annotated[str, Depends(current_subject)],
) -> ProfileRestoreOut:
    """Restore ``target`` from snapshot ``version``.

    Snapshots the current canonical file before applying the
    restore, so the restore is reversible.
    """
    parsed = _parse_target_path_param(target)
    facade = _facade(subject)
    try:
        result = facade.profile.restore(parsed, body.version, note=body.note)
        if not result.ok:
            err = result.error
            if "No snapshots exist" in err or "matches prefix" in err or "matches" in err:
                raise HTTPException(status_code=400, detail=err)
            raise HTTPException(status_code=500, detail=err)
        return ProfileRestoreOut(
            target=ProfileWireTarget(result.target.value if result.target is not None else parsed.value),
            applied_filename=result.applied_filename,
            applied_size=result.applied_size,
            pre_restore_snapshot=_snapshot_filename(result.pre_restore_snapshot),
        )
    finally:
        facade.shutdown()
