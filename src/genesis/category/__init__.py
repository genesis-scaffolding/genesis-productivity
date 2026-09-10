"""Category module: enum, inference, facade, and migration CLI.

Closed-set categories for time log entries. Used at stop time (focus
page shows the inferred value as a default the user can override)
and at statistics roll-up time (per-category KPI breakdown, stacked
daily chart, by-category section).

This package's ``__init__`` re-exports only the modules that have
no CalDAV dependency. Import :class:`CategoryFacade` and the
migration CLI directly from their submodules
(``genesis.category.facade`` and ``genesis.category.migration``) to
avoid loading CalDAV on every import.
"""

from .enums import TimeLogCategory
from .inference import infer_category

__all__ = [
    "TimeLogCategory",
    "infer_category",
]
