# Plan for adr-019

## Steps

1. Add `argon2-cffi` dependency via `uv add`.

2. Create `src/genesis/users/__init__.py` re-exporting the public surface.

3. Create `src/genesis/users/password.py` with Argon2id wrapper:
   `hash_password`, `verify_password`, parameter constants.

4. Create `src/genesis/users/store.py` with `UserStore`, `UserRecord`,
   `verify_credentials`, `create_user`, `change_password`,
   `list_users`, `delete_user`. Atomic write, 0o600, regex username
   validation. Public functions orchestrate the htpasswd too.

5. Create `src/genesis/users/htpasswd.py` with `RadicaleHtpasswd`
   managing the shared `<radicale_root>/users` file. Plaintext,
   0o600, atomic writes.

6. Create `src/genesis/users/rights.py` with `write_radicale_rights`
   writing the permissive `[base]` rule. Per-user rights sections
   were considered and dropped because Radicale's `from_file`
   rights backend never reloads; per-user sections would force a
   Radicale restart on every `create_user`/`delete_user`.

7. Create `src/genesis/users/migrate.py` with `migrate_default(new_username)`
   implementing the XDG-dir move + Radicale collection-root rename +
   JWT secret move.

8. Create `src/genesis/users/migrate_shared.py` with `migrate_shared(username)`
   moving per-user state into the shared layout
   (`<root>/radicale/`, `<root>/journals/`, `<root>/api_secret.key`).

9. Update `src/genesis/configs.py`: extend `Paths` with three SHARED
   fields (`radicale_root`, `journal_root`, `api_secret_key`).
   Per-user fields (`config`, `data`, `state`, `cache`) stay.

10. Update `src/genesis/api/security.py`: drop env var + `DEFAULT_USERNAME`,
    `secret_path_for(_)` returns the single shared path. Rotating the
    file invalidates every user's tokens at once.

11. Update `src/genesis/api/auth.py`: `/auth/login` calls
    `verify_credentials`. Add `GET /auth/me`.

12. Update `src/genesis/api/routers/productivity.py`: `_facade(subject)`,
    thread `subject` through every endpoint.

13. Update `src/genesis/dav/server.py`: `ServerManager._write_config`
    writes the rights file (permissive `[base]`) BEFORE starting
    Radicale. `ensure_user` delegates to `RadicaleHtpasswd`.
    `_write_config` no longer pre-populates the htpasswd — that's
    the orchestrator's job.

14. Update `src/genesis/core/facade.py`: use `paths.radicale_root`
    (shared) for the ServerManager; use
    `paths.journal_root / cfg.user` (per-user under shared) for the
    JournalFacade. `_ensure_server_running` calls `ensure_user`
    BEFORE `_write_config` so the rights file is correct on
    Radicale startup.

15. Add `src/genesis/cli/user.py` Typer subcommand with `create`,
    `list`, `change-password`, `delete`, `migrate-default`,
    `migrate-shared`.

16. Wire the user subcommand into `src/genesis/cli/app.py`.

17. Create `src/genesis/ui/_auth.py`: `is_authenticated`,
    `current_username`, `render_auth_gate`, `render_logout_button`,
    `logout`. `create_user` (called from the signup form) now also
    writes the htpasswd entry via `genesis.users.create_user`.

18. Update `src/genesis/ui/_state.py`: replace `@st.cache_resource`
    with a module-level dict keyed by username; `get_facade()` reads
    `st.session_state["username"]`. Logout does NOT evict the Facade
    cache (Radicale is shared).

19. Update `src/genesis/ui/app.py`: render logout button in sidebar
    only; auth is self-healing via `get_facade()` so no top-level
    `render_auth_gate()` (which would break sidebar via `st.stop()`).

20. Update tests:
    - `tests/conftest.py`: drop `GENESIS_API_PASSWORD`, seed default
      user via `UserStore`.
    - `tests/test_api_unit.py`: replace env-var tests with user-store
      tests; replace `test_per_user_signing_keys_isolate_tokens`
      with `test_shared_signing_key_validates_any_subject`.
    - `tests/test_api.py`: same; add `/auth/me` test, multi-user
      isolation test.
    - New `tests/test_users_unit.py` for `genesis.users` package
      (incl. htpasswd-sync orchestration).
    - New `tests/test_htpasswd_unit.py` for `genesis.users.htpasswd`.
    - New `tests/test_rights_unit.py` for `genesis.users.rights`
      (permissive `[base]` rule).
    - New `tests/test_migrate_unit.py` for `migrate_default`.
    - New `tests/test_migrate_shared_unit.py` for `migrate_shared`.
    - New `tests/test_facade_unit.py` for the Facade's
      `ensure_user` orchestration.
    - New `tests/test_ui_state_unit.py` for the shutdown wiring.

21. Run `make test`, `make lint`, `make typecheck`. Fix all findings.

22. Manual smoke: start UI, create user, log in, log out, log in as
    second user, verify isolation. Document in PR description.
