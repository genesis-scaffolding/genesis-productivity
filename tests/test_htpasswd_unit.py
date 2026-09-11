"""Unit tests for :mod:`genesis.users.htpasswd`."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from genesis.users.htpasswd import (
    RadicaleHtpasswd,
    default_htpasswd,
    default_radicale_root,
    htpasswd_path_for,
)


@pytest.fixture
def radicale_root(tmp_path: Path) -> Path:
    """Return an isolated Radicale root for the test."""
    return tmp_path / "radicale"


# ---------------------------------------------------------------------------
# RadicaleHtpasswd class
# ---------------------------------------------------------------------------


def test_add_creates_file_with_0o600(radicale_root: Path) -> None:
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.add("alice", "alice-pw")
    mode = stat.S_IMODE(os.stat(htpasswd.path).st_mode)
    assert mode == 0o600


def test_add_appends_user(radicale_root: Path) -> None:
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.add("alice", "alice-pw")
    htpasswd.add("bob", "bob-pw")
    users = htpasswd.load()
    assert users == {"alice": "alice-pw", "bob": "bob-pw"}


def test_add_overwrites_existing_user(radicale_root: Path) -> None:
    """``add`` is also the change-password entry point: the latest
    value wins on a second call with the same username."""
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.add("alice", "first")
    htpasswd.add("alice", "second")
    assert htpasswd.load() == {"alice": "second"}


def test_load_returns_empty_when_file_missing(radicale_root: Path) -> None:
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    assert htpasswd.load() == {}


def test_load_skips_blank_and_malformed_lines(radicale_root: Path) -> None:
    """Blank lines and lines without a colon are silently skipped
    (Radicale itself treats such lines as comments)."""
    radicale_root.mkdir(parents=True)
    (radicale_root / "users").write_text(
        "alice:alice-pw\n\nbob:bob-pw\nnot-a-line\ncharlie:cp\n",
    )
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    assert htpasswd.load() == {"alice": "alice-pw", "bob": "bob-pw", "charlie": "cp"}


def test_load_dedupes_keeping_first_occurrence(radicale_root: Path) -> None:
    """Duplicate usernames in the file keep the first line; matches
    the on-disk ordering rather than the latest value."""
    radicale_root.mkdir(parents=True)
    (radicale_root / "users").write_text("alice:first\nalice:second\n")
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    assert htpasswd.load() == {"alice": "first"}


def test_remove_returns_true_when_present(radicale_root: Path) -> None:
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.add("alice", "pw")
    assert htpasswd.remove("alice") is True
    assert htpasswd.load() == {}


def test_remove_returns_false_when_absent(radicale_root: Path) -> None:
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    assert htpasswd.remove("ghost") is False


def test_change_password_replaces_value(radicale_root: Path) -> None:
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.add("alice", "old")
    htpasswd.change_password("alice", "new")
    assert htpasswd.load() == {"alice": "new"}


def test_change_password_is_noop_for_unknown_user(radicale_root: Path) -> None:
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.change_password("ghost", "anything")
    assert htpasswd.load() == {}


def test_atomic_save_leaves_no_temp_files(radicale_root: Path) -> None:
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.add("alice", "pw")
    htpasswd.change_password("alice", "new")
    leftover = [p.name for p in radicale_root.iterdir() if p.name.startswith(".htpasswd.")]
    assert leftover == []


def test_password_with_colon_is_preserved(radicale_root: Path) -> None:
    """A password containing ``:`` is valid: ``split(":", 1)``
    preserves everything after the first colon."""
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.add("alice", "pass:with:colons")
    assert htpasswd.load() == {"alice": "pass:with:colons"}


def test_password_can_be_empty(radicale_root: Path) -> None:
    """An empty password is legal; ``RadicaleHtpasswd`` must accept
    it without crashing."""
    htpasswd = RadicaleHtpasswd(radicale_root=radicale_root)
    htpasswd.add("alice", "")
    assert htpasswd.load() == {"alice": ""}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_htpasswd_path_for_inside_radicale_root(radicale_root: Path) -> None:
    assert htpasswd_path_for(radicale_root) == radicale_root / "users"


def test_default_radicale_root_uses_xdg_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "custom"))
    root = default_radicale_root()
    assert root == tmp_path / "custom" / "genesis" / "radicale"


def test_default_radicale_root_falls_back_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = default_radicale_root()
    assert root == tmp_path / ".local" / "share" / "genesis" / "radicale"


def test_default_htpasswd_resolves_to_radicale_users(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    htpasswd = default_htpasswd()
    assert htpasswd.path == tmp_path / "genesis" / "radicale" / "users"


def test_init_requires_path_or_radicale_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="path="):
        RadicaleHtpasswd()


def test_init_prefers_explicit_path(tmp_path: Path, radicale_root: Path) -> None:
    """If both are given, the explicit ``path`` wins. Used by tests
    that want to point at a file outside the standard location."""
    custom = tmp_path / "custom_users"
    htpasswd = RadicaleHtpasswd(path=custom, radicale_root=radicale_root)
    assert htpasswd.path == custom
