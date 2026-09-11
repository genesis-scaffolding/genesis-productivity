"""Radicale htpasswd file management.

The CalDAV/CardDAV server is a single shared Radicale process that
authenticates every user against one ``htpasswd``-style file
(ADR-019, option A). Each user has one line: ``<username>:<password>``.

This module owns that file. ``genesis.users.store`` orchestrates
user lifecycle (Argon2id in ``users.yaml`` plus this htpasswd) so
the two stores cannot drift.

File format and atomic-write discipline match
:mod:`genesis.users.store`: plain text, mode 0o600, write to a
sibling temp file then ``os.replace``.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_USERNAME_LINE_RE = re.compile(r"^([^:\s]+):(.*)$")
"""Match a non-empty ``user:rest`` line. Whitespace inside the
password is preserved by ``(.*)``."""


def htpasswd_path_for(radicale_root: Path) -> Path:
    """Return the canonical htpasswd location inside ``radicale_root``."""
    return Path(radicale_root) / "users"


class RadicaleHtpasswd:
    """Read/write the shared Radicale htpasswd file.

    A fresh instance points at ``<radicale_root>/users``. Mutations
    are atomic (write to a sibling temp file, chmod 0o600, then
    ``os.replace``). The class never deletes the file — an empty
    htpasswd is a valid state (Radicale would simply reject every
    request, which is what you want when no users exist).
    """

    def __init__(self, path: Path | None = None, *, radicale_root: Path | None = None) -> None:
        if path is None and radicale_root is None:
            raise ValueError("either path= or radicale_root= is required")
        if path is not None:
            self._path = Path(path)
        else:
            assert radicale_root is not None  # for type checker
            self._path = htpasswd_path_for(Path(radicale_root))

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, str]:
        """Return ``{username: password}`` for every parseable line.

        Lines that are blank or have no ``:`` separator are silently
        skipped — matches Radicale itself, which treats such lines
        as comments. A duplicate username keeps the first
        occurrence so the on-disk order is deterministic.
        """
        if not self._path.exists():
            return {}
        users: dict[str, str] = {}
        for line in self._path.read_text().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = _USERNAME_LINE_RE.match(stripped)
            if not match:
                continue
            user, password = match.group(1), match.group(2)
            if user not in users:
                users[user] = password
        return users

    def _write(self, users: dict[str, str]) -> None:
        """Atomically write the file with mode 0o600.

        On any failure the real file is untouched because the
        temp file lives in a different inode until ``os.replace``.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(f"{u}:{p}\n" for u, p in users.items())
        fd, tmp = tempfile.mkstemp(prefix=".htpasswd.", suffix=".tmp", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def add(self, username: str, password: str) -> None:
        """Add ``username`` with ``password``; overwrite if present.

        The overwrite case is intentional: ``genesis.user.change-password``
        calls this with the new password, so we want the latest
        value to win.
        """
        users = self.load()
        users[username] = password
        self._write(users)

    def remove(self, username: str) -> bool:
        """Remove ``username``. Return ``True`` if a line was removed."""
        users = self.load()
        if username not in users:
            return False
        del users[username]
        self._write(users)
        return True

    def change_password(self, username: str, new_password: str) -> None:
        """Replace ``username``'s password. No-op if not present.

        ``add`` would also work but this method makes the intent
        explicit at the call site.
        """
        users = self.load()
        if username not in users:
            return
        users[username] = new_password
        self._write(users)


# ---------------------------------------------------------------------------
# Module-level convenience wrappers bound to the default Radicale root.
# ---------------------------------------------------------------------------


def default_radicale_root() -> Path:
    """Return the shared Radicale root under XDG_DATA_HOME."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        data_home = Path(xdg)
    else:
        data_home = Path.home() / ".local" / "share"
    from ..configs import DEFAULT_APP_NAME

    return data_home / DEFAULT_APP_NAME / "radicale"


def default_htpasswd() -> RadicaleHtpasswd:
    """Return a :class:`RadicaleHtpasswd` bound to the default shared path."""
    return RadicaleHtpasswd(radicale_root=default_radicale_root())


def add_user(username: str, password: str) -> None:
    """Append ``username`` to the default htpasswd."""
    default_htpasswd().add(username, password)


def change_user_password(username: str, new_password: str) -> None:
    """Replace ``username``'s password in the default htpasswd."""
    default_htpasswd().change_password(username, new_password)


def remove_user(username: str) -> bool:
    """Remove ``username`` from the default htpasswd."""
    return default_htpasswd().remove(username)
