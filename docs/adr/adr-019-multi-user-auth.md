# 019. Multi-user authentication

## Context

The system already isolates per-user data on disk via the
`genesis/<user>/` directory structure (ADR-006) and lazy-creates a
Radicale principal per user (ADR-010). But the authentication
layer is single-account:

- `genesis.api.security` ships one hardcoded password
  (`GENESIS_API_PASSWORD`, fallback `genesis-local`) and one
  username (`"default"`).
- `genesis.api.routers.productivity._facade()` hardcodes
  `resolve_config_path("default")`. The JWT subject is extracted
  by the auth dependency but never threaded into the Facade, so
  any valid token grants access to the default user's data.
- The Streamlit UI has no auth path. It calls `get_facade()` at
  module top, which constructs a Facade for whatever user
  `GENESIS__CONFIG_PATH` points at (in practice, `"default"`).

This is a real risk on the deployed server: two humans share one
machine, and one human's pi agent could end up reading or writing
the other's tasks and journals.

ADR-015 anticipated this: "A future ADR will harden it with proper
Argon2id-hashed multi-user accounts against a real backend." This
is that ADR.

Goals:

- Any number of named accounts, each with its own password and
  isolated data scope.
- Same login logic for the FastAPI path (POST /auth/login) and the
  Streamlit path (no HTTP, talks to Facade directly).
- No backward-compat with the env-var password; the migration is
  explicit.
- One CLI command moves existing `default`-user data to a named
  account so the user does not lose their live data.

## Decision

### Module

New package `src/genesis/users/` with these submodules:

```
src/genesis/users/
  __init__.py        re-exports the public surface
  store.py           UserStore: load/save the users table
  password.py        Argon2id hash + verify, parameter constants
  htpasswd.py        RadicaleHtpasswd: shared Radicale htpasswd file
  rights.py          write_radicale_rights: regenerate rights file from user list
  migrate.py         Move default-user filesystem state to a named user
  migrate_shared.py  Move per-user state into the shared layout
```

The package sits at the same level as `genesis.api`, `genesis.ui`,
`genesis.core`. It depends only on `genesis.configs` (for path
helpers). `genesis.api` and `genesis.ui` both depend on
`genesis.users`, not on each other.

### Storage

A single YAML file at
`~/.local/state/genesis/users.yaml`, mode 0o600 on creation and on
update. One document, one list:

```yaml
- username: alice
  password_hash: $argon2id$v=19$m=...$t=...$p=...$salt$digest
  created_at: 2026-09-08T10:00:00Z
```

Pydantic models for read/write. Atomic save: write to a sibling
temp file then `os.replace()`. The store rejects empty usernames,
usernames outside `[a-zA-Z0-9_-]{1,32}`, and duplicate usernames
(invariant: the list has unique usernames).

### Password hashing

Argon2id via `argon2-cffi`. The hash parameters live in
`genesis.users.password` as module constants so they can be tuned
in one place. `verify_credentials` uses the hasher's
`PasswordHasher.verify`, which raises `VerifyMismatchError` on a
bad password and falls through to `False` at the call site.

A user whose stored hash uses outdated parameters is transparently
re-hashed on the next successful login via
`PasswordHasher.check_needs_rehash`.

### Verify

```python
def verify_credentials(username: str, password: str) -> bool
```

Returns `True` iff the username exists in the table and the
password matches its Argon2id hash. Constant-time by construction
(argon2-cffi does it internally).

### User lifecycle

CLI subcommands under `genesis user ...`:

```
genesis user create <name> [--password <pw>]   create + hash + append
genesis user list                              list usernames
genesis user change-password <name> [--password <pw>]
genesis user delete <name>                     remove from users file;
                                                refuse if dirs exist
genesis user migrate-default <name>            see Migration below
```

