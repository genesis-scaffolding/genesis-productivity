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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ..configs import Configs
from ..dav.server import ServerManager

if TYPE_CHECKING:
    from ..backup.facade import BackupFacade
    from ..caldav.facade import CalDAVFacade
    from ..carddav.facade import CardDAVFacade
    from ..focus_scheduler.scheduler import FocusScheduler
    from ..game.facade import GameClassificationError, GameDisabled, GameFacade
    from ..journal.facade import JournalFacade
    from ..notify.facade import NotifyFacade
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
        self._notify: NotifyFacade | None = None
        self._focus_scheduler: FocusScheduler | None = None

        if ensure_server:
            self._ensure_server_running()
            self._ensure_scheduler_running()

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

    def ensure_server_running(self) -> None:
        """Keep the DAV server alive (idempotent, cheap when up).

        Intended for long-lived / cached facades (e.g. the per-subject
        API facade cache): a sub-millisecond localhost socket probe
        confirms Radicale is reachable, and if it has died, restarts it.
        When the server is already up, ``start()`` returns immediately and
        the htpasswd file is NOT rewritten (that only happens on a fresh
        start), so this is cheap enough to call on every request.
        """
        if self._server is None:
            self._server = ServerManager(
                dav_config=self._configs.dav,
                data_path=self._configs.paths.radicale_root,
            )
        self._server.start()

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
    def notify(self) -> NotifyFacade:
        """Return the Notify facade for ntfy POSTs.

        See ADR-027. Lazy-initialised on first access. The URL
        and topic are resolved at construction time from
        :class:`genesis.configs.NtfyConfig`, so the consumer
        never has to touch the configs object to send a
        notification.

        The facade does NOT swallow errors. Callers (the focus
        page and the focus create dialog) wrap every call in a
        ``try/except`` so a dead ntfy server cannot break the
        page.
        """
        if self._notify is None:
            from ..notify.facade import NotifyFacade

            cfg = self.configs.ntfy
            self._notify = NotifyFacade(
                url=cfg.url,
                topic=cfg.resolved_topic(self.configs.user),
            )
        return self._notify

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

    def start_session(
        self,
        *,
        label: str,
        mode: str,
        duration_minutes: int | None = None,
        category: str | None = None,
        task_id: str | None = None,
        description: str | None = None,
    ):
        """Start a new focus session.

        Creates a time log entry with status IN_PROGRESS. For
        countdown mode, ``duration_minutes`` is required and sets
        the due time. Optionally flips an aligned task to
        IN_PROGRESS.

        Returns the created entry.
        """
        from ..caldav.ical import now_utc
        from ..category.enums import TimeLogCategory
        from ..entities import TimeLogEntryCreate, TimeLogMode, TimeLogStatus

        now = now_utc()
        mode_enum = TimeLogMode(mode)
        due = None
        if mode_enum == TimeLogMode.COUNTDOWN:
            if duration_minutes is None:
                raise ValueError("duration_minutes is required for countdown mode")
            due = now + timedelta(minutes=duration_minutes)

        category_enum = TimeLogCategory(category) if category is not None else TimeLogCategory.OTHER

        entry = self.caldav.create_time_log_entry(
            TimeLogEntryCreate(
                label=label,
                start=now,
                due=due,
                mode=mode_enum,
                status=TimeLogStatus.IN_PROGRESS,
                task_id=task_id,
                category=category_enum,
                description=description,
            )
        )

        # Flip aligned task to IN_PROGRESS (mirrors the focus page).
        if task_id is not None:
            from ..entities import TaskStatus, TaskUpdate

            task = self.caldav.get_task(task_id)
            if task is not None and task.status != TaskStatus.IN_PROGRESS:
                self.caldav.update_task(task_id, TaskUpdate(status=TaskStatus.IN_PROGRESS))

        return entry

    def lodge_completed_session(
        self,
        *,
        label: str,
        start: datetime,
        duration_minutes: int,
        category: str | None = None,
        task_id: str | None = None,
        description: str | None = None,
    ):
        """Lodge a completed focus session (retrospective logging).

        Creates a time log entry that is already COMPLETED: both
        ``due`` and ``completed_at`` are ``start + duration``.
        Fires the XP event. Returns the created entry.
        """
        from ..category.enums import TimeLogCategory
        from ..entities import TimeLogEntryCreate, TimeLogMode, TimeLogStatus

        start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = start_utc + timedelta(minutes=duration_minutes)

        category_enum = TimeLogCategory(category) if category is not None else TimeLogCategory.OTHER

        entry = self.caldav.create_time_log_entry(
            TimeLogEntryCreate(
                label=label,
                start=start_utc,
                due=end_utc,
                completed_at=end_utc,
                mode=TimeLogMode.COUNTDOWN,
                status=TimeLogStatus.COMPLETED,
                task_id=task_id,
                category=category_enum,
                description=description,
            )
        )

        self._fire_xp_safely(
            self.record_xp_event,
            session_id=entry.uid,
            occurred_at=end_utc,
        )
        return entry

    def discard_session(self, uid: str) -> bool:
        """Delete an active session. Returns True if deleted."""
        return self.caldav.delete_time_log_entry(uid)

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

    def _ensure_scheduler_running(self) -> None:
        """Start the focus scheduler thread for this user.

        Lazy import keeps the dependency direction one-way:
        ``genesis.core`` does not import ``genesis.focus_scheduler``
        at module load. Tests that mock :class:`ServerManager`
        also patch
        :func:`genesis.focus_scheduler.runtime.ensure_scheduler_running`
        to a no-op so the Facade stays cheap to construct and
        does not leak threads across test cases.

        ADR-029.
        """
        from ..focus_scheduler.runtime import ensure_scheduler_running

        self._focus_scheduler = ensure_scheduler_running(self._configs.user)

    @property
    def focus_scheduler(self) -> FocusScheduler | None:
        """Return the focus scheduler for this user.

        Plain read of ``_focus_scheduler``, which is set
        eagerly in :meth:`__init__` when ``ensure_server=True``.
        Returns ``None`` when the Facade was constructed with
        ``ensure_server=False`` (a one-shot CLI read, the UI's
        nav peek, etc.).

        ADR-029.
        """
        return self._focus_scheduler

    def shutdown(self) -> None:
        """Stop the focus scheduler and the DAV server if we started them.

        Order matters: the scheduler's tick relies on Radicale,
        so we stop the scheduler first. If Radicale were stopped
        first, an in-flight tick would fail.
        """
        if self._focus_scheduler is not None:
            from ..focus_scheduler.runtime import stop_for_user

            stop_for_user(self._configs.user)
            self._focus_scheduler = None
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
