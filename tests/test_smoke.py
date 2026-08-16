"""Smoke tests to verify the project setup."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from myproject.configs import DEFAULT_APP_NAME
from myproject.core.facade import Facade


class TestConfigs:
    """Tests for configuration loading."""

    def test_facade_defaults_without_yaml(self) -> None:
        """Facade loads internal defaults when no yaml exists."""
        facade = Facade()
        cfg = facade.configs

        assert cfg.app_name == DEFAULT_APP_NAME
        assert cfg.paths.config.name == DEFAULT_APP_NAME
        assert cfg.paths.data.name == DEFAULT_APP_NAME
        assert cfg.paths.state.name == DEFAULT_APP_NAME
        assert cfg.paths.cache.name == DEFAULT_APP_NAME

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

    def test_facade_raises_for_missing_explicit_yaml(self) -> None:
        """Facade raises FileNotFoundError when explicit yaml is missing."""
        with pytest.raises(FileNotFoundError):
            Facade("/nonexistent/path/configs.yaml")


class TestSmoke:
    """High-level smoke tests."""

    def test_imports_work(self) -> None:
        """Verify all packages can be imported."""
        from myproject.cli import app as cli_app
        from myproject.core import facade
        from myproject.ui import app as ui_app  # noqa: F401

        assert facade.Facade is not None
        assert cli_app.app is not None

    def test_tests_are_discovered(self) -> None:
        """This test ensures pytest discovers and runs tests."""
        assert True
