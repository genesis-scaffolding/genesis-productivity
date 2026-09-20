"""Statistics page: rollups of completed focus sessions.

The aggregation lives in :mod:`genesis.stats`. This page handles the
period selector widget, the display formatting, and the chart glue.

Period selector
---------------
A single-select segmented control drives the window for **every**
section of the rollup: the KPI strip, the daily chart, the by-task
table, and the recent-sessions list. There is no period-independent
section. Switch to "All time" to widen the recent-sessions window.
The values are the same as the API's ``StatisticsPeriod`` enum.

Duration convention
-------------------
The recorded duration for any completed session is ``due - start``
(see :func:`genesis.stats.duration_seconds`): countdown auto-stopped
at the target keeps the target; manually stopped countdowns and
count-ups reflect actual elapsed.
"""

from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from genesis.category.enums import TimeLogCategory
from genesis.entities import TimeLogEntry, TimeLogEntryUpdate
from genesis.stats import Period, compute_rollup
from genesis.tz import format_local
from genesis.ui._render import session_log_dialog
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


def _fmt_minutes(minutes: float) -> str:
    """Render minutes as 'Hh Mm' (rounding to whole minutes)."""
    minutes = int(round(minutes))
    h, m = divmod(minutes, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def _fmt_hours(hours: float) -> str:
    """Render hours as 'Hh Mm' or 'Mm' (rounding to whole minutes)."""
    return _fmt_minutes(hours * 60)


# -----------------------------------------------------------------------------
# KPI strip
# -----------------------------------------------------------------------------


st.write("")
kpi_cols = st.columns(4, gap="medium")
with kpi_cols[0]:
    with st.container(border=True):
        st.caption("Total time")
        st.markdown(f"## {_fmt_minutes(rollup.kpis.total_minutes)}")
        st.caption(f"Across {rollup.kpis.session_count} sessions")
with kpi_cols[1]:
    with st.container(border=True):
        st.caption("Sessions")
        st.markdown(f"## {rollup.kpis.session_count}")
        st.caption("Completed in period")
with kpi_cols[2]:
    with st.container(border=True):
        st.caption("Avg session")
        st.markdown(f"## {_fmt_minutes(rollup.kpis.avg_minutes)}")
        st.caption("Per session")
with kpi_cols[3]:
    with st.container(border=True):
        st.caption("Tasks touched")
        st.markdown(f"## {rollup.kpis.tasks_touched}")
        st.caption("Unique aligned tasks")

st.write("")
st.subheader("By category")
st.caption("Total tracked time split by category.")
if rollup.kpis.session_count == 0:
    st.info("No completed sessions in this period yet.")
else:
    df = pd.DataFrame(
        {
            "Category": [b.category.value for b in rollup.by_category],
            "Time": [_fmt_hms(b.total_seconds) for b in rollup.by_category],
            "Sessions": [b.count for b in rollup.by_category],
        }
    )
    st.dataframe(df, hide_index=True, width="stretch")

st.divider()


# -----------------------------------------------------------------------------
# Daily activity bar chart
# -----------------------------------------------------------------------------


st.subheader("Daily activity")
st.caption(f"Hours per day, in local time, stacked by category, for {period_label.lower()}.")
if not rollup.daily:
    st.info("No completed sessions in this period yet.")
else:
    wide = pd.DataFrame(
        {
            cat.value: [round(p.minutes_by_category.get(cat, 0.0) / 60.0, 2) for p in rollup.daily]
            for cat in TimeLogCategory
        },
        index=[p.day.strftime("%a %-d") for p in rollup.daily],
    )
    wide.index.name = "Date"
    df = wide.reset_index().melt(id_vars="Date", var_name="Type", value_name="Hours")
    df["Hours Label"] = df["Hours"].map(_fmt_hours)
    # Compute the per-day total directly from ``rollup.daily`` so we
    # avoid the ``df.groupby(...).sum()`` chain whose return type the
    # pandas stubs over-generalize. Same numbers, simpler types.
    totals_rows: list[dict[str, object]] = []
    for p in rollup.daily:
        label = p.day.strftime("%a %-d")
        day_hours = sum(p.minutes_by_category.get(cat, 0.0) for cat in TimeLogCategory) / 60.0
        if day_hours > 0:
            totals_rows.append({"Date": label, "Hours": round(day_hours, 2)})
    totals = pd.DataFrame(totals_rows)
    if not totals.empty:
        totals["Total Label"] = totals["Hours"].map(_fmt_hours)
    seen: set[str] = set()
    labels_in_order: list[str] = []
    for p in rollup.daily:
        label = p.day.strftime("%a %-d")
        if label not in seen:
            seen.add(label)
            labels_in_order.append(label)
    x_axis = alt.X("Date:N", title="Day", sort=labels_in_order)
    bars = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=x_axis,
            y=alt.Y("Hours:Q", title="Hours", stack=True),
            color=alt.Color("Type:N", legend=alt.Legend(orient="bottom")),
            tooltip=[
                alt.Tooltip("Date:N", title="Date"),
                alt.Tooltip("Type:N", title="Type"),
                alt.Tooltip("Hours Label:N", title="Hours"),
            ],
        )
    )
    totals_text = (
        alt.Chart(totals)
        .mark_text(align="center", baseline="bottom", dy=-4, fontSize=11, color="#9ca3af")
        .encode(
            x=x_axis,
            y=alt.Y("Hours:Q"),
            text="Total Label:N",
        )
    )
    chart = (bars + totals_text).properties(height=360)
    st.altair_chart(chart, width="stretch", theme="streamlit")

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
    unaligned_seconds = (rollup.kpis.total_minutes * 60) - aligned_seconds
    st.caption(
        f"Unaligned: {unaligned_count} sessions, {_fmt_hms(unaligned_seconds)} "
        f"(gym, reading, and other activities not tied to a task)"
    )

