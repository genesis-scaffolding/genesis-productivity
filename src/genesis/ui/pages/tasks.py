"""Tasks page: global list with quick-add and per-row dialog actions."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from genesis.entities import TaskCreate, TaskStatus
from genesis.ui._render import (
    maybe_show_delete_confirm,
    maybe_show_task_dialog,
    open_task_dialog,
    request_delete,
)
from genesis.ui._state import get_facade

facade = get_facade()
caldav = facade.caldav

maybe_show_task_dialog()
maybe_show_delete_confirm()

st.title("Tasks")
st.caption("Your global backlog and scheduled work.")

# --- Quick add (inline form) -----------------------------------------------

with st.form("tasks_quickadd", clear_on_submit=True):
    fcols = st.columns([6, 2, 1])
    summary = fcols[0].text_input("Summary", label_visibility="collapsed", placeholder="Task name…")
    priority = fcols[1].number_input("Priority", min_value=1, max_value=9, value=5, label_visibility="collapsed")
    submitted = fcols[2].form_submit_button("Add", use_container_width=True)
    if submitted and summary.strip():
        caldav.create_task(TaskCreate(summary=summary.strip(), priority=priority))
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

# --- Load and render --------------------------------------------------------

tasks = caldav.list_tasks()
if active_statuses:
    tasks = [t for t in tasks if t.status.value in active_statuses]

# Build dataframe for the data editor
projects = {p.uid: p.name for p in caldav.list_projects()}
rows: list[dict] = []
for t in tasks:
    rows.append(
        {
            "uid": t.uid,
            "Summary": t.summary,
            "Project": projects.get(t.project_id, "—") if t.project_id else "—",
            "Priority": t.priority or 5,
            "Scheduled": t.scheduled_start,
            "Deadline": t.hard_deadline,
            "Status": t.status.value,
        }
    )
df = pd.DataFrame(rows)

if df.empty:
    st.info("No tasks match the current filter.")
else:
    edited = st.data_editor(
        df,
        column_config={
            "uid": None,  # hidden
            "Summary": st.column_config.TextColumn("Summary", width="large"),
            "Project": st.column_config.TextColumn("Project", disabled=True),
            "Priority": st.column_config.NumberColumn("Priority", min_value=1, max_value=9),
            "Scheduled": st.column_config.DatetimeColumn(
                "Scheduled", format="YYYY-MM-DD HH:mm", disabled=True
            ),
            "Deadline": st.column_config.DatetimeColumn(
                "Deadline", format="YYYY-MM-DD", disabled=True
            ),
            "Status": st.column_config.SelectboxColumn(
                "Status",
                options=statuses_all,
                required=True,
            ),
        },
        hide_index=True,
        use_container_width=True,
        key="tasks_editor",
    )

    # Apply inline edits: status, summary, priority
    if edited is not None:
        edited_rows = edited.to_dict(orient="records")
        for row in edited_rows:
            uid = str(row["uid"])
            orig = next((t for t in tasks if t.uid == uid), None)
            if orig is None:
                continue
            new_status = TaskStatus(str(row["Status"]))
            new_summary = str(row["Summary"])
            new_prio = int(row["Priority"])
            changed = (
                new_status != orig.status
                or new_summary != orig.summary
                or new_prio != (orig.priority or 5)
            )
            if not changed:
                continue
            from genesis.entities import TaskUpdate

            caldav.update_task(
                uid,
                TaskUpdate(
                    summary=new_summary,
                    priority=new_prio,
                    status=new_status,
                ),
            )
            st.rerun()

# --- Row actions: edit / delete --------------------------------------------

if not df.empty:
    st.divider()
    st.subheader("Actions")
    for t in tasks:
        c = st.columns([6, 1, 1])
        with c[0]:
            st.markdown(f"**{t.summary}**")
        with c[1]:
            if st.button("Edit", key=f"tasks_edit_{t.uid}", use_container_width=True):
                open_task_dialog(t.uid)
        with c[2]:
            if st.button("Delete", key=f"tasks_del_{t.uid}", use_container_width=True):
                request_delete(
                    f"tasks_{t.uid}",
                    f"task '{t.summary}'",
                    lambda uid=t.uid: caldav.delete_task(uid),
                )
