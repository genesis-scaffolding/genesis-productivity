"""Migrate per-user filesystem state into the shared layout.

ADR-019 option A: a single shared Radicale serves every user
under ``<XDG_DATA_HOME>/genesis/radicale/``; journals share
``<XDG_DATA_HOME>/genesis/journals/``; one JWT signing key
lives at ``<XDG_DATA_HOME>/genesis/api_secret.key``.

This module walks the per-user directories that the legacy
``genesis user migrate-default`` produced and re-homes them
under the shared layout. After it runs:

  - ``<xdg_data>/genesis/<user>/journal/``  ->  ``<xdg_data>/genesis/journals/<user>/``
  - ``<xdg_data>/genesis/<user>/radicale/collections/collection-root/<user>/``
       ->  ``<xdg_data>/genesis/radicale/collections/collection-root/<user>/``
  - ``<xdg_data>/genesis/<user>/radicale/users`` lines for ``<user>``
       get appended to ``<xdg_data>/genesis/radicale/users``
  - ``<xdg_data>/genesis/<user>/api_secret.key``
       ->  ``<xdg_data>/genesis/api_secret.key`` (only the first user)
  - the (now empty) ``<xdg_data>/genesis/<user>/radicale/`` dir is removed

The command is idempotent in the sense that it refuses to run if
the target shared layout already exists. It does NOT
auto-rollback on partial failure; the operator must move dirs
back by hand if a step fails partway through.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .htpasswd import RadicaleHtpasswd
from .rights import write_radicale_rights_from_htpasswd


class MigrateSharedError(Exception):
    """Raised when the shared-layout migration cannot proceed."""


def _xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


def _shared_paths() -> dict[str, Path]:
    """Return the canonical shared layout paths under XDG_DATA_HOME."""
    from ..configs import DEFAULT_APP_NAME

    root = _xdg_data_home() / DEFAULT_APP_NAME
    return {
        "radicale_root": root / "radicale",
        "journal_root": root / "journals",
        "api_secret_key": root / "api_secret.key",
        "shared_htpasswd": root / "radicale" / "users",
        "shared_rights": root / "radicale" / "rights",
    }


def _per_user_radicale_users(per_user_radicale: Path) -> Path:
    """The htpasswd file inside a per-user radicale dir."""
    return per_user_radicale / "users"


def _append_htpasswd_lines(src: Path, dst: Path) -> int:
    """Append ``user:password`` lines from ``src`` to ``dst``.

    Returns the number of NEW users added (i.e. usernames that
    were not already in ``dst``). Lines that fail the basic
    ``user:rest`` shape are skipped silently.

    The destination file is rewritten via the
    :class:`RadicaleHtpasswd` helper so atomicity and 0o600
    mode match the rest of the user lifecycle.
    """
    if not src.exists():
        return 0
    existing = RadicaleHtpasswd(path=dst)
    before = set(existing.load())
    for line in src.read_text().splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        user, _, password = stripped.partition(":")
        if user in before:
            continue
        existing.add(user, password)
    return len(set(existing.load()) - before)


def _move_dir(src: Path, dst: Path) -> bool:
    """Move ``src`` to ``dst`` (which must not exist).

    Returns ``True`` if a move happened, ``False`` if ``src`` did
    not exist. Raises :class:`MigrateSharedError` on any other
    failure.
    """
    if not src.exists():
        return False
    if dst.exists():
        raise MigrateSharedError(f"refusing to overwrite existing path: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def _prune_empty_parents(start: Path, *, stop_at: Path) -> list[Path]:
    """Remove ``start`` and any empty descendants/ancestors up to
    (but not including) ``stop_at``.

    Walks DOWN from ``start`` first so empty leaves are removed
    before their parents become empty themselves, then walks UP
    removing empty ancestors. Returns the paths that were
    actually removed, deepest-first.
    """
    removed: list[Path] = []

    def _walk(directory: Path) -> None:
        """Recurse into ``directory``; queue empty leaves and
        empty branches for removal in bottom-up order."""
        if not directory.is_dir():
            return
        children = list(directory.iterdir())
        for child in children:
            if child.is_dir():
                _walk(child)
                # Re-check after the recursive descent: the child
                # may have become empty (all of its own children
                # were removed).
                if child.exists() and not any(child.iterdir()):
                    removed.append(child)
                    child.rmdir()

    if start.exists():
        _walk(start)

    # Walk up from ``start`` removing empty ancestors up to (but
    # not including) ``stop_at``.
    current = start
    while current != stop_at and current.exists() and not any(current.iterdir()):
        if current not in removed:
            removed.append(current)
        current = current.parent
    for path in removed:
        if path.exists() and not any(path.iterdir()):
            path.rmdir()
    return removed


def migrate_shared(username: str, *, users_yaml: Path | None = None) -> list[str]:
    """Move ``username``'s per-user state into the shared layout.

    The caller is expected to have already migrated ``default`` to
    ``username`` via :func:`genesis.users.migrate.migrate_default`.
    This command handles the SECOND hop: taking the per-user
    ``~/.local/share/genesis/<username>/`` layout that resulted
    from that migration and re-homing the radicale / journal /
    api_secret.key parts under the new shared roots.

    Returns a list of human-readable notes (for CLI output).
    Raises :class:`MigrateSharedError` on any condition that
    prevents the migration from proceeding safely.
    """
    data_home = _xdg_data_home()
    shared = _shared_paths()

    notes: list[str] = []

    # 1. Refuse if no per-user data exists for this user. The
    #    operator probably ran this in the wrong directory or
    #    before ``migrate-default``.
    per_user_data = data_home / "genesis" / username
    per_user_radicale = per_user_data / "radicale"
    per_user_journal = per_user_data / "journal"
    per_user_secret = per_user_data / "api_secret.key"

    if not any(
        p.exists()
        for p in (
            per_user_data,
            per_user_radicale,
            per_user_journal,
            per_user_secret,
        )
    ):
        raise MigrateSharedError(
            f"no per-user data found for '{username}' under {per_user_data}",
        )

    # 2. Refuse if the shared layout already has data for this
    #    user (idempotency by refusal, matching ``migrate-default``).
    if (shared["radicale_root"] / "collections" / "collection-root" / username).exists():
        raise MigrateSharedError(
            f"shared layout already has collection-root for '{username}'",
        )
    if (shared["journal_root"] / username).exists():
        raise MigrateSharedError(f"shared layout already has journal dir for '{username}'")

    # 3. Move the journal: <user>/journal/  ->  <shared_journal_root>/<user>/
    new_journal = shared["journal_root"] / username
    if _move_dir(per_user_journal, new_journal):
        notes.append(f"moved journal: {per_user_journal} -> {new_journal}")

    # 4. Move the per-user Radicale collection root.
    per_user_collection = per_user_radicale / "collections" / "collection-root" / username
    new_collection = shared["radicale_root"] / "collections" / "collection-root" / username
    if _move_dir(per_user_collection, new_collection):
        notes.append(f"moved collection-root: {per_user_collection} -> {new_collection}")

    # 5. Merge the per-user htpasswd into the shared htpasswd,
    #    then delete the per-user file: it is no longer the source
    #    of truth and leaving it around risks Radicale picking up
    #    a stale path if the shared one is ever moved.
    per_user_htpasswd = _per_user_radicale_users(per_user_radicale)
    if per_user_htpasswd.exists():
        appended = _append_htpasswd_lines(per_user_htpasswd, shared["shared_htpasswd"])
        if appended:
            notes.append(
                f"appended {appended} htpasswd line(s) to {shared['shared_htpasswd']}",
            )
        per_user_htpasswd.unlink()

    # 6. Move the per-user API secret to the shared secret path.
    #    Only the first user to migrate provides the key;
    #    subsequent users' secrets are dropped (the shared key
    #    already covers all tokens).
    if per_user_secret.exists() and not shared["api_secret_key"].exists():
        shared["api_secret_key"].parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(per_user_secret), str(shared["api_secret_key"]))
        os.chmod(shared["api_secret_key"], 0o600)
        notes.append(f"moved api_secret.key: {per_user_secret} -> {shared['api_secret_key']}")
    elif per_user_secret.exists():
        notes.append(
            f"left api_secret.key at {per_user_secret}: shared secret already exists",
        )

    # 7. Remove empty per-user radicale dirs, walking up from the
    #    leaves so a chain of empty parents (collections/,
    #    collection-root/) all collapse into one removal note.
    removed = _prune_empty_parents(per_user_radicale, stop_at=data_home / "genesis")
    if removed:
        notes.append(f"removed empty dir(s): {' -> '.join(str(p) for p in removed)}")

    # 8. Regenerate the rights file from the (now updated) shared
    #    htpasswd. The current implementation always writes the
    #    permissive ``[base]`` rule; this call exists so the
    #    shared rights file exists on disk for any operator who
    #    inspects the Radicale config.
    if shared["shared_htpasswd"].exists():
        write_radicale_rights_from_htpasswd(shared["radicale_root"])
        notes.append(f"regenerated rights file at {shared['shared_rights']}")

    notes.append("")
    notes.append("Shared-layout migration complete.")
    notes.append(
        "Next: restart Radicale so it reads the merged htpasswd and rights file.",
    )
    return notes
