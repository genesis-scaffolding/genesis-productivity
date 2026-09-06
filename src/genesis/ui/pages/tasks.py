"""Tasks page: global list with quick-add and inline edit table.

The ``st.data_editor`` is the single source of truth for task rows.
Inline edits (Summary, Priority, Scheduled, Deadline, Status) flow
through the ``on_change`` callback which reads
``st.session_state["tasks_editor"]["edited_rows"]``. Per-row Open and
Delete buttons live in the same table via ``ButtonColumn``.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from genesis.entities import TaskCreate, TaskStatus, TaskUpdate
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
    summary = fcols[0].text_input(
        "Summary", label_visibility="collapsed", placeholder="Task name…"
    )
    priority = fcols[1].number_input(
        "Priority", min_value=1, max_value=9, value=5, label_visibility="collapsed"
    )
    submitted = fcols[2].form_submit_button("Add")
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

# Build dataframe for the data editor. The uid is hidden but needed
# by the inline-edit handler to look up the task by row position.
projects = {p.uid: p.name for p in caldav.list_projects()}
rows: list[dict] = []
for t in tasks:
    rows.append(
        {
            "uid": t.uid,
            "Summary": t.summary,
            "Project": projects.get(t.project_id, "—") if t.project_id else "—",
            "Priority": t.priority if t.priority is not None else 5,
            "Scheduled": t.scheduled_start,
            "Deadline": t.hard_deadline,
            "Status": t.status.value,
            "actions": ["Edit", "Delete"],
        }
    )
df = pd.DataFrame(rows)


def _handle_action() -> None:
    """Row action (Edit / Delete) handler.

    Reads the click info stashed by the ButtonColumn callback and
    dispatches to the dialog helper or the delete-confirm helper.
    """
    click = st.session_state.get("task_action")
    if not click:
        return
    row_idx = click["row"]
    label = click["label"]
    if row_idx >= len(tasks):
        return
    target = tasks[row_idx]
    if label == "Edit":
        open_task_dialog(target.uid)
    elif label == "Delete":
        request_delete(
            f"tasks_{target.uid}",
            f"task '{target.summary}'",
            lambda uid=target.uid: caldav.delete_task(uid),
        )


def _handle_inline_edit() -> None:
    """Apply inline cell edits to the backend.

    Reads the data editor's ``edited_rows`` and issues a single
    ``update_task`` per changed row covering all changed columns.
    A rerun reloads the table from the backend so the next render
    reflects the saved state and the in-flight edits clear.
    """
    state = st.session_state.get("tasks_editor") or {}
    edited_rows = state.get("edited_rows") or {}
    if not edited_rows:
        return
    any_changed = False
    for row_idx_str, changes in edited_rows.items():
        row_idx = int(row_idx_str)
        if row_idx >= len(tasks):
            continue
        original = tasks[row_idx]

        update: TaskUpdate = TaskUpdate()
        touched = False
        if "Summary" in changes:
            update = update.model_copy(update={"summary": str(changes["Summary"])})
            touched = True
        if "Priority" in changes:
            update = update.model_copy(update={"priority": int(changes["Priority"])})
            touched = True
        if "Status" in changes:
            update = update.model_copy(
                update={"status": TaskStatus(str(changes["Status"]))}
            )
            touched = True
        if "Scheduled" in changes:
            new_val = changes["Scheduled"]
            scheduled = (
                None
                if pd.isna(new_val)
                else pd.Timestamp(new_val).to_pydatetime()
            )
            update = update.model_copy(update={"scheduled_start": scheduled})
            touched = True
        if "Deadline" in changes:
            new_val = changes["Deadline"]
            deadline = (
                None
                if pd.isna(new_val)
                else pd.Timestamp(new_val).to_pydatetime()
            )
            update = update.model_copy(update={"hard_deadline": deadline})
            touched = True

        if not touched:
            continue
        caldav.update_task(original.uid, update)
        any_changed = True
    if any_changed:
        st.rerun()


if df.empty:
    st.info("No tasks match the current filter.")
else:
    st.data_editor(
        df,
        column_config={
            "uid": None,  # hidden
            "Summary": st.column_config.TextColumn("Summary", width="large"),
            "Project": st.column_config.TextColumn("Project", disabled=True),
            "Priority": st.column_config.NumberColumn(
                "Priority", min_value=1, max_value=9
            ),
            "Scheduled": st.column_config.DatetimeColumn(
                "Scheduled", format="YYYY-MM-DD HH:mm"
            ),
            "Deadline": st.column_config.DatetimeColumn(
                "Deadline", format="YYYY-MM-DD"
            ),
            "Status": st.column_config.SelectboxColumn(
                "Status",
                options=statuses_all,
                required=True,
            ),
            "actions": st.column_config.ButtonColumn(
                "Actions",
                on_click=_handle_action,
                key="task_action",
            ),
        },
        hide_index=True,
        on_change=_handle_inline_edit,
        key="tasks_editor",
    )
