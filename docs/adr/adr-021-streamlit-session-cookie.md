# 021. Streamlit session cookie persistence

## Context

The Streamlit UI's auth gate (ADR-019, `genesis.ui._auth`) stores
the logged-in username in `st.session_state['username']`. Session
state lives inside the WebSocket connection between the browser
and the Streamlit worker process. When the WebSocket drops, the
session state is gone.

This is a real problem for the primary mobile use case: starting
and stopping time tracking from a phone. The OS suspends the
browser when the screen sleeps, the WebSocket times out, and the
user lands back on the login form. Repeating the username and
password each time makes the UI effectively unusable for the
intended workflow.

The canonical Streamlit fix is `st.login()` + `st.user`, which
stores the OIDC identity in a cookie that survives WebSocket
disconnects (cookies live in the browser, not in the socket).
That path is rejected here for two reasons:

1. It requires an external OIDC provider (Google, Authentik,
   Keycloak, etc.). The project's auth model is local
   username/password against `genesis.users`, with the FastAPI
   side authenticating the CLI and agents over OAuth2 password
   grant. Replacing it with OIDC would also touch ADR-019, the
   FastAPI auth router, and the CLI login flow — out of scope
   for this fix.
2. ADR-015 says the Streamlit UI does not talk to FastAPI. So
   the Streamlit identity and the FastAPI identity are
   independent today, and there is no value in unifying them
   just to fix the phone-sleep problem.

We want a solution that uses only native Streamlit surfaces
(`st.context.cookies`, `st.session_state`, `st.App` middleware)
and preserves the existing `genesis.users` auth backend.

## Decision

### Cookie format

A signed, HttpOnly cookie named `genesis_session` carries the
username. The payload is `<unix-expiry>.<username>`; the cookie
value is `<payload>.<hex-hmac-sha256>`. The HMAC secret lives at
`<XDG_DATA_HOME>/genesis/session_cookie.key` (mode 0o600),
generated on first use. Rotating the file invalidates every
outstanding session cookie at once — the same property as the
JWT signing secret in `genesis.api.security`.

Cookie attributes:

- `Max-Age=2592000` (30 days; matches `st.login()` default)
- `HttpOnly` — JavaScript on the page cannot read it
- `SameSite=Lax` — sent on top-level navigation, blocked on
  third-party POST; sufficient because the UI is served from the
  same origin as the cookie-setting endpoint
- `Path=/` — covers both the Streamlit app and the API routes
- `Secure` — left off because the deployment is LAN-only over
  HTTP; a future deployment over HTTPS should flip this on

### Module

New file `src/genesis/ui/_session_cookie.py` with:

- `SESSION_COOKIE_NAME`, `SESSION_MAX_AGE_SECONDS` — public
  constants
- `_load_or_create_secret() -> bytes` — read or generate the
  HMAC key (mirrors `genesis.api.security._load_or_create_secret`)
- `_sign_session(username: str) -> str` — produce the cookie value
- `_verify_session(token: str) -> str | None` — return username or
  `None` on any failure (expired, malformed, bad signature)
- `rehydrate_from_cookie() -> bool` — read `st.context.cookies`,
  verify, set `st.session_state['username']` if valid. Returns
  whether rehydration happened.
- `set_session_cookie_response(username: str) -> JSONResponse`
  and `clear_session_cookie_response() -> JSONResponse` —
  Starlette response objects with the right `Set-Cookie` header.
  They are imported by `asgi_app.py` for the route handlers.

The module deliberately sits beside `_auth.py` (not under
`genesis.api.security`) for two reasons. First, the cookie format
is intentionally simpler than JWT — no header, no claims, no
type — so it does not share structure with the JWT helpers.
Second, ADR-004 forbids `genesis.ui` importing from `genesis.api`
in the reverse direction; sharing one helper there would
establish a cross-module dependency for one function.

### ASGI entrypoint

New file `src/genesis/ui/asgi_app.py` wraps the existing
`src/genesis/ui/app.py` with `st.App(...)` and adds two routes:

- `POST /api/session/login` — body `{"username": str,
  "password": str}`. Calls `genesis.users.verify_credentials`.
  On success returns a `JSONResponse` with `Set-Cookie: genesis_session=...`.
  On failure returns 401 with a generic error message (same
  shape as `genesis.api.auth.login` so the UI and CLI do not
  leak whether a username exists).
- `POST /api/session/logout` — body `{}`. Returns a
  `JSONResponse` with `Set-Cookie: genesis_session=; Max-Age=0`.

The route handlers live in `asgi_app.py` rather than `app.py`
because they need Starlette's `Request`/`Response` types, and
the Streamlit script body is not a Starlette route handler.

### Auth flow

`genesis.ui._auth` is updated:

1. `render_auth_gate()` first calls `rehydrate_from_cookie()`.
   If the cookie rehydrates successfully, the gate passes
   immediately — no login form.
2. The login form, on successful `verify_credentials`, sets
   `st.session_state['username']` and renders a one-shot
   `st.html(..., unsafe_allow_javascript=True)` snippet that
   `fetch`es `POST /api/session/login` to set the cookie,
   then `location.reload()`s. The reload re-enters the script
   run with the cookie now present, so the rehydrate path
   becomes a no-op.
3. `logout()` sets `st.session_state['username'] = None`,
   clears the cached Facade for that user, and renders the
   same one-shot snippet against `POST /api/session/logout`,
   then `location.reload()`s.

The `st.html` JavaScript fetch is the only unavoidable custom
behavior — Streamlit has no native way to set a cookie from a
script run, and the skill explicitly allows custom HTML when no
native element provides the required behavior. The JS payload
is four lines and contains no dynamic data besides the
hardcoded route path.

### Launch

The `ui` target in the Makefile switches from
`streamlit run src/genesis/ui/app.py` to
`streamlit run src/genesis/ui/asgi_app.py`. `streamlit run`
detects the `st.App` instance and runs it through Streamlit's
uvicorn integration, so the developer-facing command is
unchanged.

## Status

Accepted.

## Consequences

- Phone sleep/wake no longer logs the user out: the cookie
  rehydrates `st.session_state` on every new WebSocket.
- The HMAC secret is auto-generated and persisted to disk, so
  cookies survive server restarts. Manual rotation (deleting
  the key file) is a coordinated logout of every browser.
- A new dependency on Starlette (`Request`, `JSONResponse`) in
  the UI module — but `streamlit` already pulls in Starlette
  transitively, and `st.App` is part of Streamlit's public API.
- `_login_form` and `_signup_form` retain the existing
  Streamlit form widgets and `st.error` UX. The cookie write
  is the only new piece, and it is invisible to the user.
- FastAPI auth and the CLI login flow are untouched.
- Tests: new unit-test file `tests/test_session_cookie_unit.py`
  covers `_sign_session` / `_verify_session` round-trip,
  expired-token rejection, tampered-signature rejection,
  malformed-token rejection, secret rotation, and the
  `set/clear_session_cookie_response` header shape.