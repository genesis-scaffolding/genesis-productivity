"""ASGI entrypoint for the Streamlit UI.

Wraps :mod:`genesis.ui.app` with :class:`streamlit.App` and adds
two cookie-management routes. Streamlit runs this file with its
built-in uvicorn integration when invoked via
``streamlit run src/genesis/ui/asgi_app.py``.

Routes:

- ``POST /api/session/login`` — body ``{"intent": "<short-lived
  signed token>"}``. The intent is signed by the Streamlit script
  after the login form validates credentials; this route verifies
  it and issues the long-lived session cookie via ``Set-Cookie``.

- ``POST /api/session/logout`` — body ``{}``. Clears the session
  cookie.

The intent flow keeps the user's password off the cookie-setting
round-trip. The Streamlit script already verified the password
against :func:`genesis.users.verify_credentials`; the route only
needs to confirm the intent token is a fresh, server-signed
proof that the same script just authenticated the same user.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import streamlit as st
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from genesis.ui._session_cookie import (
    clear_session_cookie_response,
    set_session_cookie_response,
    verify_intent,
)

# Configure logging early so the ASGI route handlers and the
# Streamlit script-run helpers share one configuration. Set
# GENESIS_UI_LOG_LEVEL=INFO (or DEBUG) to see cookie / rehydrate
# diagnostics. Defaults to WARNING so production logs are quiet.
logging.basicConfig(
    level=os.environ.get("GENESIS_UI_LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    stream=sys.stderr,
    force=True,
)

_log = logging.getLogger(__name__)

# Absolute path so the wrapper works regardless of CWD or how it
# was launched (streamlit run vs App.run() vs uvicorn).
_SCRIPT_PATH = Path(__file__).parent / "app.py"


async def _session_login(request: Request) -> JSONResponse:
    _log.info("/api/session/login hit, content-type=%s", request.headers.get("content-type"))
    try:
        body = await request.json()
    except Exception as e:
        _log.info("/api/session/login: invalid json body: %s", e)
        return JSONResponse({"ok": False, "error": "invalid body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid body"}, status_code=400)
    intent = body.get("intent")
    if not isinstance(intent, str) or not intent:
        _log.info("/api/session/login: missing intent")
        return JSONResponse({"ok": False, "error": "missing intent"}, status_code=400)
    username = verify_intent(intent)
    if not username:
        _log.info("/api/session/login: invalid intent")
        return JSONResponse({"ok": False, "error": "invalid intent"}, status_code=401)
    _log.info("/api/session/login: setting cookie for username=%r", username)
    return set_session_cookie_response(username)


async def _session_logout(request: Request) -> JSONResponse:
    _log.info("/api/session/logout hit")
    return clear_session_cookie_response()


app = st.App(
    str(_SCRIPT_PATH),
    routes=[
        Route("/api/session/login", _session_login, methods=["POST"]),
        Route("/api/session/logout", _session_logout, methods=["POST"]),
    ],
)
