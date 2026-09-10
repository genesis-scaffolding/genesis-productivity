"""Statistics page: rollups of completed focus sessions.

The aggregation lives in :mod:`genesis.stats`. This page handles the
period selector widget, the display formatting, and the chart glue.

Period selector
---------------
A single-select segmented control drives the window for the KPI
strip, the daily chart, the by-task table, and the recent-sessions
list. The values are the same as the API's ``StatisticsPeriod`` enum.

Duration convention
-------------------
The recorded duration for any completed session is ``due - start``
(see :func:`genesis.stats.duration_seconds`): countdown auto-stopped
at the target keeps the target; manually stopped countdowns and
count-ups reflect actual elapsed.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from genesis.stats import Period, compute_rollup
from genesis.tz import format_local
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

_PERIOD_LABEL_TO_ENUM = {
    "This week": Period.THIS_WEEK,
    "This month": Period.THIS_MONTH,
    "Last 30 days": Period.LAST_30_DAYS,
    "All time": Period.ALL_TIME,
}

period_label = st.segmented_control(
    "Period",
    options=PERIOD_OPTIONS,
    default=st.session_state.get("stats_period", PERIOD_DEFAULT),
    key="stats_period",
    label_visibility="collapsed",
)
if period_label is None:
    period_label = PERIOD_DEFAULT


# -----------------------------------------------------------------------------
# Fetch and roll up
# -----------------------------------------------------------------------------


all_completed = caldav.list_time_log()

task_ids = {e.task_id for e in all_completed if e.task_id is not None}
task_titles: dict[str, str] = {}
for tid in task_ids:
    task = caldav.get_task(tid)
    task_titles[tid] = task.title if task else "(deleted)"

rollup = compute_rollup(
    entries=all_completed,
    period=_PERIOD_LABEL_TO_ENUM[period_label],
    today=date.today(),
    task_titles=task_titles,
)


# -----------------------------------------------------------------------------
# Display helpers
# -----------------------------------------------------------------------------


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


st.write("")
kpi_cols = st.columns(4, gap="medium")
with kpi_cols[0]:
    with st.container(border=True):
        st.caption("Total time")
        st.markdown(f"## {_fmt_hms(rollup.kpis.total_seconds)}")
        st.caption(f"Across {rollup.kpis.session_count} sessions")
with kpi_cols[1]:
    with st.container(border=True):
        st.caption("Sessions")
        st.markdown(f"## {rollup.kpis.session_count}")
        st.caption("Completed in period")
with kpi_cols[2]:
    with st.container(border=True):
        st.caption("Avg session")
        st.markdown(f"## {_fmt_hms(rollup.kpis.avg_seconds)}")
        st.caption("Per session")
with kpi_cols[3]:
    with st.container(border=True):
        st.caption("Tasks touched")
        st.markdown(f"## {rollup.kpis.tasks_touched}")
        st.caption("Unique aligned tasks")

st.divider()


# -----------------------------------------------------------------------------
# Daily activity bar chart
# -----------------------------------------------------------------------------


st.subheader("Daily activity")
st.caption(f"Minutes per day, in local time, for {period_label.lower()}.")
if not rollup.daily:
    st.info("No completed sessions in this period yet.")
else:
    df = pd.DataFrame(
        {
            "day": [p.day for p in rollup.daily],
            "minutes": [p.minutes for p in rollup.daily],
        }
    ).set_index("day")
    st.bar_chart(df, height=220, width="stretch")

st.write("")


# -----------------------------------------------------------------------------
# By task
# -----------------------------------------------------------------------------


st.subheader("By task")
st.caption("Aligned sessions grouped by task. Top 10 by total time.")

if not rollup.by_task:
    st.info("No aligned sessions in this period.")
else:
    df = pd.DataFrame(
        {
            "Task": [b.title for b in rollup.by_task],
            "Sessions": [b.count for b in rollup.by_task],
            "Total time": [_fmt_hms(b.total_seconds) for b in rollup.by_task],
            "Avg": [_fmt_hms(b.avg_seconds) for b in rollup.by_task],
        }
    )
    st.dataframe(df, hide_index=True, width="stretch")

aligned_total = sum(b.count for b in rollup.by_task)
aligned_seconds = sum(b.total_seconds for b in rollup.by_task)
unaligned_count = rollup.kpis.session_count - aligned_total
if unaligned_count:
    unaligned_seconds = rollup.kpis.total_seconds - aligned_seconds
    st.caption(
        f"Unaligned: {unaligned_count} sessions, {_fmt_hms(unaligned_seconds)} "
        f"(gym, reading, and other activities not tied to a task)"
    )

st.write("")


# -----------------------------------------------------------------------------
# Recent sessions
# -----------------------------------------------------------------------------


st.subheader("Recent sessions")
st.caption(f"Last {len(rollup.recent)} completed sessions in period, newest first.")

if not rollup.recent:
    st.info("No completed sessions in this period.")
else:
    rows = [
        {
            "Started": format_local(s.start, "%Y-%m-%d %H:%M"),
            "Label": s.label,
            "Task": s.task_title,
            "Mode": s.mode,
            "Duration": _fmt_hms(s.duration_seconds),
        }
        for s in rollup.recent
    ]
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch")