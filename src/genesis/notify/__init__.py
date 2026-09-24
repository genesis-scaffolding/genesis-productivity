"""ntfy notification transport and message builders.

Exposes :class:`NotifyFacade` and the message builders in
:mod:`genesis.notify.messages`. Reached via ``Facade.notify``
on the parent :class:`genesis.core.facade.Facade`. See ADR-027.

The facade raises on transport failure; the caller (the focus
page and the focus create dialog) is responsible for catching
and ignoring so a dead ntfy server never breaks the page.
"""

from __future__ import annotations

from .facade import NotifyFacade
from .messages import session_finished, session_milestone, session_started

__all__ = [
    "NotifyFacade",
    "session_finished",
    "session_milestone",
    "session_started",
]
