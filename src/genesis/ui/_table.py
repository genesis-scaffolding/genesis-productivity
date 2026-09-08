"""Shared Streamlit data widgets for the genesis UI.

This module hosts the canonical ``tasks table`` so it has a single
source of truth. Page modules pass in their filtered task list and
a unique widget-key prefix; the table handles inline edits, per-row
Edit/Delete actions, and the empty-state message.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

from genesis.entities import Task, TaskStatus, TaskUpdate
from genesis.tz import to_local, to_utc
from genesis.ui._render import delete_confirm_dialog, task_edit_dialog

if TYPE_CHECKING:
    from genesis.caldav import CalDAVFacade


def render_tasks_table(
    tasks: list[Task],
    *,
    caldav: CalDAVFacade,
    key: str,
    show_project_column: bool = True,
) -> None:
    """Render the canonical tasks table.

    Args:
        tasks: Tasks to display (caller pre-filters by status / project).
        caldav: Facade used for ``update_task`` and ``delete_task``.
        key: Unique widget-key prefix. The data editor stores its
            state under ``st.session_state[key]``; the row-action
            click info lands under ``st.session_state[f"{key}_action"]``.
        show_project_column: Show the project name column. Pass
            ``False`` on project-detail pages where every row belongs
            to the same project.

    Inline edits to Title, Priority, Scheduled, Deadline, Assigned,
    Status, and Project flow through ``on_change``. The on-change
    handler reads ``st.session_state[key]["edited_rows"]`` and
    dispatches a single ``update_task`` per changed row covering all
    changed columns. The Project column is a SelectboxColumn so users
    can reassign or unassign tasks without opening the edit dialog;
    Actions (Edit / Delete) is the last column.
    """
    if not tasks:
        st.info("No tasks to show.")
        return

    statuses_all = [s.value for s in TaskStatus]

    projects_list = (
        list(caldav.list_projects()) if show_project_column else []
    )
    project_label_to_uid: dict[str, str | None] = {"(none)": None}
    for p in projects_list:
        project_label_to_uid[p.name] = p.uid
    project_labels = ["(none)"] + [p.name for p in projects_list]

    def _project_label(project_id: str | None) -> str:
        if project_id is None:
            return "(none)"
        for p in projects_list:
            if p.uid == project_id:
                return p.name
        return "(missing)"

    rows: list[dict] = []
    for t in tasks:
        # Strip tz for the dataframe; on edit, the value comes back as
        # naive local and we convert via to_utc before storing.
        sched = to_local(t.scheduled_start) if t.scheduled_start else None
        dl = to_local(t.hard_deadline) if t.hard_deadline else None
        row: dict = {
            "uid": t.uid,
            "Title": t.title,
            "Priority": t.priority if t.priority is not None else 5,
            "Scheduled": sched.replace(tzinfo=None) if sched else None,
            "Deadline": dl.replace(tzinfo=None) if dl else None,
            "Assigned": t.assigned_date,
            "Status": t.status.value,
        }
        if show_project_column:
            row["Project"] = _project_label(t.project_id)
        row["actions"] = ["Edit", "Delete"]
        rows.append(row)
    df = pd.DataFrame(rows)

    action_key = f"{key}_action"
    table_tasks = tasks

    def _handle_action() -> None:
        click = st.session_state.get(action_key)
        if not click:
            return
        row_idx = click["row"]
        label = click["label"]
        if row_idx >= len(table_tasks):
            return
        target = table_tasks[row_idx]
        if label == "Edit":
            task_edit_dialog(target.uid)
        elif label == "Delete":
            delete_confirm_dialog(
                f"task '{target.title}'",
                lambda uid=target.uid: caldav.delete_task(uid),
            )

    def _handle_inline_edit() -> None:
        state = st.session_state.get(key) or {}
        edited_rows = state.get("edited_rows") or {}
        if not edited_rows:
            return
        for row_idx_str, changes in edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx >= len(table_tasks):
                continue
            original = table_tasks[row_idx]

            update: TaskUpdate = TaskUpdate()
            touched = False
            if "Title" in changes:
                update = update.model_copy(
                    update={"title": str(changes["Title"])}
                )
                touched = True
            if "Priority" in changes:
                update = update.model_copy(
                    update={"priority": int(changes["Priority"])}
                )
                touched = True
            if "Status" in changes:
                update = update.model_copy(
                    update={"status": TaskStatus(str(changes["Status"]))}
                )
                touched = True
            if "Scheduled" in changes:
                new_val = changes["Scheduled"]
                scheduled_ts = pd.Timestamp(new_val)
                if pd.isna(scheduled_ts):
                    scheduled: object = None
                else:
                    py_dt = scheduled_ts.to_pydatetime()
                    scheduled = to_utc(py_dt) if isinstance(py_dt, datetime) else None
                update = update.model_copy(update={"scheduled_start": scheduled})
                touched = True
            if "Deadline" in changes:
                new_val = changes["Deadline"]
                deadline_ts = pd.Timestamp(new_val)
                if pd.isna(deadline_ts):
                    deadline: object = None
                else:
                    py_dt = deadline_ts.to_pydatetime()
                    deadline = to_utc(py_dt) if isinstance(py_dt, datetime) else None
                update = update.model_copy(update={"hard_deadline": deadline})
                touched = True
            if "Assigned" in changes:
                new_val = changes["Assigned"]
                assigned: object = (
                    None if pd.isna(new_val) else pd.Timestamp(new_val).date()
                )
                update = update.model_copy(update={"assigned_date": assigned})
                touched = True
            if "Project" in changes and show_project_column:
                new_label = str(changes["Project"])
                if new_label in project_label_to_uid:
                    new_uid = project_label_to_uid[new_label]
                    if new_uid is None:
                        update = update.model_copy(
                            update={"clear_project_id": True}
                        )
                    else:
                        update = update.model_copy(
                            update={"project_id": new_uid}
                        )
                    touched = True

            if not touched:
                continue
            caldav.update_task(original.uid, update)
        # Clear in-flight edits so they are not re-applied to the new
        # df on the next render. data_editor preserves edited_rows in
        # widget state and reapplies them to row positions on each
        # render; if the underlying data reorders between renders
        # (Radicale's PROPFIND order is not stable across PUTs), the
        # preserved edits would land on the wrong rows. Clearing here
        # after we've already dispatched the updates prevents that.
        state["edited_rows"] = {}
        # No explicit st.rerun() — this is an on_change callback.
        # Streamlit's natural rerun after on_change picks up the updates.

    column_config: dict = {
        "uid": None,  # hidden
        "Title": st.column_config.TextColumn("Title", width="large"),
        "Priority": st.column_config.NumberColumn(
            "Priority", min_value=1, max_value=9
        ),
        "Scheduled": st.column_config.DatetimeColumn(
            "Scheduled", format="YYYY-MM-DD HH:mm"
        ),
        "Deadline": st.column_config.DatetimeColumn(
            "Deadline", format="YYYY-MM-DD"
        ),
        "Assigned": st.column_config.DateColumn(
            "Assigned", format="YYYY-MM-DD"
        ),
        "Status": st.column_config.SelectboxColumn(
            "Status", options=statuses_all, required=True
        ),
    }
    if show_project_column:
        column_config["Project"] = st.column_config.SelectboxColumn(
            "Project",
            options=project_labels,
            required=False,
            help=(
                "Reassign task to another project. "
                "Select (none) to unassign."
            ),
        )
    else:
        column_config["Project"] = None
    column_config["actions"] = st.column_config.ButtonColumn(
        "Actions", on_click=_handle_action, key=action_key
    )

    st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        on_change=_handle_inline_edit,
        key=key,
    )
