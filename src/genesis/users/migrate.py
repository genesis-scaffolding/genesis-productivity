"""Move ``default``-user filesystem state to a named user.

ADR-010 already lays out the per-user directory structure:

    <xdg_config>/genesis/<user>/configs.yaml
    <xdg_data>/genesis/<user>/
    <xdg_state>/genesis/<user>/
    <xdg_cache>/genesis/<user>/

The migration walks all four XDG bases and renames
``genesis/default`` -> ``genesis/<new>``. It also renames the
Radicale collection root from ``default`` to ``<new>`` (Radicale
re-reads htpasswd and rights on each request, so no restart is
needed after the migration).

The command is idempotent in the sense that it refuses to run if
``<new>`` already exists. It does NOT auto-rollback on partial
failure: the operator must move dirs back by hand if step 4 fails.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..configs import (
    DEFAULT_APP_NAME,
    DEFAULT_CACHE_HOME,
    DEFAULT_CONFIG_HOME,
    DEFAULT_DATA_HOME,
    DEFAULT_STATE_HOME,
)
from .store import UserStore, UserStoreError, default_store_path

OLD_USER = "default"


class MigrateError(Exception):
    """Raised when migration cannot proceed."""


def _xdg_bases() -> list[tuple[str, Path]]:
    """Return [(name, resolved_path), ...] for the four XDG bases."""
    return [
        ("config", Path(os.environ.get("XDG_CONFIG_HOME", str(DEFAULT_CONFIG_HOME)))),
        ("data", Path(os.environ.get("XDG_DATA_HOME", str(DEFAULT_DATA_HOME)))),
        ("state", Path(os.environ.get("XDG_STATE_HOME", str(DEFAULT_STATE_HOME)))),
        ("cache", Path(os.environ.get("XDG_CACHE_HOME", str(DEFAULT_CACHE_HOME)))),
    ]


def _user_dir(base: Path, user: str) -> Path:
    return base / DEFAULT_APP_NAME / user


def _move_dir(src: Path, dst: Path) -> bool:
    """Move ``src`` to ``dst`` (which must not exist).

    Returns ``True`` if a move happened, ``False`` if ``src`` did
    not exist. Raises :class:`MigrateError` on any other failure.
    """
    if not src.exists():
        return False
    if dst.exists():
        raise MigrateError(f"refusing to overwrite existing path: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def _rename_collection_root(data_dir: Path, new_user: str) -> bool:
    """Rename ``radicale/collections/collection-root/<OLD_USER>`` to
    ``<NEW_USER>`` if both the source and the data dir exist.
    """
    collections_root = data_dir / "radicale" / "collections" / "collection-root"
    if not collections_root.exists():
        return False
    old = collections_root / OLD_USER
    new = collections_root / new_user
    if not old.exists():
        return False
    if new.exists():
        raise MigrateError(f"refusing to overwrite existing collection root: {new}")
    old.rename(new)
    return True


def _rewrite_htpasswd(data_dir: Path, new_user: str) -> int:
    """Rewrite ``radicale/users`` so any line starting with
    ``default:`` becomes ``<new_user>:``. Returns the number of
    rewritten lines. Other lines (other users) are preserved.

    Without this, after the data dir is moved the htpasswd file
    still contains ``default:genesis-local`` and Radicale will
    reject connections from the new user until a Facade is
    constructed (which calls ``ensure_user``).
    """
    htpasswd = data_dir / "radicale" / "users"
    if not htpasswd.exists():
        return 0
    lines = htpasswd.read_text().splitlines()
    rewritten = 0
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            new_lines.append(line)
            continue
        user, _, rest = stripped.partition(":")
        if user == OLD_USER:
            new_lines.append(f"{new_user}:{rest}")
            rewritten += 1
        else:
            new_lines.append(line)
    if rewritten:
        htpasswd.write_text("\n".join(new_lines) + ("\n" if new_lines else ""))
    return rewritten


def _default_has_data() -> bool:
    """Return ``True`` if any ``genesis/default/`` directory exists
    or the users table contains a ``default`` entry."""
    for _, base in _xdg_bases():
        if _user_dir(base, OLD_USER).exists():
            return True
    store_path = default_store_path()
    if store_path.exists():
        try:
            store = UserStore(store_path)
            if store.get(OLD_USER) is not None:
                return True
        except Exception:
            # A malformed users file is not our problem during the
            # "is there anything to migrate?" check.
            pass
    return False


def migrate_default(new_username: str, *, users_yaml: Path | None = None) -> list[str]:
    """Move all ``default``-user filesystem state to ``new_username``.

    Returns a list of human-readable notes about what happened
    (for CLI output). Raises :class:`MigrateError` if the migration
    cannot proceed; on partial failure the operator must roll back
    by hand.
    """
    if new_username == OLD_USER:
        raise MigrateError(f"new username must differ from '{OLD_USER}'")
    if not _default_has_data():
        raise MigrateError("no 'default' user data found to migrate")

    notes: list[str] = []

    # 1. Refuse if the new user already has any state.
    for name, base in _xdg_bases():
        if _user_dir(base, new_username).exists():
            raise MigrateError(f"target user already has a {name} directory: {_user_dir(base, new_username)}")

    store = UserStore(users_yaml) if users_yaml else UserStore()
    if store.get(new_username) is not None:
        raise MigrateError(f"target user already exists in users file: {new_username}")

    # 2. Move the four XDG dirs.
    for name, base in _xdg_bases():
        src = _user_dir(base, OLD_USER)
        dst = _user_dir(base, new_username)
        if _move_dir(src, dst):
            notes.append(f"moved {name}: {src} -> {dst}")

    # 3. Rename the Radicale collection root inside the (moved) data dir.
    new_data = _user_dir(Path(os.environ.get("XDG_DATA_HOME", str(DEFAULT_DATA_HOME))), new_username)
    if _rename_collection_root(new_data, new_username):
        notes.append(f"renamed Radicale collection-root: {OLD_USER} -> {new_username}")

    # 3b. Rewrite htpasswd entries so Radicale knows about the new
    # username. Without this, CalDAV lookups for the new user would
    # 500 until a Facade is constructed for them (which lazily
    # calls ``ensure_user``). Doing it here makes a CLI-only
    # migration self-sufficient.
    rewritten = _rewrite_htpasswd(new_data, new_username)
    if rewritten:
        notes.append(f"rewrote {rewritten} htpasswd line(s): {OLD_USER} -> {new_username}")

    # 4. The per-user JWT secret lives inside the data dir, so step
    # 2 already moved it. Confirm presence and report.
    new_secret = new_data / "api_secret.key"
    if new_secret.exists():
        notes.append(f"kept api_secret.key at {new_secret}")
    else:
        notes.append("no api_secret.key found; a new one will be generated on first login")

    # 5. Remove the default entry from the users file if it exists.
    try:
        if store.get(OLD_USER) is not None:
            store.remove(OLD_USER)
            notes.append(f"removed '{OLD_USER}' from {store.path}")
    except UserStoreError:
        # No entry to remove.
        pass

    notes.append("")
    notes.append("Migration complete.")
    notes.append(f"Next: run 'genesis user create {new_username}' and log in.")
    return notes
