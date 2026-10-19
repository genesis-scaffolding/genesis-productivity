"""Regression tests for ``configs._extract_user``.

The extractor must find the user under the *innermost* ``genesis``
directory. If an ancestor path component is also named after the app
(e.g. the Docker image user ``genesis`` with home ``/home/genesis``),
a first-occurrence match returns the wrong segment — ``.config`` in
that case — which breaks every CalDAV call because Radicale's
multifilesystem storage refuses to create a principal for a
dot-prefixed name.
"""

from __future__ import annotations

from pathlib import Path

from genesis.configs import DEFAULT_USER, _extract_user, resolve_config_path


def test_extract_user_none_returns_default() -> None:
    assert _extract_user(None) == DEFAULT_USER


def test_extract_user_normal_host_path() -> None:
    p = Path("/home/gentran/.config/genesis/gentran1991/configs.yaml")
    assert _extract_user(p) == "gentran1991"


def test_extract_user_default_user() -> None:
    p = Path("/home/gentran/.config/genesis/default/configs.yaml")
    assert _extract_user(p) == "default"


def test_extract_user_app_named_home_dir() -> None:
    """Regression: HOME=/home/genesis (Docker image user) must not shadow
    the app directory."""
    p = Path("/home/genesis/.config/genesis/gentran1991/configs.yaml")
    assert _extract_user(p) == "gentran1991"


def test_extract_user_app_named_home_dir_default_user() -> None:
    p = Path("/home/genesis/.config/genesis/default/configs.yaml")
    assert _extract_user(p) == "default"


def test_extract_user_legacy_flat_path() -> None:
    """Legacy flat path (~/.config/genesis/configs.yaml) keeps working."""
    p = Path("/home/gentran/.config/genesis/configs.yaml")
    assert _extract_user(p) == DEFAULT_USER


def test_extract_user_no_genesis_segment() -> None:
    p = Path("/home/gentran/.config/otherapp/gentran1991/configs.yaml")
    assert _extract_user(p) == DEFAULT_USER


def test_resolve_then_extract_round_trip_app_named_home(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: resolve_config_path under a genesis-named config home
    must round-trip through _extract_user."""
    # Simulate the Docker image user: a "genesis"-named home directory
    # above the XDG config home.
    fake_home = tmp_path / "home" / "genesis" / ".config"
    fake_home.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home))
    for user in ("gentran1991", "default", "exptest"):
        p = resolve_config_path(user)
        assert _extract_user(p) == user
