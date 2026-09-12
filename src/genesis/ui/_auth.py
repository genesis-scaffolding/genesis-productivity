"""Streamlit authentication gate.

The Streamlit UI does not talk to FastAPI (ADR-015). Instead it
calls :func:`genesis.users.verify_credentials` directly, stores
the logged-in username in ``st.session_state``, and rebuilds the
cached Facade for that user.

Session persistence (ADR-021) layers on top of this: a signed,
HttpOnly cookie carries the username across WebSocket reconnects
(phone sleep/wake, tab close). On every script run the entrypoint
calls :func:`rehydrate_session` which restores
``st.session_state['username']`` from the cookie if present. On
successful login or logout the auth forms emit a small JavaScript
snippet that fetches ``/api/session/login`` or
``/api/session/logout`` to set / clear the cookie via the
``Set-Cookie`` header.

This module exposes:

- :func:`is_authenticated` / :func:`current_username` — read state
- :func:`rehydrate_session` — restore state from cookie, call
  once per script run from ``app.py``
- :func:`render_auth_gate` — show login + sign-up forms, gate the
  rest of the app
- :func:`render_logout_button` — sidebar widget
- :func:`logout` — clear session state and emit the cookie-clearing
  JavaScript
"""

from __future__ import annotations

import logging

import streamlit as st

from genesis.users import UserStoreError, create_user, verify_credentials

from ._session_cookie import rehydrate_from_cookie, sign_intent

_log = logging.getLogger(__name__)

_SESSION_KEY = "username"

# JavaScript emitted by the login / signup / logout forms to set or
# clear the session cookie via the ASGI cookie endpoint. Streamlit
# has no native way to write a cookie from a script run; this
# ``st.html`` snippet is the minimum custom behaviour required
# (the developing-with-streamlit skill allows custom HTML only
# when no native element provides the required behaviour, and
# there is no native element that sets cookies).
_COOKIE_FETCH_JS = (
    "<script>"
    "console.log('[genesis] cookie fetch starting', {url});"
    "fetch({url}, {{"
    "method: 'POST',"
    "headers: {{'Content-Type': 'application/json'}},"
    "body: JSON.stringify({body})"
    "}}).then(function (r) {{"
    "console.log('[genesis] cookie fetch response', r.status);"
    "if (r.ok) location.reload();"
    "else document.body.innerText = 'Failed to persist session';"
    "}}).catch(function (e) {{"
    "console.error('[genesis] cookie fetch error', e);"
    "}});"
    "</script>"
)


def is_authenticated() -> bool:
    return bool(st.session_state.get(_SESSION_KEY))


def current_username() -> str:
    """Return the logged-in username. Raises ``KeyError`` if no user is
    in session state (callers should gate on :func:`is_authenticated`
    first)."""
    return st.session_state[_SESSION_KEY]


def rehydrate_session() -> None:
    """Restore ``st.session_state['username']`` from the session
    cookie if one is present. Call once per script run, from the
    Streamlit entrypoint, before any gate check.

    Idempotent: if ``st.session_state`` already carries a
    username, the cookie is ignored. Returns nothing; the
    boolean the underlying function returns is only useful in
    tests.
    """
    rehydrate_from_cookie()


def _emit_session_cookie_js() -> None:
    """Render the JavaScript snippet that asks the ASGI route to
    set the session cookie for the freshly authenticated user.

    The intent token is short-lived (60 s) and signed by the same
    secret that signs the session cookie. The route verifies it
    and issues the long-lived ``Set-Cookie`` header in response.
    """
    username = st.session_state[_SESSION_KEY]
    intent = sign_intent(username)
    _log.info("emitting cookie JS for username=%r intent=%r", username, intent)
    st.html(
        _COOKIE_FETCH_JS.format(
            url="'/api/session/login'", body=f'{{"intent": "{intent}"}}'
        ),
        unsafe_allow_javascript=True,
    )


def logout() -> None:
    """Clear this session's username and emit the cookie-clearing
    JavaScript.

    Intentionally does NOT evict the cached Facade or shut down
    Radicale — the cache and the Radicale subprocess are shared
    across all browser sessions in the same Streamlit worker, so
    Alice logging out must not kill the subprocess that Bob is
    still using. The Facade stays cached and Radicale keeps
    running; on Streamlit process exit the SIGTERM handler in
    :mod:`genesis.ui._state` tears everything down.

    The cookie-clearing JS triggers a ``location.reload()`` which
    re-enters the script run without the cookie, so the auth
    gate fires on the next render.
    """
    st.session_state.pop(_SESSION_KEY, None)
    st.html(
        _COOKIE_FETCH_JS.format(
            url="'/api/session/logout'", body="{}"
        ),
        unsafe_allow_javascript=True,
    )


def _login_form() -> None:
    """Render the Sign In form."""
    with st.form("login_form", clear_on_submit=True):
        st.subheader("Sign In")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign In")
    if submitted:
        if verify_credentials(username, password):
            st.session_state[_SESSION_KEY] = username
            _emit_session_cookie_js()
        else:
            st.error("Invalid username or password.")


def _signup_form() -> None:
    """Render the Create Account form."""
    with st.form("signup_form", clear_on_submit=True):
        st.subheader("Create Account")
        username = st.text_input("Choose username")
        password = st.text_input("Password", type="password")
        confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Create Account")
    if submitted:
        if not username or not password:
            st.error("Username and password are required.")
            return
        if password != confirm:
            st.error("Passwords do not match.")
            return
        try:
            create_user(username, password)
        except UserStoreError as e:
            st.error(str(e))
            return
        st.session_state[_SESSION_KEY] = username
        st.success(f"Account created. Welcome, {username}.")
        _emit_session_cookie_js()


def render_auth_gate() -> None:
    """If no user is logged in, render the auth UI and stop.

    On return, the caller can assume ``st.session_state['username']``
    is set.
    """
    if is_authenticated():
        return

    st.title("genesis")
    st.caption("Productivity system. Sign in to continue.")

    tab_signin, tab_signup = st.tabs(["Sign In", "Create Account"])
    with tab_signin:
        _login_form()
    with tab_signup:
        _signup_form()

    st.stop()


def render_logout_button() -> None:
    """Render a sidebar logout control. No-op if not authenticated."""
    if not is_authenticated():
        return
    with st.sidebar:
        st.caption(f"Signed in as **{st.session_state[_SESSION_KEY]}**")
        if st.button("Sign out", key="logout_btn"):
            logout()