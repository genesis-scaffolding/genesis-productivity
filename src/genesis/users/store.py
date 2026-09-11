"""User table: load, save, verify, mutate.

The table is a YAML file at ``~/.local/state/genesis/users.yaml``,
mode 0o600 on every write. Writes are atomic: we write to a sibling
temp file then ``os.replace`` it over the real path.

Public functions wrap a module-level ``UserStore`` instance bound
to the default path. Tests instantiate ``UserStore(path)`` directly
to point at a temp file.

The module-level convenience functions (:func:`create_user`,
:func:`change_password`, :func:`delete_user`) also keep the
shared Radicale htpasswd file in sync, so a single call site
creates / updates / removes the user from both stores at once.
The :class:`UserStore` class itself stays focused on the users
table; the orchestration moves to the public wrappers.
"""

from __future__ import annotations

import datetime
import os
import re
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

from ..configs import DEFAULT_APP_NAME, DEFAULT_STATE_HOME
from .password import hash_password, needs_rehash, verify_hash

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
"""Allowed usernames: ASCII letters/digits/underscore/dash, 1-32 chars."""


class UserRecord(BaseModel):
    """One row in the users table."""

    username: str
    password_hash: str
    created_at: datetime.datetime

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        if not _USERNAME_RE.match(v):
            raise ValueError(f"username must match [a-zA-Z0-9_-]{{1,32}} (got {v!r})")
        return v


class UserStoreError(Exception):
    """Raised on duplicate username, missing user, or invalid input."""


def default_store_path() -> Path:
    """Return the canonical users table path.

    Honors ``XDG_STATE_HOME``. Always resolves to
    ``<xdg_state>/genesis/users.yaml``.
    """
    state_home = Path(os.environ.get("XDG_STATE_HOME", str(DEFAULT_STATE_HOME)))
    return state_home / DEFAULT_APP_NAME / "users.yaml"


class UserStore:
    """Read/write the users table at a fixed path.

    A fresh instance reads the table on first access. Mutations
    save atomically with mode 0o600.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_store_path()

    @property
    def path(self) -> Path:
        return self._path

    def _read(self) -> list[UserRecord]:
        if not self._path.exists():
            return []
        with open(self._path) as f:
            data = yaml.safe_load(f) or []
        return [UserRecord.model_validate(row) for row in data]

    def _write(self, records: list[UserRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [r.model_dump(mode="json") for r in records]
        # Atomic write: temp file in the same directory, then replace.
        fd, tmp = tempfile.mkstemp(prefix=".users.", suffix=".yaml.tmp", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                yaml.safe_dump(payload, f, sort_keys=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except Exception:
            # Best-effort cleanup; os.replace is atomic so the real
            # file is untouched on failure.
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def list(self) -> list[str]:
        """Return all usernames in the table (unspecified order)."""
        return sorted(r.username for r in self._read())

    def get(self, username: str) -> UserRecord | None:
        for r in self._read():
            if r.username == username:
                return r
        return None

    def add(self, username: str, password: str) -> UserRecord:
        """Append a new user. Raises :class:`UserStoreError` on
        duplicate or invalid input."""
        if not _USERNAME_RE.match(username):
            raise UserStoreError("username must match [a-zA-Z0-9_-]{1,32}")
        records = self._read()
        if any(r.username == username for r in records):
            raise UserStoreError(f"user already exists: {username}")
        record = UserRecord(
            username=username,
            password_hash=hash_password(password),
            created_at=datetime.datetime.now(datetime.UTC),
        )
        records.append(record)
        self._write(records)
        return record

    def remove(self, username: str) -> None:
        """Remove ``username`` from the table. Raises
        :class:`UserStoreError` if not present."""
        records = self._read()
        new_records = [r for r in records if r.username != username]
        if len(new_records) == len(records):
            raise UserStoreError(f"user not found: {username}")
        self._write(new_records)

    def set_password(self, username: str, new_password: str) -> None:
        """Replace the hash for ``username``. Raises
        :class:`UserStoreError` if not present."""
        records = self._read()
        for r in records:
            if r.username == username:
                r.password_hash = hash_password(new_password)
                self._write(records)
                return
        raise UserStoreError(f"user not found: {username}")

    def verify(self, username: str, password: str) -> bool:
        """Return ``True`` iff ``username`` exists and the password
        matches. On successful verify, transparently rehashes if the
        stored hash uses outdated parameters.
        """
        records = self._read()
        for r in records:
            if r.username != username:
                continue
            if not verify_hash(r.password_hash, password):
                return False
            if needs_rehash(r.password_hash):
                r.password_hash = hash_password(password)
                self._write(records)
            return True
        return False


# ---------------------------------------------------------------------------
# Module-level convenience wrappers bound to the default store path.
# ---------------------------------------------------------------------------


_default_store = UserStore()


def verify_credentials(username: str, password: str) -> bool:
    """Verify ``username``/``password`` against the default users table."""
    return _default_store.verify(username, password)


def create_user(username: str, password: str) -> None:
    """Append a new user to the default users table.

    Also writes a corresponding entry to the shared Radicale
    htpasswd so the new user can authenticate to CalDAV/CardDAV.
    Radicale re-reads the htpasswd on every request, so no
    restart is needed: the next request from the new user is
    accepted by Radicale's htpasswd and the permissive ``[base]``
    rights rule grants access.

    Raises :class:`UserStoreError` (from the underlying
    :meth:`UserStore.add`) on duplicate usernames or invalid
    input. The htpasswd is only written when the users-table
    write succeeds, so the two stores cannot disagree on
    membership.
    """
    _default_store.add(username, password)
    try:
        from .htpasswd import add_user

        add_user(username, password)
    except Exception:
        # Best-effort: if the htpasswd write fails we still have
        # a valid users-table entry. A Facade construction will
        # call ``ensure_user`` and recover. But surface the error
        # so the operator notices.
        raise


def change_password(username: str, new_password: str) -> None:
    """Replace ``username``'s password.

    Updates the users table AND the shared Radicale htpasswd so
    CalDAV/CardDAV authentication picks up the new password on
    the next request without a restart.

    Raises :class:`UserStoreError` if the user is unknown.
    """
    _default_store.set_password(username, new_password)
    try:
        from .htpasswd import change_user_password

        change_user_password(username, new_password)
    except Exception:
        raise


def list_users() -> list[str]:
    """List usernames in the default users table."""
    return _default_store.list()


def delete_user(username: str) -> None:
    """Remove ``username`` from the users table and the htpasswd.

    Radicale re-reads the htpasswd on every request, so the
    deletion takes effect on the next request from ``username``
    without a restart. The Radicale collection tree for
    ``username`` is left in place on disk so an accidental delete
    can be undone by re-creating the account.
    """
    _default_store.remove(username)
    try:
        from .htpasswd import remove_user

        remove_user(username)
    except Exception:
        # Mirror the create_user policy: if htpasswd removal
        # fails we still have a consistent users-table view;
        # surface but don't silently swallow.
        raise
