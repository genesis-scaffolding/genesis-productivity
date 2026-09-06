"""Tasks page: global list with full-featured inline quick-add.

The ``st.data_editor`` is supplied by ``render_tasks_table`` from
``_table``. Inline edits (Summary, Priority, Scheduled, Deadline,
Assigned, Status) and per-row Edit/Delete buttons all live inside
the shared component.
"""

from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from genesis.entities import TaskCreate, TaskStatus
from genesis.ui._render import (
    maybe_show_delete_confirm,
    maybe_show_task_dialog,
)
from genesis.ui._state import get_facade
from genesis.ui._table import render_tasks_table

facade = get_facade()
caldav = facade.caldav

maybe_show_task_dialog()
maybe_show_delete_confirm()

st.title("Tasks")
st.caption("Your global backlog and scheduled work.")

# --- Full-featured quick add (inline form) ---------------------------------

with st.form("tasks_quickadd", clear_on_submit=True):
    summary = st.text_input("Summary", placeholder="Task name…")
    description = st.text_area(
        "Description", placeholder="Optional details…", height=100
    )

    toggle_cols = st.columns(3)
    has_schedule = toggle_cols[0].checkbox("Schedule on calendar")
    has_deadline = toggle_cols[1].checkbox("Set hard deadline")
    has_assigned = toggle_cols[2].checkbox("Assign to a day")

    scheduled_start = scheduled_duration = hard_deadline = assigned_date = None
    if has_schedule:
        sched_row = st.columns([3, 1])
        scheduled_start = sched_row[0].datetime_input(
            "Start", value=datetime.now()
        )
        scheduled_duration = sched_row[1].number_input(
            "Duration (min)", min_value=5, max_value=480, value=30
        )
    if has_deadline:
        hard_deadline = st.datetime_input("Deadline", value=datetime.now())
    if has_assigned:
        assigned_date = st.date_input("Assigned", value=date.today())

    action_cols = st.columns([1, 5, 1])
    priority = action_cols[0].number_input(
        "Priority", min_value=1, max_value=9, value=5
    )
    submitted = action_cols[2].form_submit_button("Add")

    if submitted and summary.strip():
        kwargs: dict = {
            "summary": summary.strip(),
            "description": description or None,
            "priority": priority,
        }
        if scheduled_start is not None:
            kwargs["scheduled_start"] = scheduled_start
        if scheduled_duration is not None:
            kwargs["scheduled_duration"] = scheduled_duration
        if hard_deadline is not None:
            kwargs["hard_deadline"] = hard_deadline
        if assigned_date is not None:
            kwargs["assigned_date"] = assigned_date
        caldav.create_task(TaskCreate(**kwargs))
        st.toast(f"Added: {summary.strip()}")
        st.rerun()

st.write("")

# --- Filter chips -----------------------------------------------------------

statuses_all = [s.value for s in TaskStatus]
active_statuses = st.session_state.get("tasks_filter_statuses", list(statuses_all))
active_statuses = st.pills(
    "Status",
    options=statuses_all,
    default=active_statuses,
    selection_mode="multi",
    key="tasks_filter_statuses",
)

st.write("")

# --- Render table -----------------------------------------------------------

tasks = caldav.list_tasks()
if active_statuses:
    selected = {TaskStatus(v) for v in active_statuses}
    tasks = [t for t in tasks if t.status in selected]

render_tasks_table(tasks, caldav=caldav, key="global_tasks")
