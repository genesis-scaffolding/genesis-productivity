# Plan for adr-021

## Steps

1. Create `src/genesis/ui/_session_cookie.py` with `SESSION_COOKIE_NAME`, `SESSION_MAX_AGE_SECONDS`, `_load_or_create_secret`, `_sign_session`, `_verify_session`, `rehydrate_from_cookie`, `set_session_cookie_response`, `clear_session_cookie_response`.
2. Create `src/genesis/ui/asgi_app.py` that wraps `app.py` with `st.App(...)` and adds `POST /api/session/login` and `POST /api/session/logout` routes calling into `_session_cookie`.
3. Refactor `src/genesis/ui/_auth.py` so `render_auth_gate` calls `rehydrate_from_cookie` first; `_login_form` issues the cookie via an `st.html(..., unsafe_allow_javascript=True)` fetch + reload; `logout` does the same with the logout endpoint and clears the cached Facade via `genesis.ui._state.clear_cached_facade`.
4. Update `Makefile` `ui` target to run `asgi_app.py` instead of `app.py`.
5. Add `tests/test_session_cookie_unit.py` covering sign/verify round-trip, expiry, tampered signature, malformed token, secret rotation, and response header shape.
6. Run `make lint`, `make typecheck`, `make test`.
7. Manual smoke test: `make serve`, log in via browser, sleep phone, wake, confirm session persists.