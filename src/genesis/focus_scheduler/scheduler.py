"""Focus session scheduler tick loop.

The :class:`FocusScheduler` runs in a daemon thread and, on every
tick, performs two pieces of work that previously lived in the
Streamlit focus page's ``@st.fragment(run_every="1s")``:

1. **Countdown auto-stop.** For each active countdown session
   whose ``due`` has passed, call :meth:`Facade.stop_session`.
   On If-Match win, fire a ``session_finished`` ntfy
   notification. ADR-014, ADR-027.
2. **30-minute milestone notifications.** For each active
   session, compute crossings since ``last_milestone_minutes``,
   persist the new value first, then fire one POST per crossing.
   Persist-before-notify so a PUT failure skips the notify
   rather than firing twice. ADR-028.

Both pieces of work are derivable from ``now`` plus the
persisted session state. The scheduler is stateless across
restarts; the CalDAV VTODO is the source of truth. Multiple
schedulers ticking concurrently across processes are safe via
the same If-Match race protection that protects the focus
page's manual stops.

The thread is ``daemon=True`` so a hang in the tick loop
cannot block process shutdown. A :class:`threading.Event`
named ``_stop_event`` is the cooperative-shutdown signal; the
main loop waits on it between ticks.

ADR-029.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from ..caldav.ical import now_utc
from ..entities import (
    TimeLogEntry,
    TimeLogEntryUpdate,
    TimeLogMode,
    TimeLogStatus,
)
from ..notify import messages

if TYPE_CHECKING:
    from ..core.facade import Facade


logger = logging.getLogger(__name__)


# Milestone cadence: notifications fire on every 30-minute
# crossing. The unit lives on the wire as
# ``X-LAST-MILESTONE-MINUTES`` (ADR-028); if we ever change the
# cadence, the field name should change alongside it.
_MILESTONE_MINUTES = 30


# Default tick interval. Five seconds is short enough that
# auto-stop lag is unnoticeable and long enough that the per-tick
# CalDAV load is trivial (~0.2 qps per scheduler instance).
# Override via the constructor for tests.
_DEFAULT_TICK_SECONDS = 5.0


class FocusScheduler:
    """Tick loop for focus session timing-driven events.

    Construct one per :class:`genesis.core.facade.Facade`. The
    constructor does NOT start the thread; call :meth:`start`
    explicitly. The Facade's ``__init__`` does this for callers
    using ``ensure_server=True``; tests construct the scheduler
    directly and call ``start``/``stop`` themselves.

    The thread is per-process. A module-level cache in
    :mod:`genesis.focus_scheduler.runtime` deduplicates so the
    FastAPI per-request Facade does not spawn one thread per
    request.
    """

    def __init__(
        self,
        *,
        facade: Facade,
        tick_seconds: float = _DEFAULT_TICK_SECONDS,
    ) -> None:
        if tick_seconds <= 0:
            raise ValueError(f"tick_seconds must be positive, got {tick_seconds}")
        self._facade = facade
        self._tick_seconds = tick_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Per-process cache of the highest persisted milestone
        # minute value we have already attempted to fire for
        # each session. Avoids a CalDAV PUT every tick in the
        # steady state (the page had the same optimisation via
        # ``st.session_state``; the scheduler has it via a plain
        # dict). Cross-process dedup still relies on
        # ``last_milestone_minutes`` on the VTODO itself.
        self._last_seen_milestone: dict[str, int] = {}
        # Test seams: total ticks completed; wall-clock time of
        # the last tick start. Production code does not read
        # these. A future health check could.
        self._tick_count = 0
        self._last_tick_at: datetime | None = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the daemon thread. Idempotent.

        Safe to call multiple times. A second call after the
        thread is already running is a no-op so callers do not
        have to guard against double-start.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        thread = threading.Thread(
            target=self._run,
            name=f"focus-scheduler-{self._facade.configs.user}",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        """Signal the thread to exit and join. Idempotent.

        Safe to call before :meth:`start`, after a successful
        stop, or from a signal handler. ``timeout`` is the
        maximum seconds to wait for the thread to drain its
        current tick. After the timeout, the daemon thread is
        abandoned (it will not block process exit).
        """
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        # If the thread is still alive after the timeout we
        # leave it running; ``daemon=True`` ensures it cannot
        # block process shutdown. We deliberately do not
        # ``raise`` here: callers (signal handlers, ``atexit``
        # hooks, the Facade's ``shutdown`` method) need stop()
        # to be best-effort.
        self._thread = None
        # The cache is keyed by session UID; once we stop the
        # scheduler, callers may still hold references but we
        # are done with the data.
        self._last_seen_milestone.clear()

    def wait_until_stopped(self) -> None:
        """Block until :meth:`stop` is called. Used by the CLI."""
        self._stop_event.wait()

    # -------------------------------------------------------------------------
    # Public observability
    # -------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the daemon thread is alive."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def tick_count(self) -> int:
        """Total ticks completed since :meth:`start`. Test seam."""
        return self._tick_count

    @property
    def last_tick_at(self) -> datetime | None:
        """UTC time of the most recent tick start. Test / health seam."""
        return self._last_tick_at

    # -------------------------------------------------------------------------
    # Thread body
    # -------------------------------------------------------------------------

    def _run(self) -> None:
        """Daemon thread body. Loops until ``_stop_event`` is set.

        Wait BEFORE the first tick so the Facade construction
        that started this scheduler can finish its own CalDAV
        probes without contending with our first request. Radicale
        is single-threaded; concurrent CalDAV work serialises
        against its accept loop, and a freshly-started Radicale
        takes ~hundreds of milliseconds to index a new collection
        on first access. Skipping the immediate first tick means
        the caller does not pay that cost on the same wall-clock
        instant as ``Facade.__init__``.

        Subsequent ticks run ``tick_seconds`` apart, same as
        before. Errors during a tick are logged and swallowed;
        the next tick retries.

        ADR-029.
        """
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self._tick_seconds):
                return
            self._tick_once()

    def _tick_once(self, now: datetime | None = None) -> None:
        """One iteration of the tick loop. Exposed for tests.

        Production calls this from the daemon thread. Tests call
        it directly with a fixed ``now`` to avoid the
        ``time.sleep`` dependency and finish in well under a
        second.
        """
        self._tick_count += 1
        self._last_tick_at = now or now_utc()
        current = self._last_tick_at

        try:
            sessions = self._facade.caldav.list_time_log(active_only=True)
        except Exception as exc:
            logger.warning("focus scheduler: list_time_log failed: %s", exc)
            return

        # Pass 1: auto-stop countdowns whose due has passed.
        # ``stop_session`` mutates the VTODO (status -> COMPLETED)
        # so a follow-up list would not return the session; we
        # do not bother refetching.
        for s in sessions:
            self._maybe_auto_stop(s, current)

        # Pass 2: milestone notifications on every active
        # session. Reload the list so we do not iterate over
        # sessions we just stopped (their ``status`` would now
        # be COMPLETED, but skipping them again is harmless).
        try:
            sessions = self._facade.caldav.list_time_log(active_only=True)
        except Exception as exc:
            logger.warning("focus scheduler: list_time_log (pass 2) failed: %s", exc)
            return

        for s in sessions:
            self._maybe_fire_milestone(s, current)

    # -------------------------------------------------------------------------
    # Auto-stop
    # -------------------------------------------------------------------------

    def _maybe_auto_stop(self, s: TimeLogEntry, now: datetime) -> None:
        """Auto-stop an expired countdown and fire the finished notification.

        Fires only when :meth:`Facade.stop_session` returns a
        truthy value (the If-Match win). When it returns
        ``None`` we lost the race to another device / process;
        silently skip the notify so the race winner fires
        exactly once.
        """
        if s.mode != TimeLogMode.COUNTDOWN:
            return
        if s.due is None:
            return
        if now < s.due:
            return
        if s.status == TimeLogStatus.COMPLETED:
            return

        try:
            result = self._facade.stop_session(
                s.uid,
                actual_due=s.due,
                completed_at=now,
                category=s.category,
            )
        except Exception as exc:
            logger.warning("focus scheduler: stop_session(%s) failed: %s", s.uid, exc)
            return

        if result is None:
            # Lost the If-Match race. Another device / process
            # already stopped this session; that side fires the
            # notification.
            return

        task_title = self._task_title(s)
        self._notify_safely(lambda s=s, tt=task_title: messages.session_finished(s, task_title=tt))

    # -------------------------------------------------------------------------
    # Milestone notifications
    # -------------------------------------------------------------------------

    def _maybe_fire_milestone(self, s: TimeLogEntry, now: datetime) -> None:
        """Fire a 30-minute milestone notification if a crossing just happened.

        Source of truth is ``s.last_milestone_minutes`` on the
        TimeLogEntry itself (ADR-028): the highest elapsed-minute
        value at which a milestone notification has already
        fired. The per-process ``_last_seen_milestone`` cache
        suppresses the CalDAV PUT in the steady state (the page
        had the same optimisation via ``st.session_state``).

        Persist BEFORE notify: if the PUT fails (network, 412
        race, 404) we skip the notify rather than fire twice. A
        subsequent tick catches up on transient failures; a
        multi-device race winner is detected by refetching and
        seeing the field has already moved.

        On first tick of a long-running session we may need to
        back-fire several crossings; we issue one POST per
        crossing, sequenced.
        """
        if s.status == TimeLogStatus.COMPLETED:
            return

        elapsed_seconds = (now - s.start).total_seconds()
        if elapsed_seconds < _MILESTONE_MINUTES * 60:
            return
        elapsed_minutes = int(elapsed_seconds // 60)

        cached = self._last_seen_milestone.get(s.uid, 0)
        effective_max = max(s.last_milestone_minutes, cached)
        crossings = list(
            range(
                effective_max + _MILESTONE_MINUTES,
                elapsed_minutes + 1,
                _MILESTONE_MINUTES,
            )
        )
        if not crossings:
            return

        new_max = crossings[-1]
        try:
            result = self._facade.caldav.update_time_log_entry(
                s.uid,
                TimeLogEntryUpdate(last_milestone_minutes=new_max),
            )
        except Exception as exc:
            logger.warning("focus scheduler: update_time_log_entry(%s) failed: %s", s.uid, exc)
            return

        if result is None:
            # Distinguish "another device won" from "transient
            # PUT failure" by refetching.
            try:
                refetched = self._facade.caldav.get_time_log_entry(s.uid)
            except Exception as exc:
                logger.warning("focus scheduler: get_time_log_entry(%s) failed: %s", s.uid, exc)
                return
            if refetched is not None and refetched.last_milestone_minutes >= new_max:
                # Another device already persisted and fired for
                # these crossings. Refresh the cache from the
                # server view so we do not refetch on every tick.
                self._last_seen_milestone[s.uid] = max(cached, refetched.last_milestone_minutes)
                return
            # Transient PUT failure: skip this tick's notify,
            # let the next tick retry.
            return

        self._last_seen_milestone[s.uid] = max(cached, new_max)

        task_title = self._task_title(s)
        for c in crossings:
            self._notify_safely(lambda s=s, n=c, tt=task_title: messages.session_milestone(s, n, task_title=tt))

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _task_title(self, s: TimeLogEntry) -> str | None:
        """Return the title of the task aligned to ``s``, if any.

        Returns ``None`` when the session is unaligned or the
        task was deleted (legitimate race). ADR-027.
        """
        if not s.task_id:
            return None
        try:
            task = self._facade.caldav.get_task(s.task_id)
        except Exception as exc:
            logger.warning("focus scheduler: get_task(%s) failed: %s", s.task_id, exc)
            return None
        return task.title if task is not None else None

    def _notify_safely(self, builder: Callable[[], tuple[str, str, list[str]]]) -> None:
        """Run a message builder and POST to ntfy. Never raises.

        A dead ntfy server must not break the scheduler tick.
        We log a warning so the operator can see the failure
        but otherwise swallow the exception. Mirrors the
        ``_notify_safely`` shape that used to live in the
        focus page.
        """
        try:
            title, message, tags = builder()
            self._facade.notify.send(title, message, tags=tags)
        except Exception as exc:
            logger.warning("focus scheduler: notify failed: %s", exc)


__all__ = ["FocusScheduler"]
