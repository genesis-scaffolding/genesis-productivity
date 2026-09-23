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

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..configs import Configs
from ..dav.server import ServerManager

if TYPE_CHECKING:
    from ..backup.facade import BackupFacade
    from ..caldav.facade import CalDAVFacade
    from ..carddav.facade import CardDAVFacade
    from ..game.facade import GameClassificationError, GameDisabled, GameFacade
    from ..journal.facade import JournalFacade
    from ..profile.facade import ProfileFacade
from ..game.facade import GameClassificationError, GameDisabled

logger = logging.getLogger(__name__)


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
        self._backup: BackupFacade | None = None
        self._profile: ProfileFacade | None = None
        self._game: GameFacade | None = None

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

    @property
    def profile(self) -> ProfileFacade:
        """Return the Profile facade for profile and life-story storage.

        ADR-025: per-user profile data lives under
        ``<data_home>/genesis/<user>/profile``. The facade is
        constructed lazily so callers that never touch profile
        data don't pay the directory-mkdir cost.
        """
        if self._profile is None:
            from ..profile.facade import ProfileFacade

            self._profile = ProfileFacade(self._configs.paths.profile_root)
        return self._profile

    @property
    def backup(self) -> BackupFacade:
        """Return the Backup facade for export and restore.

        See ADR-024. The facade reuses this ``Facade``'s configs,
        sub-facades, and ``restart_server`` so a destructive restore
        can invalidate Radicale's in-memory state.
        """
        if self._backup is None:
            from ..backup.facade import BackupFacade

            self._backup = BackupFacade(self)
        return self._backup

    @property
    def game(self) -> GameFacade:
        """Return the Game facade for life-gamification.

        See ADR-026. Lazy-initialized on first access so callers
        who never touch the game layer don't pay the construction
        cost. Honours ``configs.game.enabled``: when False, every
        ``record_xp_event`` call raises ``GameDisabled``.
        """
        if self._game is None:
            from ..game.config import load_game_config
            from ..game.facade import GameFacade

            self._game = GameFacade(self.caldav, load_game_config(self._configs.game))
        return self._game

    # -------------------------------------------------------------------------
    # Facade-level orchestration for the game layer
    # -------------------------------------------------------------------------

    def record_xp_event(
        self,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
        reason: str | None = None,
        stat_id: str | None = None,
        delta: int | None = None,
        idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ):
        """Record an XP event for a task, session, or ad-hoc reason.

        Exactly one of ``task_id``, ``session_id``, ``reason`` must
        be supplied. The facade fetches the entity from caldav,
        builds the appropriate ``XpSource``, and delegates to
        ``game.record_xp_event``.

        Idempotency keys auto-derive for tasks/sessions
        (``task:{uid}:completed``, ``session:{uid}:stopped``);
        reason callers must supply an explicit key.

        Returns the persisted ``XpEvent``, or ``None`` if the
        source entity could not be found. ``GameDisabled`` and
        ``GameClassificationError`` propagate to the caller.
        """
        from ..category.enums import TimeLogCategory
        from ..game.source import ReasonSource, SessionSource, TaskSource

        provided = [
            name
            for name, val in (
                ("task_id", task_id),
                ("session_id", session_id),
                ("reason", reason),
            )
            if val is not None
        ]
        if len(provided) != 1:
            raise ValueError(f"exactly one of task_id, session_id, reason is required (got: {provided or 'none'})")

        if task_id is not None:
            task = self.caldav.get_task(task_id)
            if task is None:
                return None
            source = TaskSource(
                task_uid=task.uid,
                title=task.title,
                description=task.description,
            )
            key = idempotency_key or f"task:{task.uid}:completed"
        elif session_id is not None:
            entry = self.caldav.get_time_log_entry(session_id)
            if entry is None:
                return None
            source = SessionSource(
                session_uid=entry.uid,
                label=entry.label,
                category=TimeLogCategory(entry.category),
            )
            key = idempotency_key or f"session:{entry.uid}:stopped"
        else:
            assert reason is not None  # guarded by the provided check above
            if idempotency_key is None:
                raise ValueError("reason source requires an explicit idempotency_key")
            source = ReasonSource(reason=reason)
            key = idempotency_key

        return self.game.record_xp_event(
            source=source,
            stat_id=stat_id,
            delta=delta,
            idempotency_key=key,
            occurred_at=occurred_at,
        )

    def complete_task(self, uid: str):
        """Mark a task COMPLETED and fire the XP event.

        Equivalent to ``caldav.update_task(uid, status=COMPLETED)``
        plus ``record_xp_event(task_id=uid, ...)``. XP failure is
        logged but does not propagate; the task is completed either
        way. Productivity writes are the source of truth.
        """
        from ..entities import TaskStatus, TaskUpdate

        updated = self.caldav.update_task(uid, TaskUpdate(status=TaskStatus.COMPLETED))
        if updated is not None:
            self._fire_xp_safely(
                self.record_xp_event,
                task_id=uid,
                occurred_at=datetime.now(UTC),
            )
        return updated

    def stop_session(
        self,
        uid: str,
        *,
        actual_due: datetime | None,
        completed_at: datetime | None,
        category=None,
    ):
        """Stop a session and fire the XP event.

        Equivalent to ``caldav.stop_time_log_entry(uid, ...)`` plus
        ``record_xp_event(session_id=uid, ...)``. XP failure is
        logged but does not propagate.
        """
        updated = self.caldav.stop_time_log_entry(
            uid,
            actual_due=actual_due,
            completed_at=completed_at,
            category=category,
        )
        if updated is not None:
            self._fire_xp_safely(
                self.record_xp_event,
                session_id=uid,
                occurred_at=completed_at or datetime.now(UTC),
            )
        return updated

    def _fire_xp_safely(self, fn, **kwargs) -> None:
        """Run ``fn(**kwargs)``. Swallow exceptions, log them.

        Used by ``complete_task`` and ``stop_session`` so XP layer
        failures never roll back the productivity write.
        """
        try:
            fn(**kwargs)
        except GameDisabled:
            # User has the game layer off. Expected.
            pass
        except GameClassificationError as exc:
            # Inference failed. Logged because it usually points to
            # a session/task the keyword engine couldn't classify.
            logger.warning("XP classification failed: %s", exc)
        except Exception as exc:
            # Anything else: log loudly so we see it, but do not
            # break the caller.
            logger.warning("XP event fire failed: %s", exc)

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
