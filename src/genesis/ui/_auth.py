"""Streamlit authentication gate.

The Streamlit UI does not talk to FastAPI (ADR-015). Instead it
calls :func:`genesis.users.verify_credentials` directly, stores
the logged-in username in ``st.session_state``, and rebuilds the
cached Facade for that user.

This module exposes:

- :func:`is_authenticated` / :func:`current_username` — read state
- :func:`render_auth_gate` — show login + sign-up forms, gate the rest of the app
- :func:`render_logout_button` — sidebar widget
- :func:`logout` — clear session state and the Facade cache
"""

from __future__ import annotations

import streamlit as st

from genesis.users import UserStoreError, create_user, verify_credentials

_SESSION_KEY = "username"


def is_authenticated() -> bool:
    return bool(st.session_state.get(_SESSION_KEY))


def current_username() -> str:
    """Return the logged-in username. Raises ``KeyError`` if no user is
    in session state (callers should gate on :func:`is_authenticated`
    first)."""
    return st.session_state[_SESSION_KEY]


def logout() -> None:
    """Clear this session's username.

    Intentionally does NOT evict the cached Facade or shut down
    Radicale — the cache and the Radicale subprocess are shared
    across all browser sessions in the same Streamlit worker, so
    Alice logging out must not kill the subprocess that Bob is
    still using. The Facade stays cached and Radicale keeps
    running; on Streamlit process exit the SIGTERM handler in
    :mod:`genesis.ui._state` tears everything down.

    The next render after logout will hit the auth gate.
    """
    st.session_state.pop(_SESSION_KEY, None)


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
            st.rerun()
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
        st.rerun()


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
            st.rerun()
