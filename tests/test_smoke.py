"""Smoke tests to verify the project setup."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from genesis.configs import CONFIG_PATH_ENV, DEFAULT_APP_NAME, DEFAULT_USER, resolve_config_path
from genesis.core.facade import Facade


class TestConfigs:
    """Tests for configuration loading."""

    def test_facade_defaults_without_yaml(self) -> None:
        """Facade loads internal defaults when no yaml exists."""
        facade = Facade(ensure_server=False)
        cfg = facade.configs

        assert cfg.app_name == DEFAULT_APP_NAME
        assert cfg.user == DEFAULT_USER
        # All paths should be scoped to genesis/<user>/
        assert DEFAULT_APP_NAME in str(cfg.paths.config)
        assert DEFAULT_USER in str(cfg.paths.config)
        assert DEFAULT_APP_NAME in str(cfg.paths.data)
        assert DEFAULT_USER in str(cfg.paths.data)
        assert DEFAULT_APP_NAME in str(cfg.paths.state)
        assert DEFAULT_USER in str(cfg.paths.state)
        assert DEFAULT_APP_NAME in str(cfg.paths.cache)
        assert DEFAULT_USER in str(cfg.paths.cache)

    def test_facade_loads_from_yaml(self) -> None:
        """Facade loads and merges config from yaml file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "configs.yaml"
            yaml_path.write_text(
                yaml.safe_dump(
                    {
                        "app_name": "testapp",
                        "paths": {
                            "data": "/custom/data",
                        },
                    }
                )
            )

            # ``ensure_server=False`` because this test only checks
            # config loading. The full server lifecycle (Radicale
            # start, htpasswd lazy-create) is covered elsewhere.
            facade = Facade(yaml_path, ensure_server=False)
            cfg = facade.configs

            assert cfg.app_name == "testapp"
            assert cfg.paths.data == Path("/custom/data")

    def test_facade_loads_defaults_for_nonexistent_explicit_yaml(self) -> None:
        """Facade loads defaults when explicit yaml path doesn't exist."""
        facade = Facade("/nonexistent/path/configs.yaml", ensure_server=False)
        cfg = facade.configs

        assert cfg.user == "default"
        assert DEFAULT_APP_NAME in str(cfg.paths.config)


class TestResolveConfigPath:
    """Tests for the shared config path resolver."""

    def test_default_user_path_is_scoped_under_genesis_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Default user resolves to <xdg_config_home>/genesis/default/configs.yaml."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        path = resolve_config_path()

        assert path == tmp_path / "genesis" / "default" / "configs.yaml"
        assert path.exists()
        assert path.read_text() == ""

    def test_default_user_seeds_empty_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Default user gets an empty configs.yaml created if missing."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        path = resolve_config_path()

        assert path.exists()
        assert path.read_text() == ""

    def test_existing_default_user_config_is_not_overwritten(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If default user config already exists, do not touch it."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        target = tmp_path / "genesis" / "default" / "configs.yaml"
        target.parent.mkdir(parents=True)
        target.write_text("app_name: keepme")

        resolved = resolve_config_path()

        assert resolved == target
        assert resolved.read_text() == "app_name: keepme"

    def test_named_user_creates_dir_only(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Named users get the parent dir but no configs.yaml file."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        path = resolve_config_path("alice")

        assert path == tmp_path / "genesis" / "alice" / "configs.yaml"
        assert path.parent.exists()
        assert not path.exists()

    def test_none_and_empty_user_resolve_to_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """None and empty user names both resolve to the default user."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        default_path = resolve_config_path()
        assert resolve_config_path(None) == default_path
        assert resolve_config_path("") == default_path

    def test_config_path_env_constant(self) -> None:
        """CLI and UI share this constant so they cannot drift."""
        assert CONFIG_PATH_ENV == "GENESIS__CONFIG_PATH"


class TestSmoke:
    """High-level smoke tests."""

    def test_imports_work(self) -> None:
        """Verify all packages can be imported."""
        from genesis.cli import app as cli_app
        from genesis.core import facade
        from genesis.ui import app as ui_app  # noqa: F401

        assert facade.Facade is not None
        assert cli_app.app is not None

    def test_tests_are_discovered(self) -> None:
        """This test ensures pytest discovers and runs tests."""
        assert True
