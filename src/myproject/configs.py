"""Configuration management for the project.

This module handles loading configuration from multiple sources:
  1. Defaults (lowest priority)
  2. YAML config file (configs.yaml)
  3. Environment variables (highest priority)

Environment variables use double-underscore separator with an app-prefix:
  e.g. MYPROJECT__APP_NAME, MYPROJECT__PATHS__STATE

The XDG base directories are used for standard paths, with <app_name> as subdirectory.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Module-level defaults (used before any config is loaded)
# -----------------------------------------------------------------------------

DEFAULT_APP_NAME = "myproject"

DEFAULT_CONFIG_HOME = Path.home() / ".config"
DEFAULT_DATA_HOME = Path.home() / ".local" / "share"
DEFAULT_STATE_HOME = Path.home() / ".local" / "state"
DEFAULT_CACHE_HOME = Path.home() / ".cache"


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------


class Paths(BaseModel):
    """XDG-based paths for the application.

    All paths are resolved relative to <xdg_base>/<app_name>/.
    XDG_BASE defaults can be overridden via env vars:
      - XDG_CONFIG_HOME
      - XDG_DATA_HOME
      - XDG_STATE_HOME
      - XDG_CACHE_HOME
    """

    config: Path = Field(description="Directory for config files.")
    data: Path = Field(description="Directory for persistent data.")
    state: Path = Field(description="Directory for logs and runtime state.")
    cache: Path = Field(description="Directory for cache files.")


class Configs(BaseModel):
    """Root configuration object.

    Fields can be overridden via:
      - YAML file (configs.yaml)
      - Environment variables (MYPROJECT__<FIELD>)
    """

    app_name: str = Field(default=DEFAULT_APP_NAME, description="Application name.")
    paths: Paths = Field(description="XDG-based directory paths.")

    model_config = {
        "extra": "ignore",  # Ignore unknown fields in YAML
    }


# -----------------------------------------------------------------------------
# Config loader
# -----------------------------------------------------------------------------


class ConfigsLoader:
    """Loads and merges configuration from defaults, YAML, and env vars.

    Args:
        yaml_path: Optional path to a configs.yaml file.
                   If None, uses the default XDG path:
                   $XDG_CONFIG_HOME/<app_name>/configs.yaml
                   If the default file does not exist, returns defaults only.
                   If yaml_path is explicitly provided but doesn't exist,
                   raises FileNotFoundError.
    """

    def __init__(self, yaml_path: str | Path | None = None) -> None:
        if yaml_path is None:
            config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(DEFAULT_CONFIG_HOME)))
            app_name = os.environ.get(f"{DEFAULT_APP_NAME.upper()}__APP_NAME", DEFAULT_APP_NAME)
            self._yaml_path: Path | None = config_home / app_name / "configs.yaml"
        else:
            self._yaml_path = Path(yaml_path).resolve()
            if not self._yaml_path.exists():
                raise FileNotFoundError(f"configs.yaml not found at: {self._yaml_path}")

    def load(self) -> Configs:
        """Load configuration, merging defaults, YAML, and env vars."""
        raw: dict = {}

        # 1. Load from YAML if it exists
        if self._yaml_path is not None and self._yaml_path.exists():
            with open(self._yaml_path) as f:
                raw = yaml.safe_load(f) or {}

        # 2. Build paths from environment or defaults
        app_name = self._resolve_field(raw, "app_name", DEFAULT_APP_NAME)
        raw["app_name"] = app_name
        raw["paths"] = self._build_paths(raw, app_name)

        # 3. Pydantic handles env var overrides automatically via env var names
        return Configs(**raw)

    def _resolve_field(self, raw: dict, field: str, default: str) -> str:
        """Resolve a top-level string field from YAML or env var."""
        env_key = f"{DEFAULT_APP_NAME.upper()}__{field.upper()}"
        return os.environ.get(env_key) or raw.get(field, default)

    def _build_paths(self, raw: dict, app_name: str) -> dict:
        """Build the paths dict, respecting env overrides for XDG bases."""
        config_home = os.environ.get("XDG_CONFIG_HOME", str(DEFAULT_CONFIG_HOME))
        data_home = os.environ.get("XDG_DATA_HOME", str(DEFAULT_DATA_HOME))
        state_home = os.environ.get("XDG_STATE_HOME", str(DEFAULT_STATE_HOME))
        cache_home = os.environ.get("XDG_CACHE_HOME", str(DEFAULT_CACHE_HOME))

        # Allow YAML to override the base paths
        yaml_paths = raw.get("paths", {})
        config_base = yaml_paths.get("config", f"{config_home}/{app_name}")
        data_base = yaml_paths.get("data", f"{data_home}/{app_name}")
        state_base = yaml_paths.get("state", f"{state_home}/{app_name}")
        cache_base = yaml_paths.get("cache", f"{cache_home}/{app_name}")

        return {
            "config": config_base,
            "data": data_base,
            "state": state_base,
            "cache": cache_base,
        }
