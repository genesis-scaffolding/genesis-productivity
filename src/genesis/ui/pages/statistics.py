"""Statistics page: rollups of completed focus sessions.

Aggregates over ``caldav.list_time_log()`` (default = completed
historical records). All clientside aggregation; the underlying
list is small and CalDAV is already filtered.

Period selector
---------------
A single-select segmented control drives the window for the KPI
strip, the daily chart, and the by-task table. Recent sessions
ignores the period filter (always shows the last N regardless).

Duration convention
-------------------
The recorded duration for any completed session is ``due - start``:
countdown auto-stopped at the target keeps the target; manually
stopped countdowns and count-ups reflect actual elapsed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import streamlit as st

from genesis.tz import format_local, to_local, to_utc
from genesis.ui._state import get_facade

facade = get_facade()
caldav = facade.caldav

st.title("Statistics")
st.caption("Productivity metrics from completed focus sessions.")


# -----------------------------------------------------------------------------
# Period selector
# -----------------------------------------------------------------------------


PERIOD_OPTIONS = ["This week", "This month", "Last 30 days", "All time"]
PERIOD_DEFAULT = "This week"


def _period_bounds(label: str) -> tuple[datetime, datetime | None]:
    """Return (start, end) for the selected period. end=None means open."""
    today = date.today()
    today_start = to_utc(datetime.combine(today, time.min))
    if label == "This week":
        # Monday of the current week.
        week_start_date = today - timedelta(days=today.weekday())
        start = to_utc(datetime.combine(week_start_date, time.min))
        return start, today_start + timedelta(days=1)
    if label == "This month":
        month_start_date = today.replace(day=1)
        start = to_utc(datetime.combine(month_start_date, time.min))
        return start, today_start + timedelta(days=1)
    if label == "Last 30 days":
        start = today_start - timedelta(days=29)
        return start, today_start + timedelta(days=1)
    if label == "All time":
        return datetime(1970, 1, 1, tzinfo=UTC), None
    raise ValueError(f"Unknown period label: {label}")


period_label = st.segmented_control(
    "Period",
    options=PERIOD_OPTIONS,
    default=st.session_state.get("stats_period", PERIOD_DEFAULT),
    key="stats_period",
    label_visibility="collapsed",
)
if period_label is None:
    period_label = PERIOD_DEFAULT

period_start, period_end = _period_bounds(period_label)


# -----------------------------------------------------------------------------
# Fetch and filter
# -----------------------------------------------------------------------------


all_completed = caldav.list_time_log()
in_period = [
    s
    for s in all_completed
    if s.start >= period_start and (period_end is None or s.start < period_end)
]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Bucket:
    """Aggregate counters for a grouping key (task id, day, mode, ...)."""

    count: int
    total_seconds: float

    @property
    def avg_seconds(self) -> float:
        return self.total_seconds / self.count if self.count else 0.0


def _duration_seconds(s) -> float:
    """Recorded duration of a completed session in seconds."""
    if s.due is None or s.start is None:
        return 0.0
    return (s.due - s.start).total_seconds()


def _fmt_hms(seconds: float) -> str:
    """Render seconds as 'Hh Mm' or 'Mm Ss'."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


# -----------------------------------------------------------------------------
# KPI strip
# -----------------------------------------------------------------------------


total_seconds = sum(_duration_seconds(s) for s in in_period)
session_count = len(in_period)
avg_seconds = (total_seconds / session_count) if session_count else 0.0
task_ids = {s.task_id for s in in_period if s.task_id is not None}

st.write("")
kpi_cols = st.columns(4, gap="medium")
with kpi_cols[0]:
    with st.container(border=True):
        st.caption("Total time")
        st.markdown(f"## {_fmt_hms(total_seconds)}")
        st.caption(f"Across {session_count} sessions")
with kpi_cols[1]:
    with st.container(border=True):
        st.caption("Sessions")
        st.markdown(f"## {session_count}")
        st.caption("Completed in period")
