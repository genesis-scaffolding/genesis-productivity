"""Configuration management for the project.

Loads from YAML file, then applies environment variable overrides.
User is extracted from the config file path structure.

Priority (lowest to highest):
  1. YAML file
  2. Environment variables (e.g., GENESIS__DAV__PASSWORD)
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# -----------------------------------------------------------------------------
# Module-level defaults
# -----------------------------------------------------------------------------

DEFAULT_APP_NAME = "genesis"
DEFAULT_USER = "default"

DEFAULT_DAV_HOST = "127.0.0.1"
DEFAULT_DAV_PORT = 5232
DEFAULT_DAV_USERNAME = "genesis"
DEFAULT_DAV_PASSWORD = "genesis-local"

DEFAULT_CONFIG_HOME = Path.home() / ".config"
DEFAULT_DATA_HOME = Path.home() / ".local" / "share"
DEFAULT_STATE_HOME = Path.home() / ".local" / "state"
DEFAULT_CACHE_HOME = Path.home() / ".cache"


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------


class Paths(BaseModel):
    """XDG-based paths for the application.

    All paths are resolved relative to <xdg_base>/<app_name>/<user>/.
    """

    config: Path = Field(description="Directory for config files.")
    data: Path = Field(description="Directory for persistent data.")
    state: Path = Field(description="Directory for logs and runtime state.")
    cache: Path = Field(description="Directory for cache files.")


class DAV(BaseModel):
    """CalDAV and CardDAV connection settings."""

    host: str = Field(default=DEFAULT_DAV_HOST, description="DAV server host.")
    port: int = Field(default=DEFAULT_DAV_PORT, description="DAV server port.")
    username: str = Field(default="", description="Username for DAV authentication.")
    password: str = Field(default="", description="Password for DAV authentication.")
    task_calendar: str = Field(
        default="tasks",
        description="Name of the calendar used for tasks.",
    )
    project_calendar_prefix: str = Field(
        default="project:",
        description="Prefix for project calendars.",
    )

    @property
    def server_url(self) -> str:
        """Construct server URL from host and port."""
        return f"http://{self.host}:{self.port}"


class Configs(BaseSettings):
    """Root configuration object.

    Loads from YAML file (if exists), then applies environment variable overrides.
    User is derived from the config file path.

    Environment variables use double-underscore separator:
      e.g. GENESIS__DAV__SERVER_URL, GENESIS__PATHS__DATA
    """

    app_name: str = Field(default=DEFAULT_APP_NAME)
    user: str = Field(default=DEFAULT_USER)
    paths: Paths = Field(description="XDG-based directory paths.")
    dav: DAV = Field(description="CalDAV and CardDAV settings.")

    model_config = {
        "extra": "ignore",
    }

    def __init__(
        self,
        yaml_path: str | Path | None = None,
        **kwargs,
    ) -> None:
        # Determine yaml_path
        if yaml_path is None:
            config_home = os.environ.get(
                "XDG_CONFIG_HOME", str(DEFAULT_CONFIG_HOME)
            )
            yaml_path = Path(config_home) / DEFAULT_APP_NAME / "configs.yaml"
        else:
            yaml_path = Path(yaml_path).resolve()

        # Extract user from path
        user = _extract_user(yaml_path)

        # Load YAML if exists
        yaml_data: dict = {}
        if yaml_path.exists():
            with open(yaml_path) as f:
                yaml_data = yaml.safe_load(f) or {}

        # Build paths scoped to genesis/<user>/
        yaml_data["paths"] = _build_paths(yaml_data, user)

        # Build dav config
        yaml_data["dav"] = _build_dav(yaml_data)

        # Merge: yaml_data < env_vars < explicit kwargs
        merged = {**yaml_data, **kwargs}
        merged["user"] = user

        super().__init__(**merged)


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------


def _extract_user(yaml_path: Path | None) -> str:
    """Extract user from yaml_path directory structure.

    Expected: ~/.config/genesis/<user>/configs.yaml
    Falls back to DEFAULT_USER if path doesn't match expected pattern.
    """
    if yaml_path is None:
        return DEFAULT_USER

    parts = yaml_path.parts
    try:
        genesis_idx = parts.index(DEFAULT_APP_NAME)
        if len(parts) >= genesis_idx + 2:
            user = parts[genesis_idx + 1]
            # Reject if it looks like a filename (no user subdirectory)
            if user != "configs.yaml":
                return user
    except ValueError:
        pass

    return DEFAULT_USER


def _build_paths(raw: dict, user: str) -> dict:
    """Build the paths dict, scoping all paths to genesis/<user>/."""
    config_home = os.environ.get("XDG_CONFIG_HOME", str(DEFAULT_CONFIG_HOME))
    data_home = os.environ.get("XDG_DATA_HOME", str(DEFAULT_DATA_HOME))
    state_home = os.environ.get("XDG_STATE_HOME", str(DEFAULT_STATE_HOME))
    cache_home = os.environ.get("XDG_CACHE_HOME", str(DEFAULT_CACHE_HOME))

    user_prefix = f"{DEFAULT_APP_NAME}/{user}"

    yaml_paths = raw.get("paths", {})
    config_base = yaml_paths.get("config") or f"{config_home}/{user_prefix}"
    data_base = yaml_paths.get("data") or f"{data_home}/{user_prefix}"
    state_base = yaml_paths.get("state") or f"{state_home}/{user_prefix}"
    cache_base = yaml_paths.get("cache") or f"{cache_home}/{user_prefix}"

    return {
        "config": config_base,
        "data": data_base,
        "state": state_base,
        "cache": cache_base,
    }


def _build_dav(raw: dict) -> dict:
    """Build the DAV config dict from YAML or defaults."""
    yaml_dav = raw.get("dav", {})
    return {
        "host": os.environ.get("GENESIS__DAV__HOST")
        or yaml_dav.get("host", DEFAULT_DAV_HOST),
        "port": int(
            os.environ.get("GENESIS__DAV__PORT")
            or yaml_dav.get("port", DEFAULT_DAV_PORT)
        ),
        "username": os.environ.get("GENESIS__DAV__USERNAME")
        or yaml_dav.get("username", DEFAULT_DAV_USERNAME),
        "password": os.environ.get("GENESIS__DAV__PASSWORD")
        or yaml_dav.get("password", DEFAULT_DAV_PASSWORD),
        "task_calendar": yaml_dav.get("task_calendar", "tasks"),
        "project_calendar_prefix": yaml_dav.get("project_calendar_prefix", "project:"),
    }
