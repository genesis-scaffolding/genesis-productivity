"""Per-process cache for focus schedulers.

The FastAPI process builds a fresh :class:`Facade` per request.
Without dedup, every request would spawn a daemon thread and
we'd have N threads per process for N requests. The cache
keeps one thread per (process, user); subsequent
``ensure_scheduler_running`` calls for the same user return
the same instance.

The cache is module-level. There is no cross-process
coordination: multiple processes can concurrently host
schedulers for the same user. ADR-014 / ADR-028 If-Match race
protection keeps notifications deduplicated; the cost is
duplicate CalDAV list queries, which is acceptable at a 5s
tick (~0.4 qps combined across UI + API).

``atexit.register(stop_all)`` is installed at module import so
a clean Python exit drains the threads. The Streamlit signal
handlers in :mod:`genesis.ui._state` also call ``stop_all`` on
``SIGTERM`` / ``SIGINT`` so a Ctrl+C in ``make serve`` stops
the schedulers before the process exits.

ADR-029.
"""

from __future__ import annotations

import atexit
import logging
import threading
from typing import TYPE_CHECKING

from ..configs import resolve_config_path
from ..core.facade import Facade
from .scheduler import FocusScheduler

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


# Per-process scheduler registry. Keyed by username (the same
# string used by ``Configs.user``). Value is the
# ``FocusScheduler``; the ``threading.Thread`` is reachable
# via ``scheduler._thread``.
_schedulers: dict[str, FocusScheduler] = {}
_lock = threading.Lock()


def ensure_scheduler_running(user: str) -> FocusScheduler:
    """Return the scheduler for ``user``, starting one if needed.

    One thread per (process, user). Multiple Facades in the
    same process share this cache so the FastAPI per-request
    path does not spawn a thread per request.

    The scheduler is constructed with the user's resolved
    config path; the scheduler itself owns its own Facade.
    This means the scheduler's lifecycle is independent of
    the requesting Facade's lifecycle: a Facade that returns
    from a request does not stop the scheduler. The scheduler
    dies when :func:`stop_for_user` is called or when the
    process exits via :func:`stop_all`.
    """
    with _lock:
        cached = _schedulers.get(user)
        if cached is not None:
            return cached

        # ``ensure_server=False`` is critical here. The Facade
        # the scheduler uses is an internal handle for talking
        # to an already-running Radicale subprocess over HTTP;
        # it does not need to start a fresh Radicale. More
        # importantly, it must NOT trigger another
        # ``ensure_scheduler_running`` call from its own
        # ``__init__`` — that would recursively re-enter the
        # runtime lock below, which is a non-re-entrant
        # ``threading.Lock`` and would deadlock. ADR-029.
        facade = Facade(resolve_config_path(user), ensure_server=False)
        scheduler = FocusScheduler(facade=facade)
        try:
            scheduler.start()
        except RuntimeError as exc:
            # ``threading.Thread`` cannot start outside the main
            # thread in some embedded environments. Surface the
            # failure but do not break the caller; subsequent
            # ticks via the scheduler are not available, but
            # the Facade's other surfaces (CalDAV, journal,
            # etc.) still work.
            logger.warning("focus scheduler: failed to start for user '%s': %s", user, exc)
            _schedulers[user] = scheduler  # store anyway so future calls return same instance
            return scheduler
        _schedulers[user] = scheduler
        return scheduler


def stop_for_user(user: str, *, timeout: float = 5.0) -> None:
    """Stop the scheduler for ``user`` and remove it from the cache.

    No-op when no scheduler is running for ``user``. Used by
    :class:`Facade`'s ``shutdown`` so each user's scheduler
    dies alongside Radicale.
    """
    with _lock:
        scheduler = _schedulers.pop(user, None)
    if scheduler is None:
        return
    scheduler.stop(timeout=timeout)


def stop_all(*, timeout: float = 5.0) -> None:
    """Stop every cached scheduler. Best-effort; never raises.

    Wired to ``atexit`` at module import and to the Streamlit
    signal handlers. Called by ``Facade.shutdown`` for the
    current user only; called wholesale on process exit.
    """
    with _lock:
        schedulers = list(_schedulers.values())
        _schedulers.clear()
    for scheduler in schedulers:
        try:
            scheduler.stop(timeout=timeout)
        except Exception as exc:
            logger.warning("focus scheduler: stop failed for user '%s': %s", scheduler._facade.configs.user, exc)


# Module-level shutdown hook. Runs on normal Python exit so a
# one-shot CLI command that constructed a Facade (and so a
# scheduler) cleans up the daemon thread before the process
# returns from ``main``. Daemon threads would also be reaped
# at process exit, but joining explicitly is cleaner and gives
# us the WARNING log path if ``stop()`` itself raises.
atexit.register(stop_all)
