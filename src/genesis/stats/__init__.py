"""Statistics rollup facade.

Pure aggregation over completed time log entries. No I/O, no UI, no
network. Both the Streamlit Statistics page and the FastAPI
``/productivity/statistics`` endpoint call :func:`compute_rollup` to
get a :class:`Rollup`. The UI formats the result for display; the
API serialises it as JSON.

Period labels match the UI segmented control exactly so a caller can
ask "what does this-week look like?" without knowing the underlying
window math:

    - ``this-week``     Monday of the current week through end of today
    - ``this-month``    1st of the current month through end of today
    - ``last-30-days``  29 days ago through end of today
    - ``all-time``      epoch onwards; upper bound is the latest entry's day

All datetimes on :class:`~genesis.entities.TimeLogEntry` are tz-aware
UTC per the project tz convention. Local-day bucketing uses
:func:`genesis.tz.to_local`.
"""

from .facade import (
    ByCategory,
    DailyPoint,
    Kpis,
    Period,
    PeriodBounds,
    RecentSession,
    Rollup,
    TaskBucket,
    compute_rollup,
    duration_seconds,
    period_bounds,
)

__all__ = [
    "Period",
    "PeriodBounds",
    "Kpis",
    "DailyPoint",
    "TaskBucket",
    "ByCategory",
    "RecentSession",
    "Rollup",
    "compute_rollup",
    "duration_seconds",
    "period_bounds",
]
