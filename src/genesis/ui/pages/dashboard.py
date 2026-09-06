"""Dashboard page: today's tasks, active project count, today's task list."""

from __future__ import annotations

from datetime import date, datetime, time

import streamlit as st

from genesis.entities import ProjectStatus
from genesis.ui._render import status_badge
from genesis.ui._state import get_facade

facade = get_facade()
caldav = facade.caldav
maybe_show_task_dialog = st.session_state.get("__noop__")  # placeholder

st.title("Dashboard")

# --- Cards: today + active projects -----------------------------------------

all_tasks = caldav.list_tasks()
all_projects = caldav.list_projects()
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
active_projects = [p for p in all_projects if p.status == ProjectStatus.IN_PROGRESS]

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

# --- Today's task list -------------------------------------------------------

st.subheader("Your day")
if not today_tasks:
    st.info("Nothing scheduled for today. Use the Tasks page to add something.")
else:
    for task in sorted(
        today_tasks, key=lambda t: t.scheduled_start or datetime.max
    ):
        with st.container(border=True):
            cols = st.columns([1, 6, 2, 2])
            with cols[0]:
                if st.button("Open", key=f"dash_open_{task.uid}", use_container_width=True):
                    st.session_state["open_task_dialog"] = task.uid
                    st.rerun()
            with cols[1]:
                st.markdown(f"**{task.summary}**")
                if task.description:
                    st.caption(task.description[:200])
            with cols[2]:
                if task.scheduled_start:
                    st.caption(f"Scheduled: {task.scheduled_start:%H:%M}")
                elif task.assigned_date:
                    st.caption(f"Assigned: {task.assigned_date}")
                elif task.hard_deadline:
                    st.caption(f"Due: {task.hard_deadline:%Y-%m-%d}")
            with cols[3]:
                status_badge(task.status)


# --- Open any pending task dialog from the "Open" buttons above -------------
if "open_task_dialog" in st.session_state:
    from genesis.ui._render import _edit_task_dialog

    uid = st.session_state.pop("open_task_dialog")
    _edit_task_dialog(uid)
