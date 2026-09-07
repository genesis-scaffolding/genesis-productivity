"""Dashboard page: today's tasks, active project count, today's task list."""

from __future__ import annotations

from datetime import date, datetime, time

import streamlit as st

from genesis.entities import ProjectStatus
from genesis.ui._render import maybe_show_task_dialog
from genesis.ui._state import get_facade
from genesis.ui._table import render_tasks_table

facade = get_facade()
caldav = facade.caldav

maybe_show_task_dialog()

st.title("Dashboard")

# --- Cards: today + active projects -----------------------------------------

all_tasks = caldav.list_tasks()
today = date.today()
today_start = datetime.combine(today, time.min)
today_end = datetime.combine(today, time.max)

today_tasks = [
    t
    for t in all_tasks
    if (t.scheduled_start and today_start <= t.scheduled_start <= today_end)
    or (t.assigned_date == today)
    or (t.hard_deadline and today_start <= t.hard_deadline <= today_end)
]
# Server-side STATUS filter for the active-projects count.
active_projects = caldav.list_projects(status=ProjectStatus.IN_PROGRESS)

cols = st.columns(2, gap="medium")
with cols[0]:
    with st.container(border=True):
        st.subheader("Today")
        st.markdown(f"## {len(today_tasks)} Tasks")
        st.caption("Due today, scheduled today, or assigned today")
with cols[1]:
    with st.container(border=True):
        st.subheader("Active Projects")
        st.markdown(f"## {len(active_projects)}")
        st.caption("Projects currently in progress")

st.divider()

# --- Today's task list via the shared tasks table --------------------------

st.subheader("Your day")
if not today_tasks:
    st.info("Nothing scheduled for today. Use the Tasks page to add something.")
else:
    sorted_today = sorted(
        today_tasks, key=lambda t: t.scheduled_start or datetime.max
    )
    render_tasks_table(
        sorted_today, caldav=caldav, key="dashboard_today_tasks",
        show_project_column=True,
    )
