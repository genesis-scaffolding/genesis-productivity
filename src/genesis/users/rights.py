"""Generate the Radicale ``rights`` file from the user table.

The shared Radicale server uses a permissive ``[base]`` rule that
grants any authenticated user (``user = .+``) full access to any
collection. The htpasswd is the per-user authentication gate;
the application layer (Streamlit ``session_state``, JWT subject,
per-user Facade) scopes every request to the right user.

This deliberately drops the per-user rights sections the first
implementation of option A had. Per-user sections require a
Radicale restart whenever membership changes — Radicale's
``from_file`` rights backend reads the file once at startup and
never reloads. Restarting on every ``create_user`` is untenable
at any non-trivial signup rate.

The isolation contract is now: an authenticated user cannot
impersonate another user (htpasswd enforces separate credentials
per row), and the application never sends a request to a
collection on behalf of the wrong user (Streamlit session state
+ JWT subject + per-user Facade). A user with shell access to
the host could still send raw CalDAV requests to a peer's
collection if they tried; that is the documented trade-off.

If stricter per-collection isolation is needed later, the rule
set can be extended with per-user sections via an operator
command — the helpers below accept an explicit username list
for that purpose.
"""

from __future__ import annotations

from pathlib import Path

from .htpasswd import htpasswd_path_for

_BASE_RIGHTS = """[base]
user = .+
collection = .*
permissions = RrWw
"""


def rights_path_for(radicale_root: Path) -> Path:
    """Return the canonical rights file location inside ``radicale_root``."""
    return Path(radicale_root) / "rights"


def write_radicale_rights(radicale_root: Path, usernames: list[str] | None = None) -> Path:
    """Regenerate ``<radicale_root>/rights``.

    Always writes a single permissive ``[base]`` rule. The
    ``usernames`` argument is ignored — it is kept in the
    signature so callers that still pass a user list (e.g.
    ``migrate-shared`` notes) continue to work without change.

    Returns the path written so callers can log it.
    """
    path = rights_path_for(Path(radicale_root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_BASE_RIGHTS)
    return path


def write_radicale_rights_from_htpasswd(radicale_root: Path) -> Path:
    """Convenience wrapper: derive usernames from the htpasswd file.

    Like :func:`write_radicale_rights`, this always emits the
    permissive ``[base]`` rule. The htpasswd argument exists so
    the call site reads naturally; the actual rule content is
    independent of which users are listed there.
    """
    from .htpasswd import RadicaleHtpasswd

    RadicaleHtpasswd(path=htpasswd_path_for(Path(radicale_root)))
    return write_radicale_rights(Path(radicale_root))
