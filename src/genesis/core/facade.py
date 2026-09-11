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

    with Facade() as facade:
        facade.caldav.create_task(...)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..configs import Configs
from ..dav.server import ServerManager

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
        ensure_server: If True (default), starts the DAV server if not running.
                       Set to False if managing server separately.
    """

    def __init__(
        self,
        yaml_path: str | Path | None = None,
        *,
        ensure_server: bool = True,
    ) -> None:
        self._configs = Configs(yaml_path=yaml_path)
        self._server: ServerManager | None = None
        self._caldav: CalDAVFacade | None = None
        self._carddav: CardDAVFacade | None = None
        self._journal: JournalFacade | None = None

        if ensure_server:
            self._ensure_server_running()

    def _ensure_server_running(self) -> None:
        """Start the shared Radicale server if not already running.

        Always makes sure the configured user exists in the Radicale
        htpasswd. ``ServerManager.start()`` only calls ``ensure_user``
        on a fresh start; on a long-running deployed server the
        start is a no-op and a newly-created account would not get
        an htpasswd entry, which causes CalDAV lookups to fail.
        ``ensure_user`` is idempotent.

        ADR-019 option A: the data path passed to ``ServerManager``
        is the SHARED ``radicale_root`` (same for every user). One
        Radicale process serves every account; per-user data is
        namespaced by the collection tree under it.
        """
        self._server = ServerManager(
            dav_config=self._configs.dav,
            data_path=self._configs.paths.radicale_root,
        )
        self._server.start()
        self._server.ensure_user(self._configs.dav.username)

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
            self._caldav = CalDAVFacade(
                server_url=cfg.dav.server_url,
                username=cfg.dav.username,
                password=cfg.dav.password,
            )
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
        """Return the Journal facade for markdown entries.

        ADR-019: journal entries live under the SHARED
        ``journal_root``, namespaced per user
        (``<journal_root>/<user>/``). All users see their own
        subtree; one shared root keeps backup and tooling simple.
        """
        if self._journal is None:
            from ..journal.facade import JournalFacade

            cfg = self.configs
            self._journal = JournalFacade(cfg.paths.journal_root / cfg.user)
        return self._journal

    def shutdown(self) -> None:
        """Stop the DAV server if we started it."""
        if self._server is not None:
            self._server.stop()
            self._server = None

    def restart_server(self) -> None:
        """Restart the Radicale server for this Facade's user.

        Used after a migration that moved the user's data dir:
        the previously-running Radicale is still bound to the
        pre-migration paths in its in-memory config, so CalDAV
        calls would 500 until the process is replaced.

        Safe to call when no server is running (start path runs as
        normal). Safe to call repeatedly.
        """
        if self._server is None:
            self._ensure_server_running()
            return
        was_running = self._server.is_running()
        if was_running:
            self._server.stop()
        self._server.start()
        self._server.ensure_user(self._configs.dav.username)
        if was_running:
            import sys as _sys

            print(
                f"Radicale restarted for user '{self._configs.dav.username}'",
                file=_sys.stderr,
            )

    def __enter__(self) -> Facade:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.shutdown()
