"""Unit tests for the ``migrate-default`` command."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis.users.migrate import MigrateError, migrate_default
from genesis.users.store import UserStore


@pytest.fixture
def xdg_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Pin all four XDG bases under a temp tree and seed a
    ``default``-user data + config + users file."""
    config = tmp_path / "config"
    data = tmp_path / "data"
    state = tmp_path / "state"
    cache = tmp_path / "cache"
    for d in (config, data, state, cache):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))

    # Seed a default-user config and data dir with a Radicale
    # collection root.
    (config / "genesis" / "default").mkdir(parents=True)
    (config / "genesis" / "default" / "configs.yaml").write_text("user: default\n")
    radic = data / "genesis" / "default" / "radicale" / "collections" / "collection-root" / "default"
    radic.mkdir(parents=True)
    (radic / "stuff.ics").write_text("data")
    (data / "genesis" / "default" / "api_secret.key").write_bytes(b"\x00" * 32)
    (state / "genesis" / "default").mkdir(parents=True)
    (state / "genesis" / "default" / "cache.sqlite").write_text("blob")
    users_file = state / "genesis" / "users.yaml"
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text("- username: default\n  password_hash: x\n  created_at: 2025-01-01T00:00:00Z\n")

    return {
        "config": config,
        "data": data,
        "state": state,
        "cache": cache,
        "root": tmp_path,
    }


def test_migrate_default_moves_all_bases(xdg_roots: dict[str, Path]) -> None:
    notes = migrate_default("alice")
    assert any("moved config" in n for n in notes)
    assert any("moved data" in n for n in notes)
    assert any("moved state" in n for n in notes)
    assert (xdg_roots["config"] / "genesis" / "alice" / "configs.yaml").exists()
    assert (xdg_roots["config"] / "genesis" / "default").exists() is False
    assert (xdg_roots["data"] / "genesis" / "alice" / "radicale" / "collections" / "collection-root" / "alice").exists()
    assert (
        xdg_roots["data"] / "genesis" / "alice" / "radicale" / "collections" / "collection-root" / "default"
    ).exists() is False
    assert (xdg_roots["data"] / "genesis" / "alice" / "api_secret.key").exists()


def test_migrate_default_drops_old_user_from_users_file(xdg_roots: dict[str, Path]) -> None:
    migrate_default("alice")
    store = UserStore(xdg_roots["state"] / "genesis" / "users.yaml")
    assert store.get("default") is None


def test_migrate_default_refuses_target_user_exists_in_users_file(
    xdg_roots: dict[str, Path],
) -> None:
    store = UserStore(xdg_roots["state"] / "genesis" / "users.yaml")
    store.add("alice", "x")
    with pytest.raises(MigrateError, match="already exists in users file"):
        migrate_default("alice")


def test_migrate_default_refuses_target_has_data_dir(xdg_roots: dict[str, Path]) -> None:
    (xdg_roots["config"] / "genesis" / "alice").mkdir(parents=True)
    with pytest.raises(MigrateError, match="already has a"):
        migrate_default("alice")


def test_migrate_default_refuses_same_username(xdg_roots: dict[str, Path]) -> None:
    with pytest.raises(MigrateError, match="must differ"):
        migrate_default("default")


def test_migrate_default_refuses_when_nothing_to_migrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for d in (tmp_path / "config", tmp_path / "data", tmp_path / "state", tmp_path / "cache"):
        d.mkdir(parents=True, exist_ok=True)
    with pytest.raises(MigrateError, match="no 'default' user data"):
        migrate_default("alice")


def test_migrate_default_preserves_journal_markdown(xdg_roots: dict[str, Path]) -> None:
    """A user's journal entry on disk survives the move."""
    journal = xdg_roots["data"] / "genesis" / "default" / "journal" / "2026-09-08.md"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("# Hello\n")
    migrate_default("alice")
    new_journal = xdg_roots["data"] / "genesis" / "alice" / "journal" / "2026-09-08.md"
    assert new_journal.exists()
    assert new_journal.read_text() == "# Hello\n"


def test_migrate_default_rewrites_htpasswd(xdg_roots: dict[str, Path]) -> None:
    """Radicale's htpasswd ``default:`` line is renamed to ``<new>:``
    so CalDAV auth works without a Facade round-trip."""
    htpasswd = xdg_roots["data"] / "genesis" / "default" / "radicale" / "users"
    htpasswd.parent.mkdir(parents=True, exist_ok=True)
    htpasswd.write_text("default:genesis-local\n")
    migrate_default("alice")
    new_htpasswd = xdg_roots["data"] / "genesis" / "alice" / "radicale" / "users"
    assert new_htpasswd.read_text() == "alice:genesis-local\n"


def test_migrate_default_htpasswd_preserves_other_users(xdg_roots: dict[str, Path]) -> None:
    """Lines for other users are kept as-is during htpasswd rewrite."""
    htpasswd = xdg_roots["data"] / "genesis" / "default" / "radicale" / "users"
    htpasswd.parent.mkdir(parents=True, exist_ok=True)
    htpasswd.write_text("default:genesis-local\nbob:other-pw\n")
    migrate_default("alice")
    new_htpasswd = xdg_roots["data"] / "genesis" / "alice" / "radicale" / "users"
    lines = new_htpasswd.read_text().splitlines()
    assert "alice:genesis-local" in lines
    assert "bob:other-pw" in lines
    assert "default:genesis-local" not in lines
