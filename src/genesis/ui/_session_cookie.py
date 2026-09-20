"""HMAC-signed session cookie helpers for the Streamlit UI.

A signed, HttpOnly cookie named ``genesis_session`` carries the
logged-in username across WebSocket reconnects (phone sleep/wake,
tab close, server restart). The cookie value format is::

    <expiry>.<username>.<hex-hmac-sha256>

Usernames are restricted to ``[a-zA-Z0-9_-]{1,32}`` by
:mod:`genesis.users.store`, so they never contain ``.`` and the
``rsplit(".", 2)`` parser below is unambiguous.

The HMAC secret is persisted at
``<XDG_DATA_HOME>/genesis/session_cookie.key`` (mode 0o600),
generated on first use. Rotating the file invalidates every
outstanding cookie at once.

This module sits beside :mod:`genesis.ui._auth` rather than under
:mod:`genesis.api.security` because the cookie format is
intentionally simpler than JWT (no header, no claims, no type)
and because ADR-004 forbids reverse imports between the ``ui``
and ``api`` packages.

Public surface used by :mod:`genesis.ui._auth` and
:mod:`genesis.ui.asgi_app`:

- :func:`rehydrate_from_cookie` — read cookie, restore session_state
- :func:`sign_intent` — sign a short-lived intent token the script
  emits to the browser so the cookie-setting route can mint a real
  session cookie without the password travelling twice
- :func:`verify_intent` — verify an intent token, enforcing the
  short TTL so a stolen long-lived session cookie cannot be
  replayed as an intent
- :func:`set_session_cookie_response` — Starlette response with
  ``Set-Cookie: genesis_session=...``
- :func:`clear_session_cookie_response` — Starlette response that
  expires the cookie
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from pathlib import Path

import streamlit as st
from starlette.responses import JSONResponse

from ..configs import DEFAULT_APP_NAME, DEFAULT_DATA_HOME

_log = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "genesis_session"
"""Cookie name. Path=/ so it covers both the Streamlit app and
the cookie-setting route under ``/api/session/``."""

SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
"""30 days. Matches the default lifetime of ``st.login()``'s
identity cookie."""

INTENT_TTL_SECONDS = 60
"""How long an intent token minted by the Streamlit script run
stays valid for the cookie-setting route. Short on purpose: the
token travels from the script response to the same browser
within the same WebSocket connection, so a 60-second window is
plenty."""

_SECRET_FILENAME = "session_cookie.key"


def _secret_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(DEFAULT_DATA_HOME)))
    return data_home / DEFAULT_APP_NAME / _SECRET_FILENAME


def _load_or_create_secret() -> bytes:
    """Return the HMAC secret, creating it on first call.

    File mode is 0o600 so other users on the host cannot read it.
    Rotating the file (deleting it) invalidates every outstanding
    cookie.
    """
    path = _secret_path()
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, secret)
    finally:
        os.close(fd)
    return secret


def _sign(username: str, ttl_seconds: int) -> str:
    """Sign ``username`` with the HMAC secret.

    Returns the cookie/intent value of the form
    ``<expiry>.<username>.<hex-hmac-sha256>``.
    """
    expiry = int(time.time()) + ttl_seconds
    payload = f"{expiry}.{username}"
    sig = hmac.new(_load_or_create_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify(token: str, *, max_ttl_seconds: int | None = None) -> str | None:
    """Return ``username`` iff ``token`` is well-formed, signed
    with the current secret, not expired, and (if
    ``max_ttl_seconds`` is set) was minted with a TTL no greater
    than that bound.

    ``max_ttl_seconds`` exists so the cookie-setting route can
    reject long-lived session cookies presented as intent tokens.
    """
    try:
        expiry_s, username, sig = token.rsplit(".", 2)
        expiry = int(expiry_s)
    except (ValueError, AttributeError):
        return None
    now = int(time.time())
    if expiry <= now:
        return None
    if max_ttl_seconds is not None and expiry > now + max_ttl_seconds:
        return None
    expected = hmac.new(
        _load_or_create_secret(),
        f"{expiry_s}.{username}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return username


def sign_intent(username: str) -> str:
    """Mint a short-lived intent token the script run hands to
    the browser so the cookie-setting route can mint a real
    session cookie without the password travelling twice."""
    return _sign(username, ttl_seconds=INTENT_TTL_SECONDS)


def verify_intent(token: str) -> str | None:
    """Verify an intent token.

    Rejects if the token's remaining lifetime exceeds
    :data:`INTENT_TTL_SECONDS` so a stolen long-lived session
    cookie cannot be replayed as an intent.
    """
    return _verify(token, max_ttl_seconds=INTENT_TTL_SECONDS)


def rehydrate_from_cookie() -> bool:
    """Read ``st.context.cookies``, verify it, and set
    ``st.session_state['username']`` if valid. Returns whether
    rehydration happened.

    Called by the Streamlit entrypoint on every script run.
    Idempotent: if ``st.session_state`` already has a username,
    the cookie is ignored.
    """
    if st.session_state.get("username"):
        _log.debug("rehydrate: session_state already has username, skipping")
        return False
    cookies = st.context.cookies
    token = cookies.get(SESSION_COOKIE_NAME, "")
    _log.info(
        "rehydrate: cookie %s (cookies seen: %s)",
        "present" if token else "absent",
        list(cookies.keys()),
    )
    if not token:
        return False
    username = _verify(token)
    if not username:
        _log.info("rehydrate: cookie failed verification")
        return False
    _log.info("rehydrate: restored username=%r", username)
    st.session_state["username"] = username
    return True


def set_session_cookie_response(username: str) -> JSONResponse:
    """Return a Starlette response that sets the session cookie
    for ``username``.

    Attributes: HttpOnly, SameSite=Lax, Path=/, Max-Age=30d.
    ``Secure`` is left off because the deployment is LAN-only
    over HTTP; a future deployment over HTTPS should flip it on.
    """
    response = JSONResponse({"ok": True})
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _sign(username, ttl_seconds=SESSION_MAX_AGE_SECONDS),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


def clear_session_cookie_response() -> JSONResponse:
    """Return a Starlette response that expires the session
    cookie. Path must match the original set-cookie path."""
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response
