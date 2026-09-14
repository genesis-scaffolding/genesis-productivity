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
from genesis.ui._render import task_create_dialog
from genesis.ui._state import get_facade
from genesis.ui._table import render_filterable_tasks_table

facade = get_facade()
caldav = facade.caldav

st.title("Tasks")
st.caption("Your global backlog and scheduled work.")
if st.button("+ Add Task", key="global_add_task_btn"):
    task_create_dialog()

st.divider()

tasks = caldav.list_tasks()
render_filterable_tasks_table(
    tasks,
    caldav=caldav,
    key="global_tasks",
    filter_key="tasks_filter_statuses",
    default_statuses=[TaskStatus.NEEDS_ACTION, TaskStatus.IN_PROGRESS],
)
