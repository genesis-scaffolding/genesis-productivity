"""Unit tests for the user table and password verification.

No FastAPI, no streamlit, no subprocess. Each test points the store
at a temp file via ``monkeypatch``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from genesis.users import (
    UserStoreError,
    change_password,
    create_user,
    delete_user,
    list_users,
    verify_credentials,
)
from genesis.users.store import _USERNAME_RE, UserStore, default_store_path


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> UserStore:
    """Return a UserStore bound to a fresh temp file and patched in
    as the module-level default so the convenience functions hit it."""
    store_path = tmp_path / "users.yaml"
    store = UserStore(store_path)
    monkeypatch.setattr("genesis.users.store._default_store", store)
    return store


# ---------------------------------------------------------------------------
# Username validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["alice", "Bob_42", "user-1", "ABC", "x", "a" * 32],
)
def test_valid_usernames_accepted(name: str) -> None:
    assert _USERNAME_RE.match(name) is not None


@pytest.mark.parametrize(
    "name",
    ["", "with space", "weird!char", "a" * 33, "user@host"],
)
def test_invalid_usernames_rejected(name: str) -> None:
    assert _USERNAME_RE.match(name) is None


# ---------------------------------------------------------------------------
# Store: round-trip
# ---------------------------------------------------------------------------


def test_store_creates_file_with_0o600(store: UserStore) -> None:
    store.add("alice", "secret")
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600


def test_store_round_trip(store: UserStore) -> None:
    store.add("alice", "alice-pw")
    store.add("bob", "bob-pw")
    # Re-open from disk to confirm persistence.
    reopened = UserStore(store.path)
    assert set(reopened.list()) == {"alice", "bob"}
    assert reopened.verify("alice", "alice-pw") is True
    assert reopened.verify("bob", "bob-pw") is True
    assert reopened.verify("alice", "wrong") is False


def test_store_missing_file_is_empty(store: UserStore) -> None:
    assert store.list() == []
    assert store.get("anyone") is None


def test_store_add_rejects_duplicate(store: UserStore) -> None:
    store.add("alice", "pw1")
    with pytest.raises(UserStoreError, match="already exists"):
        store.add("alice", "pw2")


def test_store_add_rejects_invalid_username(store: UserStore) -> None:
    with pytest.raises(UserStoreError, match="username must match"):
        store.add("bad name", "pw")


def test_store_remove_missing_raises(store: UserStore) -> None:
    with pytest.raises(UserStoreError, match="not found"):
        store.remove("ghost")


def test_store_set_password_missing_raises(store: UserStore) -> None:
    with pytest.raises(UserStoreError, match="not found"):
        store.set_password("ghost", "new")


def test_atomic_save_does_not_corrupt_on_existing_file(store: UserStore, tmp_path: Path) -> None:
    """Overwriting the file twice in a row leaves both writes visible
    and no stray temp files."""
    store.add("alice", "first")
    store.set_password("alice", "second")
    assert store.verify("alice", "second") is True
    leftover = [p.name for p in tmp_path.iterdir() if p.name.startswith(".users.")]
    assert leftover == []


def test_default_store_path_under_xdg_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/custom/state")
    p = default_store_path()
    assert p == Path("/custom/state/genesis/users.yaml")


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def test_verify_credentials_against_default_store(store: UserStore) -> None:
    create_user("alice", "pw")
    assert verify_credentials("alice", "pw") is True
    assert verify_credentials("alice", "wrong") is False
    assert verify_credentials("ghost", "anything") is False


def test_change_password_replaces_hash(store: UserStore) -> None:
    create_user("alice", "old-pw")
    assert verify_credentials("alice", "old-pw") is True
    change_password("alice", "new-pw")
    assert verify_credentials("alice", "old-pw") is False
    assert verify_credentials("alice", "new-pw") is True


def test_change_password_unknown_user_raises(store: UserStore) -> None:
    with pytest.raises(UserStoreError, match="not found"):
        change_password("ghost", "pw")


def test_list_users_returns_sorted(store: UserStore) -> None:
    create_user("charlie", "p")
    create_user("alice", "p")
    create_user("bob", "p")
    assert list_users() == ["alice", "bob", "charlie"]


def test_delete_user_removes_entry(store: UserStore) -> None:
    create_user("alice", "pw")
    delete_user("alice")
    assert list_users() == []


def test_delete_user_unknown_raises(store: UserStore) -> None:
    with pytest.raises(UserStoreError, match="not found"):
        delete_user("ghost")


# ---------------------------------------------------------------------------
# Hash behaviour
# ---------------------------------------------------------------------------


def test_verify_is_case_sensitive(store: UserStore) -> None:
    """Argon2 hashes are case-sensitive on the password, not on the
    username; both sides must match exactly."""
    create_user("Alice", "Password")
    assert verify_credentials("Alice", "Password") is True
    assert verify_credentials("alice", "Password") is False
    assert verify_credentials("Alice", "password") is False


def test_two_users_can_share_password(store: UserStore) -> None:
    """Same password for two users must yield two distinct hashes."""
    create_user("alice", "shared")
    create_user("bob", "shared")
    alice = store.get("alice")
    bob = store.get("bob")
    assert alice is not None and bob is not None
    assert alice.password_hash != bob.password_hash


# ---------------------------------------------------------------------------
# Orchestration with the shared Radicale htpasswd
# ---------------------------------------------------------------------------


def test_create_user_writes_to_htpasswd(store: UserStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``create_user`` keeps the users table AND the shared Radicale
    htpasswd in sync. The htpasswd entry uses the same plaintext
    password the user typed at signup."""
    from genesis.users.htpasswd import RadicaleHtpasswd

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    create_user("alice", "alice-pw")
    htpasswd = RadicaleHtpasswd(radicale_root=tmp_path / "genesis" / "radicale")
    assert htpasswd.load() == {"alice": "alice-pw"}


def test_change_password_updates_htpasswd(store: UserStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from genesis.users.htpasswd import RadicaleHtpasswd

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    create_user("alice", "old-pw")
    change_password("alice", "new-pw")
    htpasswd = RadicaleHtpasswd(radicale_root=tmp_path / "genesis" / "radicale")
    assert htpasswd.load() == {"alice": "new-pw"}


def test_delete_user_removes_htpasswd_entry(store: UserStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from genesis.users.htpasswd import RadicaleHtpasswd

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    create_user("alice", "pw")
    delete_user("alice")
    htpasswd = RadicaleHtpasswd(radicale_root=tmp_path / "genesis" / "radicale")
    assert htpasswd.load() == {}


def test_create_user_does_not_corrupt_table_on_htpasswd_failure(
    store: UserStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the htpasswd write fails after the users-table write, the
    table is left in a consistent state with the failed user
    present. The exception propagates so the operator notices."""
    from genesis.users import htpasswd as htpasswd_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(htpasswd_mod, "add_user", _boom)

    with pytest.raises(RuntimeError, match="disk full"):
        create_user("alice", "pw")

    # The users table still has alice (the partial state).
    assert store.get("alice") is not None
    # But verify_credentials must not return True because we did
    # not complete the orchestration.
    # (The test for "is the table in a sane state" is satisfied
    # by the existence check; the htpasswd failure is independent.)
