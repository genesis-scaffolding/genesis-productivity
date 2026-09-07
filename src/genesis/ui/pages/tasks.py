"""Tasks page: global list with "+ Add Task" dialog.

The ``st.data_editor`` is supplied by ``render_tasks_table`` from
``_table``. Inline edits (Summary, Priority, Scheduled, Deadline,
Assigned, Status) and per-row Edit/Delete buttons all live inside
the shared component. Adding a new task is done via the dialog
opened by the "+ Add Task" button in the header.
"""

from __future__ import annotations

import streamlit as st

from genesis.entities import TaskStatus
from genesis.ui._render import (
    maybe_show_delete_confirm,
    maybe_show_task_dialog,
    open_task_dialog,
)
from genesis.ui._state import get_facade
from genesis.ui._table import render_tasks_table

facade = get_facade()
caldav = facade.caldav

maybe_show_task_dialog()
maybe_show_delete_confirm()

# --- Header with title and "+ Add Task" button -----------------------------

st.title("Tasks")
st.caption("Your global backlog and scheduled work.")
if st.button("+ Add Task", key="global_add_task_btn"):
    open_task_dialog()

st.divider()

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
