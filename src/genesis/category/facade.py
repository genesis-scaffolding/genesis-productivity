"""CategoryFacade: thin wrapper that pulls history from caldav and
delegates to :func:`genesis.category.inference.infer_category`.

Used by the focus page (stop flow) and the migration CLI. Pure
delegation; no caching, no business state.
"""

from __future__ import annotations

from ..caldav.facade import CalDAVFacade
from .enums import TimeLogCategory
from .inference import infer_category


class CategoryFacade:
    """Looks up category history and runs inference.

    Args:
        caldav: A :class:`genesis.caldav.facade.CalDAVFacade` (or the
            in-memory mock). The facade is only used to read history;
            writes happen through the normal caldav update flow.
    """

    def __init__(self, caldav: CalDAVFacade) -> None:
        self._caldav = caldav

    def infer(self, label: str, task_id: str | None) -> TimeLogCategory | None:
        """Return the recommended category for ``label`` + ``task_id``,
        or ``None`` when there is no confident signal.

        History is loaded from completed time log entries. Newest
        first; only the ``label`` -> ``category`` mapping is read.

        Callers that always want a value (e.g. the focus page) should
        fall back to ``TimeLogCategory.PERSONAL`` on ``None``.
        """
        entries = self._caldav.list_time_log()
        history: list[tuple[str, TimeLogCategory]] = [
            (e.label, e.category) for e in entries
        ]
        return infer_category(label, task_id, history)
