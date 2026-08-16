"""Facade for the application.

The Facade provides a single entry point to the core library.
Each instance represents an independent application context with its own
configuration. Multiple instances can coexist (e.g., multi-user scenarios).

Usage:
    facade = Facade()                          # uses default XDG path
    facade = Facade("/path/to/configs.yaml")   # explicit yaml path

    print(facade.configs.app_name)
"""

from __future__ import annotations

from pathlib import Path

from ..configs import Configs, ConfigsLoader


class Facade:
    """Single entry point to the application core.

    Args:
        yaml_path: Optional path to a configs.yaml file.
                   If None, uses the default XDG path. If that default
                   file does not exist, uses internal defaults only.
                   If provided but not found, raises FileNotFoundError.
    """

    def __init__(self, yaml_path: str | Path | None = None) -> None:
        loader = ConfigsLoader(yaml_path)
        self._configs: Configs | None = None
        self._loader = loader

    @property
    def configs(self) -> Configs:
        """Return the resolved configuration object.

        The config is loaded and cached on first access.
        """
        if self._configs is None:
            self._configs = self._loader.load()
        return self._configs
