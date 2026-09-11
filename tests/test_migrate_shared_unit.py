"""Unit tests for the shared-layout migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis.users.migrate_shared import MigrateSharedError, migrate_shared


@pytest.fixture
def per_user_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Pin XDG_DATA_HOME under tmp_path and seed a per-user data
    tree that ``migrate_shared`` can consume. Mirrors the state
    produced by ``genesis user migrate-default alice``."""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    # Per-user radicale data path
    per_user_radicale = data / "genesis" / "alice" / "radicale"
    per_user_radicale.mkdir(parents=True)
    per_user_collection = per_user_radicale / "collections" / "collection-root" / "alice"
    per_user_collection.mkdir(parents=True)
    (per_user_collection / "calendar.ics").write_text("data")

    # Per-user htpasswd (used by Radicale before the migration)
    (per_user_radicale / "users").write_text("alice:genesis-local\n")

    # Per-user journal
    per_user_journal = data / "genesis" / "alice" / "journal"
    per_user_journal.mkdir(parents=True)
    (per_user_journal / "2026-09-08.md").write_text("# entry\n")

    # Per-user JWT signing key
    (data / "genesis" / "alice" / "api_secret.key").write_bytes(b"\x00" * 32)

    return {
        "data": data,
        "per_user_radicale": per_user_radicale,
        "per_user_collection": per_user_collection,
        "per_user_journal": per_user_journal,
        "per_user_secret": data / "genesis" / "alice" / "api_secret.key",
        "shared_root": data / "genesis",
    }


def test_moves_journal_to_shared_root(per_user_layout: dict[str, Path]) -> None:
    migrate_shared("alice")
    new_journal = per_user_layout["shared_root"] / "journals" / "alice"
    assert (new_journal / "2026-09-08.md").read_text() == "# entry\n"
    assert not per_user_layout["per_user_journal"].exists()


def test_moves_collection_root_to_shared_radicale(
    per_user_layout: dict[str, Path],
) -> None:
    migrate_shared("alice")
    new_collection = per_user_layout["shared_root"] / "radicale" / "collections" / "collection-root" / "alice"
    assert (new_collection / "calendar.ics").read_text() == "data"
    assert not per_user_layout["per_user_collection"].exists()


def test_appends_per_user_htpasswd_to_shared(per_user_layout: dict[str, Path]) -> None:
    migrate_shared("alice")
    shared_htpasswd = per_user_layout["shared_root"] / "radicale" / "users"
    assert shared_htpasswd.exists()
    content = shared_htpasswd.read_text()
    assert "alice:genesis-local" in content


def test_moves_api_secret_to_shared_path(per_user_layout: dict[str, Path]) -> None:
    migrate_shared("alice")
    shared_secret = per_user_layout["shared_root"] / "api_secret.key"
    assert shared_secret.exists()
    assert shared_secret.read_bytes() == b"\x00" * 32
    assert not per_user_layout["per_user_secret"].exists()


def test_api_secret_mode_is_0o600(per_user_layout: dict[str, Path]) -> None:
    migrate_shared("alice")
    shared_secret = per_user_layout["shared_root"] / "api_secret.key"
    mode = shared_secret.stat().st_mode & 0o777
    assert mode == 0o600


def test_regenerates_rights_file(per_user_layout: dict[str, Path]) -> None:
    """The shared rights file is regenerated so an operator who
    inspects the Radicale config sees a complete setup. The
    permissive ``[base]`` rule means the content does not depend
    on which users exist."""
    migrate_shared("alice")
    rights_path = per_user_layout["shared_root"] / "radicale" / "rights"
    assert rights_path.exists()
    assert "[base]" in rights_path.read_text()


def test_removes_empty_per_user_radicale_dir(
    per_user_layout: dict[str, Path],
) -> None:
    """The per-user radicale dir is removed iff it's empty after
    the move. The fixture's per-user layout leaves only the moved
    subdirs behind (collections/, users file is moved), so the
    parent should be empty and removed."""
    migrate_shared("alice")
    assert not per_user_layout["per_user_radicale"].exists()


def test_does_not_remove_per_user_radicale_when_non_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the per-user radicale dir still has unrelated files
    after the move (e.g., logs), the migration leaves it alone."""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    per_user_radicale = data / "genesis" / "alice" / "radicale"
    per_user_radicale.mkdir(parents=True)
    (per_user_radicale / "log.txt").write_text("debug log")

    notes = migrate_shared("alice")
    # Directory still present with the unrelated log file
    assert per_user_radicale.exists()
    assert (per_user_radicale / "log.txt").exists()
    assert any("removed empty dir" not in n for n in notes)


def test_refuses_when_no_per_user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    # No per-user state at all
    with pytest.raises(MigrateSharedError, match="no per-user data"):
        migrate_shared("alice")


def test_refuses_when_target_already_has_collection(
    per_user_layout: dict[str, Path],
) -> None:
    # Pre-create the target collection root
    target = per_user_layout["shared_root"] / "radicale" / "collections" / "collection-root" / "alice"
    target.mkdir(parents=True)
    with pytest.raises(MigrateSharedError, match="already has collection-root"):
        migrate_shared("alice")


def test_refuses_when_target_already_has_journal(
    per_user_layout: dict[str, Path],
) -> None:
    target = per_user_layout["shared_root"] / "journals" / "alice"
    target.mkdir(parents=True)
    with pytest.raises(MigrateSharedError, match="already has journal dir"):
        migrate_shared("alice")


def test_does_not_overwrite_existing_shared_secret(
    per_user_layout: dict[str, Path],
) -> None:
    """A second user migrating into a shared layout must NOT
    overwrite the existing api_secret.key (which the first user
    moved). Their per-user secret is left in place on disk."""
    shared_secret = per_user_layout["shared_root"] / "api_secret.key"
    shared_secret.parent.mkdir(parents=True, exist_ok=True)
    shared_secret.write_bytes(b"existing")

    notes = migrate_shared("alice")
    # Original secret preserved
    assert shared_secret.read_bytes() == b"existing"
    # Per-user secret still present (left behind)
    assert per_user_layout["per_user_secret"].exists()
    assert any("already exists" in n for n in notes)


def test_appends_only_new_users_to_existing_htpasswd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If two users migrate and the shared htpasswd already has
    the first user, the second user's htpasswd is appended
    without duplicating the first."""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    shared = data / "genesis"
    shared.mkdir(parents=True)

    # Pre-seed shared htpasswd with alice (from an earlier migration)
    (shared / "radicale").mkdir(parents=True)
    (shared / "radicale" / "users").write_text("alice:genesis-local\n")

    # Now migrate bob's per-user state
    per_user_radicale = data / "genesis" / "bob" / "radicale"
    per_user_radicale.mkdir(parents=True)
    (per_user_radicale / "users").write_text("bob:genesis-local\n")
    (per_user_radicale / "collections" / "collection-root" / "bob").mkdir(parents=True)
    (data / "genesis" / "bob" / "journal").mkdir(parents=True)

    migrate_shared("bob")
    shared_htpasswd = shared / "radicale" / "users"
    lines = shared_htpasswd.read_text().splitlines()
    assert lines.count("alice:genesis-local") == 1
    assert lines.count("bob:genesis-local") == 1


def test_returns_human_readable_notes(
    per_user_layout: dict[str, Path],
) -> None:
    notes = migrate_shared("alice")
    assert any("moved journal" in n for n in notes)
    assert any("moved collection-root" in n for n in notes)
    assert any("regenerated rights file" in n for n in notes)
    assert any("complete" in n.lower() for n in notes)
