"""Dashboard page: active assignments and deadlines.

Surfaces two forward-looking tasks views plus a project count:

* Active assignments: tasks whose ``assigned_date`` is today or earlier,
  so you should look at them.
* Deadlines: tasks whose ``hard_deadline`` is within the next 7 days,
  or already overdue.

Both exclude tasks with status COMPLETED or CANCELED. A task may
appear in both lists if both fields are set.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import streamlit as st

from genesis.entities import ProjectStatus, TaskStatus
from genesis.tz import to_utc
from genesis.ui._state import get_facade
from genesis.ui._table import TASK_TABLE_DEFAULT_SORT_KEYS, render_tasks_table

facade = get_facade()
caldav = facade.caldav

st.title("Dashboard")
st.caption("What's on your plate.")

# --- Compute today / week window -------------------------------------------

today = date.today()
today_start = to_utc(datetime.combine(today, time.min))
week_end_dt = today_start + timedelta(days=7)

# --- Fetch tasks once, derive both views -----------------------------------

all_tasks = caldav.list_tasks()
done_statuses = {TaskStatus.COMPLETED, TaskStatus.CANCELED}

# Active assignments: assigned_date <= today, not done/canceled.
# The table widget owns the sort (cache-based, ADR-023).
active_assignments = [
    t for t in all_tasks if t.assigned_date is not None and t.assigned_date <= today and t.status not in done_statuses
]

# Deadlines: hard_deadline within 7 days or already overdue.
# The table widget owns the sort (cache-based, ADR-023).
deadlines = [
    t
    for t in all_tasks
    if t.hard_deadline is not None and t.hard_deadline <= week_end_dt and t.status not in done_statuses
]

active_projects = caldav.list_projects(status=ProjectStatus.IN_PROGRESS)

# --- Metric cards ----------------------------------------------------------

cols = st.columns(3, gap="medium")
with cols[0]:
    with st.container(border=True):
        st.caption("Active assignments")
        st.markdown(f"## {len(active_assignments)}")
        st.caption("Assigned today or earlier")
with cols[1]:
    with st.container(border=True):
        st.caption("Deadlines this week")
        st.markdown(f"## {len(deadlines)}")
        st.caption("Due in the next 7 days or overdue")
with cols[2]:
    with st.container(border=True):
        st.caption("Active projects")
        st.markdown(f"## {len(active_projects)}")
        st.caption("Projects currently in progress")

st.divider()

# --- Active assignments -----------------------------------------------------

st.subheader(f"Active assignments ({len(active_assignments)})")
st.caption("Assigned today or earlier.")
if not active_assignments:
    st.info("No overdue or today assignments.")
else:
    render_tasks_table(
        active_assignments,
        caldav=caldav,
        key="dashboard_assignments",
        show_project_column=True,
        sort_keys=TASK_TABLE_DEFAULT_SORT_KEYS,
    )

st.write("")

# --- Deadlines --------------------------------------------------------------

st.subheader(f"Deadlines ({len(deadlines)})")
st.caption("Due in the next 7 days or already overdue.")
if not deadlines:
    st.info("Nothing due in the next 7 days.")
else:
    render_tasks_table(
        deadlines,
        caldav=caldav,
        key="dashboard_deadlines",
        show_project_column=True,
        sort_keys=TASK_TABLE_DEFAULT_SORT_KEYS,
    )