with kpi_cols[2]:
    with st.container(border=True):
        st.caption("Avg session")
        st.markdown(f"## {_fmt_hms(avg_seconds)}")
        st.caption("Per session")
with kpi_cols[3]:
    with st.container(border=True):
        st.caption("Tasks touched")
        st.markdown(f"## {len(task_ids)}")
        st.caption("Unique aligned tasks")

st.divider()


# -----------------------------------------------------------------------------
# Daily activity bar chart
# -----------------------------------------------------------------------------


st.subheader("Daily activity")
st.caption(f"Minutes per day, in local time, for {period_label.lower()}.")
if not in_period:
    st.info("No completed sessions in this period yet.")
else:
    by_day: dict[date, float] = defaultdict(float)
    for s in in_period:
        local_day = to_local(s.start).date()
        by_day[local_day] += _duration_seconds(s) / 60.0
    if period_end is not None:
        end_day = to_local(period_end - timedelta(microseconds=1)).date()
    else:
        end_day = max(by_day.keys())
    start_day = to_local(period_start).date()
    days = pd.date_range(start=start_day, end=end_day, freq="D")
    df = pd.DataFrame(
        {
            "day": days,
            "minutes": [
                round(by_day.get(d.date(), 0.0), 1)
                for d in days
            ],
        }
    ).set_index("day")
    st.bar_chart(df, height=220, width="stretch")

st.write("")


# -----------------------------------------------------------------------------
# By task
# -----------------------------------------------------------------------------


st.subheader("By task")
st.caption("Aligned sessions grouped by task. Top 10 by total time.")
aligned = [s for s in in_period if s.task_id is not None]
unaligned = [s for s in in_period if s.task_id is None]

if not aligned:
    st.info("No aligned sessions in this period.")
else:
    by_task: dict[str, _Bucket] = {}
    task_label_cache: dict[str, str] = {}

    for s in aligned:
        tid = s.task_id
        assert tid is not None
        if tid not in task_label_cache:
            task = caldav.get_task(tid)
            task_label_cache[tid] = task.title if task else "(deleted)"
        label = task_label_cache[tid]
        bucket = by_task.get(label)
        secs = _duration_seconds(s)
        if bucket is None:
            by_task[label] = _Bucket(count=1, total_seconds=secs)
        else:
            by_task[label] = _Bucket(
                count=bucket.count + 1,
                total_seconds=bucket.total_seconds + secs,
            )

    rows = sorted(
        by_task.items(),
        key=lambda kv: kv[1].total_seconds,
        reverse=True,
    )[:10]
    df = pd.DataFrame(
        {
            "Task": [r[0] for r in rows],
            "Sessions": [r[1].count for r in rows],
            "Total time": [_fmt_hms(r[1].total_seconds) for r in rows],
            "Avg": [_fmt_hms(r[1].avg_seconds) for r in rows],
        }
    )
    st.dataframe(df, hide_index=True, width="stretch")

if unaligned:
    secs = sum(_duration_seconds(s) for s in unaligned)
    st.caption(
        f"Unaligned: {len(unaligned)} sessions, {_fmt_hms(secs)} "
        f"(gym, reading, and other activities not tied to a task)"
    )

st.write("")


# -----------------------------------------------------------------------------
# Recent sessions
# -----------------------------------------------------------------------------


st.subheader("Recent sessions")
st.caption("Last 20 completed sessions, newest first.")

recent = sorted(in_period, key=lambda s: s.start, reverse=True)[:20]
if not recent:
    st.info("No completed sessions yet.")
else:
    rows = []
    for s in recent:
        task_label = ""
        if s.task_id is not None:
            task = caldav.get_task(s.task_id)
            task_label = task.title if task else "(deleted)"
        rows.append(
            {
                "Started": format_local(s.start, "%Y-%m-%d %H:%M"),
                "Label": s.label,
                "Task": task_label,
                "Mode": s.mode.value,
                "Duration": _fmt_hms(_duration_seconds(s)),
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch")
