"""Cached Facade per user for the Streamlit UI.

The Facade wraps Radicale startup and the CalDAV/CardDAV/Journal
sub-facades. We keep one instance per (process, username) pair so
Radicale starts once per user per session, and so logout-then-login-as-someone-else
produces a fresh Facade.

The cache is a plain module-level dict: Streamlit runs single-threaded
per session, so no locking is needed.

Shutdown wiring:

When the Streamlit worker process receives SIGTERM or SIGINT (e.g.
``Ctrl+C`` in the shell that ran ``make serve``, or ``kill 0`` from
the makefile trap), we shut down every cached Facade. Each Facade
in turn terminates its Radicale subprocess, which lets the shell's
``wait`` complete without a second ``Ctrl+C``.
"""

from __future__ import annotations

import atexit
import os
import signal

from genesis.configs import resolve_config_path
from genesis.core.facade import Facade

_facade_cache: dict[str, Facade] = {}


def _shutdown_all() -> None:
    """Shut down every cached Facade. Best-effort; never raises."""
    for facade in list(_facade_cache.values()):
        try:
            facade.shutdown()
        except Exception:
            pass
    _facade_cache.clear()


def _install_signal_handlers() -> None:
    """Install SIGTERM/SIGINT handlers that shut down cached Facades.

    The handler tears down Radicale, then re-raises the default
    disposition so the worker actually exits. Otherwise streamlit's
    own signal chain keeps the process alive and the shell's
    ``wait`` blocks.
    """

    def _handler(signum: int, frame: object) -> None:
        _shutdown_all()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Signal can only be installed from the main thread; in
            # tests or worker threads we skip silently.
            pass


_install_signal_handlers()
atexit.register(_shutdown_all)


def get_facade() -> Facade:
    """Return the Facade for the authenticated user.

    Reads the username from ``st.session_state['username']`` (set by
    :mod:`genesis.ui._auth`). If no user is logged in, renders the
    auth gate inline and stops — this is the self-healing path for
    direct-URL navigation (``localhost:3000/statistics``), where
    Streamlit loads the page module even though ``app.py``'s auth
    gate stopped before ``set_page_config`` and ``st.navigation``
    ran.
    """
    # Local import keeps this module importable in tests that mock
    # Streamlit session state.
    import streamlit as st
    from streamlit.runtime.scriptrunner import get_script_run_ctx

    ctx = get_script_run_ctx()
    if ctx is None:
        raise RuntimeError("get_facade() must be called from within a Streamlit script run")
    username = st.session_state.get("username")
    if not username:
        # Lazy import to avoid a circular dependency at module load:
        # _auth imports clear_cached_facade from this module.
        from genesis.ui._auth import render_auth_gate

        render_auth_gate()
        # ``render_auth_gate`` calls ``st.stop()`` on the unauthenticated
        # path, so we never reach the line below. The raise keeps
        # the type checker happy and documents the invariant.
        raise RuntimeError("unreachable: render_auth_gate() should have stopped execution")

    if username not in _facade_cache:
        _facade_cache[username] = Facade(resolve_config_path(username))
    return _facade_cache[username]


def clear_cached_facade(username: str) -> None:
    """Remove ``username``'s cached Facade and shut it down.

    Used on logout so the next render does not see a stale instance.
    """
    facade = _facade_cache.pop(username, None)
    if facade is not None:
        try:
            facade.shutdown()
        except Exception:
            pass
