"""Facade for the application.

The Facade provides a single entry point to the core library.
Each instance represents an independent application context with its own
configuration. Multiple instances can coexist (e.g., multi-user scenarios).

Usage:
    facade = Facade()                          # uses default XDG path
    facade = Facade("/path/to/configs.yaml")   # explicit yaml path

    print(facade.configs.app_name)
    facade.caldav.list_tasks()
    facade.journal.get_entry(today)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..configs import Configs

if TYPE_CHECKING:
    from ..caldav.facade import CalDAVFacade
    from ..carddav.facade import CardDAVFacade
    from ..journal.facade import JournalFacade


class Facade:
    """Single entry point to the application core.

    Args:
        yaml_path: Optional path to a configs.yaml file.
                   If None, uses the default XDG path. If that default
                   file does not exist, uses internal defaults only.
                   If provided but not found, raises FileNotFoundError.
    """

    def __init__(self, yaml_path: str | Path | None = None) -> None:
        self._configs = Configs(yaml_path=yaml_path)
        self._caldav: CalDAVFacade | None = None
        self._carddav: CardDAVFacade | None = None
        self._journal: JournalFacade | None = None

    @property
    def configs(self) -> Configs:
        """Return the configuration object."""
        return self._configs

    @property
    def caldav(self) -> CalDAVFacade:
        """Return the CalDAV facade for tasks and projects."""
        if self._caldav is None:
            from ..caldav.facade import CalDAVFacade
            cfg = self.configs
            self._caldav = CalDAVFacade(cfg.paths.data / "caldav")
        return self._caldav

    @property
    def carddav(self) -> CardDAVFacade:
        """Return the CardDAV facade for contacts."""
        if self._carddav is None:
            from ..carddav.facade import CardDAVFacade
            cfg = self.configs
            self._carddav = CardDAVFacade(
                server_url=cfg.dav.server_url,
                username=cfg.dav.username,
                password=cfg.dav.password,
            )
        return self._carddav

    @property
    def journal(self) -> JournalFacade:
        """Return the Journal facade for markdown entries."""
        if self._journal is None:
            from ..journal.facade import JournalFacade
            cfg = self.configs
            self._journal = JournalFacade(cfg.paths.data / "journal")
        return self._journal
