"""Smoke tests to verify the project setup."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from genesis.configs import DEFAULT_APP_NAME, DEFAULT_USER
from genesis.core.facade import Facade


class TestConfigs:
    """Tests for configuration loading."""

    def test_facade_defaults_without_yaml(self) -> None:
        """Facade loads internal defaults when no yaml exists."""
        facade = Facade()
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

            facade = Facade(yaml_path)
            cfg = facade.configs

            assert cfg.app_name == "testapp"
            assert cfg.paths.data == Path("/custom/data")

    def test_facade_loads_defaults_for_nonexistent_explicit_yaml(self) -> None:
        """Facade loads defaults when explicit yaml path doesn't exist."""
        facade = Facade("/nonexistent/path/configs.yaml")
        cfg = facade.configs

        assert cfg.user == "default"
        assert DEFAULT_APP_NAME in str(cfg.paths.config)


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
