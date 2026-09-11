"""User accounts, Radicale htpasswd, and password verification.

This package is the single source of truth for the user table and
for Radicale's shared htpasswd file. Both ``genesis.api``
(FastAPI login) and ``genesis.ui`` (Streamlit login) call into
here; they never duplicate the verify logic.

Public surface:

- :func:`verify_credentials` — username + plain password -> bool
- :func:`create_user` — append a new user to the table AND htpasswd
- :func:`change_password` — replace a user's hash AND htpasswd entry
- :func:`list_users` — list usernames in the table
- :func:`delete_user` — remove a user from the table AND htpasswd
- :func:`migrate_default` — move ``default``-user data to a named user
- :func:`migrate_shared` — move per-user state into the shared layout

The user table lives at ``~/.local/state/genesis/users.yaml``,
mode 0o600. The htpasswd lives at
``~/.local/share/genesis/radicale/users``, also mode 0o600.
"""

from __future__ import annotations

from .htpasswd import RadicaleHtpasswd, default_htpasswd, htpasswd_path_for
from .migrate import MigrateError, migrate_default
from .migrate_shared import MigrateSharedError, migrate_shared
from .rights import write_radicale_rights, write_radicale_rights_from_htpasswd
from .store import (
    UserStore,
    UserStoreError,
    change_password,
    create_user,
    delete_user,
    list_users,
    verify_credentials,
)

__all__ = [
    "MigrateError",
    "MigrateSharedError",
    "RadicaleHtpasswd",
    "UserStore",
    "UserStoreError",
    "change_password",
    "create_user",
    "default_htpasswd",
    "delete_user",
    "htpasswd_path_for",
    "list_users",
    "migrate_default",
    "migrate_shared",
    "verify_credentials",
    "write_radicale_rights",
    "write_radicale_rights_from_htpasswd",
]
