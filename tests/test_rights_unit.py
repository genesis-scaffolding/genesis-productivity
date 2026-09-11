"""Unit tests for :mod:`genesis.users.rights`."""

from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest

from genesis.users.rights import (
    rights_path_for,
    write_radicale_rights,
    write_radicale_rights_from_htpasswd,
)


@pytest.fixture
def radicale_root(tmp_path: Path) -> Path:
    return tmp_path / "radicale"


# ---------------------------------------------------------------------------
# write_radicale_rights
# ---------------------------------------------------------------------------


def test_writes_a_single_base_section(radicale_root: Path) -> None:
    """The rights file is always one ``[base]`` rule that grants
    any authenticated user access to any collection. Per-user
    rights sections were dropped because Radicale's ``from_file``
    rights backend never reloads, which would force a Radicale
    restart on every user signup/delete."""
    write_radicale_rights(radicale_root, ["alice", "bob"])
    parser = configparser.ConfigParser()
    parser.read(rights_path_for(radicale_root))
    assert parser.sections() == ["base"]


def test_base_rule_matches_any_user(radicale_root: Path) -> None:
    """``user = .+`` matches any authenticated user. The htpasswd
    is the per-user authentication gate; the rights file grants
    access after authentication succeeds."""
    write_radicale_rights(radicale_root, [])
    parser = configparser.ConfigParser()
    parser.read(rights_path_for(radicale_root))
    pattern = parser["base"]["user"]
    assert re.fullmatch(pattern, "alice") is not None
    assert re.fullmatch(pattern, "bob") is not None
    assert re.fullmatch(pattern, "anyone") is not None


def test_base_rule_matches_any_collection(radicale_root: Path) -> None:
    write_radicale_rights(radicale_root, [])
    parser = configparser.ConfigParser()
    parser.read(rights_path_for(radicale_root))
    pattern = parser["base"]["collection"]
    assert re.fullmatch(pattern, "") is not None  # root
    assert re.fullmatch(pattern, "alice") is not None
    assert re.fullmatch(pattern, "alice/calendar.ics") is not None
    assert re.fullmatch(pattern, "bob/anything/at/all") is not None


def test_permissions_grant_read_and_write(radicale_root: Path) -> None:
    write_radicale_rights(radicale_root, [])
    parser = configparser.ConfigParser()
    parser.read(rights_path_for(radicale_root))
    assert parser["base"]["permissions"] == "RrWw"


def test_username_argument_is_ignored(radicale_root: Path) -> None:
    """The signature accepts a ``usernames`` argument for backward
    compatibility with callers that still pass a list. The actual
    rights content does not depend on it."""
    write_radicale_rights(radicale_root, ["alice", "bob", "carol"])
    content = rights_path_for(radicale_root).read_text()
    assert "alice" not in content
    assert "bob" not in content
    assert "carol" not in content


def test_overwrites_existing_file(radicale_root: Path) -> None:
    """If a previous version of the rights file existed (e.g. with
    per-user sections from an earlier deployment), the new write
    replaces it. This matters when the operator switches from the
    old design to the new permissive one."""
    radicale_root.mkdir(parents=True)
    (radicale_root / "rights").write_text(
        "[user-alice]\nuser = ^alice$\ncollection = .*\npermissions = RrWw\n",
    )
    write_radicale_rights(radicale_root)
    content = (radicale_root / "rights").read_text()
    assert "[user-alice]" not in content
    assert "[base]" in content


def test_returns_written_path(radicale_root: Path) -> None:
    """The return value is the path that was written so callers
    can log it for operators."""
    written = write_radicale_rights(radicale_root)
    assert written == radicale_root / "rights"


def test_creates_parent_directories(radicale_root: Path) -> None:
    """The radicale root may not exist on a fresh install. The
    helper creates the parent on demand so callers do not have to
    pre-create the dir."""
    written = write_radicale_rights(radicale_root)
    assert written.exists()


# ---------------------------------------------------------------------------
# write_radicale_rights_from_htpasswd
# ---------------------------------------------------------------------------


def test_from_htpasswd_writes_base_section(radicale_root: Path) -> None:
    """The convenience wrapper reads the htpasswd (if present) and
    writes the same permissive rule. The htpasswd is read for
    symmetry with the legacy per-user design and to verify the
    file is reachable; the actual rule is independent of the
    htpasswd's contents."""
    radicale_root.mkdir(parents=True)
    (radicale_root / "users").write_text("alice:apw\nbob:bpw\n")
    write_radicale_rights_from_htpasswd(radicale_root)
    parser = configparser.ConfigParser()
    parser.read(radicale_root / "rights")
    assert parser.sections() == ["base"]


def test_from_htpasswd_missing_file_is_safe(radicale_root: Path) -> None:
    """If the htpasswd hasn't been written yet, the rights file
    is still created with the permissive rule. Radicale would
    reject every request (no accounts), which is the correct
    state before users exist."""
    write_radicale_rights_from_htpasswd(radicale_root)
    assert (radicale_root / "rights").exists()
    parser = configparser.ConfigParser()
    parser.read(radicale_root / "rights")
    assert parser.sections() == ["base"]
