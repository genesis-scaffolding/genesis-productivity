"""Server-side focus session scheduler.

Moves the timing-driven focus session work (30-minute milestone
notifications and countdown auto-stop) off the Streamlit
fragment and into a per-process daemon thread. Survives browser
tabs being closed; works for any process that constructs a
:class:`genesis.core.facade.Facade` with ``ensure_server=True``.

Public surface:

- :class:`FocusScheduler` — the tick loop.
- :func:`ensure_scheduler_running` — per-process cache; one
  thread per (process, user).
- :func:`stop_for_user`, :func:`stop_all` — graceful shutdown.

ADR-029.
"""

from __future__ import annotations

from .runtime import ensure_scheduler_running, stop_all, stop_for_user
from .scheduler import FocusScheduler

__all__ = [
    "FocusScheduler",
    "ensure_scheduler_running",
    "stop_all",
    "stop_for_user",
]