st.write("")


# -----------------------------------------------------------------------------
# Recent sessions
# -----------------------------------------------------------------------------


st.subheader("Recent sessions")
st.caption(
    f"Up to {rollup.recent.__len__()} most recent completed sessions within {period_label.lower()}, newest first."
)

if st.button(
    "+ Log past session",
    key="stats_log_past_btn",
    help="Record a session you forgot to log.",
):
    session_log_dialog()

if not rollup.recent:
    st.info("No completed sessions in this period.")
else:
    # The rollup's RecentSession carries uid/start/labels but not the
    # category the user sees in the table. Look it up from the
    # already-fetched all_completed set so we don't widen the
    # API/dataclass surface for a UI-only display field.
    entries_by_uid: dict[str, TimeLogEntry] = {e.uid: e for e in all_completed}

    def _category_for(uid: str) -> str:
        entry = entries_by_uid.get(uid)
        return entry.category.value if entry is not None else TimeLogCategory.OTHER.value

    rows = [
        {
            "uid": s.uid,
            "Started": format_local(s.start, "%Y-%m-%d %H:%M"),
            "Label": s.label,
            "Task": s.task_title,
            "Mode": s.mode,
            "Type": _category_for(s.uid),
            "Duration (min)": round(s.duration_seconds / 60.0, 1),
        }
        for s in rollup.recent
    ]
    df = pd.DataFrame(rows)
    recent_key = "stats_recent_sessions"

    def _handle_inline_edit() -> None:
        state = st.session_state.get(recent_key) or {}
        edited_rows = state.get("edited_rows") or {}
        if not edited_rows:
            return
        for row_idx_str, changes in edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx >= len(rollup.recent):
                continue
            original = rollup.recent[row_idx]
            update = TimeLogEntryUpdate()
            touched = False
            if "Type" in changes:
                try:
                    update = update.model_copy(update={"category": TimeLogCategory(str(changes["Type"]))})
                    touched = True
                except ValueError:
                    pass
            if "Duration (min)" in changes:
                try:
                    new_minutes = float(changes["Duration (min)"])
                except (TypeError, ValueError):
                    pass
                else:
                    if new_minutes >= 0:
                        update = update.model_copy(update={"due": original.start + timedelta(minutes=new_minutes)})
                        touched = True
            if not touched:
                continue
            caldav.update_time_log_entry(original.uid, update)
        # data_editor preserves edited_rows across reruns and reapplies
        # them by row position; clear after dispatch so we don't
        # double-apply if the underlying data reorders.
        state["edited_rows"] = {}

    st.data_editor(
        df,
        column_config={
            "uid": None,
            "Started": st.column_config.TextColumn("Started", disabled=True),
            "Label": st.column_config.TextColumn("Label", width="large", disabled=True),
            "Task": st.column_config.TextColumn("Task", width="large", disabled=True),
            "Mode": st.column_config.TextColumn("Mode", disabled=True),
            "Type": st.column_config.SelectboxColumn(
                "Type",
                options=[c.value for c in TimeLogCategory],
                required=True,
            ),
            "Duration (min)": st.column_config.NumberColumn(
                "Duration (min)",
                min_value=0,
                step=1,
                format="%.0f",
            ),
        },
        hide_index=True,
        on_change=_handle_inline_edit,
        key=recent_key,
    )