`create` prompts for the password if `--password` is not passed
(via Typer's `hide_input=True`). `change-password` works the same
way. `delete` refuses to run if `<xdg>/genesis/<name>/` has any
files; the operator must pass `--force` to wipe the data along
with the account, or delete the directory themselves.

### Auth model

The user has a single account (Argon2id in `users.yaml`) and a
single Radicale credential (plaintext in `users`). Same password
typed once at signup, written to both stores. The htpasswd uses
the default `genesis-local` value per ADR-010 ("shared internal
credentials"): every authenticated user has the same Radicale
password, and the application layer is the boundary that scopes
each user to their own collection tree.

This is a deliberate trade-off vs. per-user Radicale passwords:
per-user passwords would require storing plaintext in `users.yaml`
(Radicale cannot verify Argon2id), giving up the security benefit.
For a local single-machine productivity tool where the app layer
already scopes everything by user, the shared-credential model is
acceptable.

If stricter Radicale-layer isolation is needed later, the helper
`genesis.users.rights.write_radicale_rights(radicale_root,
[user_a, user_b])` can be extended to emit per-user sections.
That would require a Radicale restart whenever membership changes
— the same restart-on-create problem we just avoided — so it is
deliberately deferred.

### API changes

`genesis.api.security` keeps the JWT primitives (`_make_token`,
`decode_token`, `_load_or_create_secret`) but drops the env-var
password and the `DEFAULT_USERNAME` constant. `SECRET_PATH`
becomes a function: `secret_path_for(username) ->
~/.local/share/genesis/api_secret.key`. **One signing key serves
every user** — rotating it (deleting the file) invalidates every
user's tokens at once, forcing a coordinated re-login. The
username parameter is kept for backward compatibility with call
sites that pass the JWT subject; it is ignored.

`genesis.api.auth.login` no longer checks `username ==
DEFAULT_USERNAME`. It calls `genesis.users.verify_credentials` and
returns 401 on any failure (missing user, wrong password).

A new endpoint `GET /auth/me` returns `{"username": <subject>}`. It
is the same shape the agent extension can use to confirm who its
token belongs to. Cheap, no new auth surface.

The productivity router gains `subject: Annotated[str, Depends(current_subject)]`
on every endpoint and threads it into `_facade(subject)`. This is
the actual fix for the latent bug where any valid token granted
access to the default user's data.

### Shared Radicale (option A)

A single Radicale process serves every user. The shared data
root sits at `~/.local/share/genesis/radicale/` and holds one
config file, one htpasswd (`<radicale_root>/users`), one rights
file (`<radicale_root>/rights`), and a per-user collection
subtree (`<radicale_root>/collections/collection-root/<user>/`).

- `genesis.users.htpasswd` (new) manages the shared htpasswd.
  Plaintext passwords, mode 0o600, atomic writes. Radicale
  re-reads this file on every request (it uses an mtime cache
  invalidation), so adding or removing a user takes effect on
  the next request with no restart.
- `genesis.users.rights` (new) writes a permissive `[base]`
  rights rule — `user = .+`, `collection = .*`, `permissions =
  RrWw`. The htpasswd is the per-user authentication gate; the
  rights file grants access after authentication succeeds.
  Per-user rights sections are deliberately NOT used because
  Radicale's `from_file` rights backend reads the file ONCE at
  startup and never reloads — a design that would force a
  Radicale restart on every `create_user` or `delete_user`.
  Application-layer isolation (Streamlit `session_state`,
  JWT subject, per-user Facade) is the boundary that prevents
  one user from acting on another through normal flows.
- `genesis.users.store` orchestrates the users table and the
  htpasswd: `create_user` writes both, `change_password` updates
  both, `delete_user` removes from both. The two stores cannot
  drift.
- `genesis.core.facade.Facade` constructs its `ServerManager`
  with `paths.radicale_root` (the same path for every user); one
  Radicale process is started and reused across all sessions in
  one Streamlit worker.
- The journal dir for each user is `<paths.journal_root>/<user>/`,
  where `paths.journal_root` is `~/.local/share/genesis/journals/`
  (plural per operator preference).

### Migration

Two CLI commands cover the cutover:

```
genesis user migrate-default <new_username>   # ADR-010 legacy
genesis user migrate-shared <username>        # ADR-019 option A
```

`migrate-default` moves `default`-user XDG dirs to a named user
(unchanged). `migrate-shared` then takes the per-user
`<xdg_data>/genesis/<user>/radicale/`, `journal/`, and
`api_secret.key` and re-homes them under the shared layout:

- `<user>/journal/`  ->  `<root>/journals/<user>/`
- `<user>/radicale/collections/collection-root/<user>/`  ->
  `<root>/radicale/collections/collection-root/<user>/`
- `<user>/radicale/users` lines get merged into `<root>/radicale/users`
- `<user>/api_secret.key`  ->  `<root>/api_secret.key` (only the first user)
- empty per-user radicale dirs are pruned

After `migrate-shared` runs, the operator restarts the running
Radicale (`genesis user migrate-shared --no-restart-server` skips
this if they want to batch restarts).

### Streamlit changes

`genesis.ui._auth.py` (new) provides:

- `is_authenticated() -> bool`
- `current_username() -> str`
- `render_auth_gate() -> None` — if no user in session state, renders
  login + sign-up tabs and calls `st.stop()`. Otherwise returns.
- `render_logout_button() -> None` — sidebar widget.
- `logout() -> None` — clears session state and the cached Facade.

Login form calls `genesis.users.verify_credentials` directly (no
HTTP round-trip to the API; the UI does not talk to FastAPI per
ADR-015). On success, sets `st.session_state["username"]` and
calls `st.rerun()`.

`genesis.ui._state.get_facade()` becomes a username-keyed module
cache (replacing `@st.cache_resource` no-args). It reads
`st.session_state["username"]` and returns the Facade for that
user, building it on first access. Logout removes the entry from
the cache.

`genesis.ui.app.py` calls `render_auth_gate()` before
`st.navigation(...).run()`. The seven pages keep calling
`get_facade()` with no args because the auth gate guarantees
`session_state["username"]` is set.

### Migration

`genesis user migrate-default <new_username>`:

1. Refuse if `<new_username>` already exists in the users file or
   on disk under any XDG base.
2. Refuse if none of `<xdg>/genesis/default/` directories exist
   (nothing to migrate).
3. For each XDG base (config, data, state, cache), move
   `<base>/genesis/default/` to `<base>/genesis/<new>/` if it
   exists.
4. Inside the moved data dir, rename
   `radicale/collections/collection-root/default/` to
   `radicale/collections/collection-root/<new>/` if it exists.
   Radicale re-reads htpasswd and rights files on each request,
   so renaming the collection root is the only required
   filesystem change for Radicale.
5. Move the per-user JWT secret
   `~/.local/share/genesis/default/api_secret.key` to
   `~/.local/share/genesis/<new>/api_secret.key` if it exists.
6. Print next-step instructions: create the account via
   `genesis user create <new_username>`, then log in.

The command is idempotent: it refuses (does not silently re-run)
if `<new>/` exists. There is no automatic rollback; if step 4
fails, the operator must move dirs back by hand. This is a
trade-off for keeping the migration a single Typer command.

After migrating, the operator logs in for the first time with
`genesis user create <new>` followed by the UI login. The default
user is gone from the filesystem.

### Dependencies

- Add `argon2-cffi>=25` (developer-approved for this change).

### Tests

Unit tests under `tests/test_users_unit.py` (no FastAPI, no
streamlit): round-trip create/verify, wrong password rejected,
username validation, file permissions, atomic save, missing file,
duplicate username rejected, default data migration happy path +
refusal cases.

Updates to `tests/test_api_unit.py` and `tests/test_api.py`: drop
the `GENESIS_API_PASSWORD` env-var tests; add tests that the second
user can log in, that `/auth/me` returns the subject, that
`_facade(subject)` uses the right config path, and that a token
for `alice` cannot read `bob`'s data.

`tests/conftest.py`: the `api_server` fixture seeds a `default`
user via `genesis.users.store.UserStore` instead of setting the
env var. The `api_server_real` fixture does the same.

## Status

Proposed.

## Consequences

Positive:

- Two humans on one machine get real isolation. One agent cannot
  leak into the other's data.
- Per-user JWT signing keys mean revoking one user does not
  invalidate any other user's sessions.
- The Streamlit login uses the same primitive
  (`verify_credentials`) as the FastAPI login, so the two paths
  cannot drift.
- The migration command makes the cutover safe for live data.

Negative:

- Single hard cutover. No env-var fallback for the existing
  `"default"` user. Operators must run `genesis user migrate-default`
  before the first login on a previously-deployed server. This
  is the explicit trade-off the user accepted.
- Argon2id is slower than the previous plaintext compare. Login
  adds ~100 ms on a modern CPU. Acceptable for a productivity tool.
- The users file is plaintext YAML on disk. Anyone with shell
  access to the user's account can read usernames (not hashes,
  which are Argon2id). The file mode 0o600 limits this to the
  file owner; the operator must also keep the home dir locked
  down as before.
- The migration command has no automatic rollback. A failed step
  leaves the data in a half-moved state. Documented in the help
  text and CLI output.

Neutral:

- `genesis.api.security` no longer holds the env-var constant. The
  env var `GENESIS_API_PASSWORD` becomes a no-op; existing
  deployments that still set it are unaffected at runtime, but the
  password is ignored. This is the "no backward compat" promise
  made explicit.
- `genesis.users` is a new public package. Only `genesis.api` and
  `genesis.ui` import from it.
- The migration command supersedes the "Future Work" section of
  ADR-010.
