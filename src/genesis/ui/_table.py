"""Shared Streamlit data widgets for the genesis UI.

This module hosts the canonical ``tasks table`` so it has a single
source of truth. Page modules pass in their filtered task list and
a unique widget-key prefix; the table handles inline edits, per-row
Edit/Delete actions, and the empty-state message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

from genesis.entities import Task, TaskStatus, TaskUpdate
from genesis.ui._render import open_task_dialog, request_delete

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

    Inline edits to Summary, Priority, Scheduled, Deadline, Assigned,
    and Status flow through ``on_change``. The on-change handler reads
    ``st.session_state[key]["edited_rows"]`` and dispatches a single
    ``update_task`` per changed row covering all changed columns.
    """
    if not tasks:
        st.info("No tasks to show.")
        return

    statuses_all = [s.value for s in TaskStatus]

    projects: dict[str, str] = (
        {p.uid: p.name for p in caldav.list_projects()}
        if show_project_column
        else {}
    )

    rows: list[dict] = []
    for t in tasks:
        row: dict = {
            "uid": t.uid,
            "Summary": t.summary,
            "Priority": t.priority if t.priority is not None else 5,
            "Scheduled": t.scheduled_start,
            "Deadline": t.hard_deadline,
            "Assigned": t.assigned_date,
            "Status": t.status.value,
            "actions": ["Edit", "Delete"],
        }
        if show_project_column:
            row["Project"] = (
                projects.get(t.project_id, "—") if t.project_id else "—"
            )
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
            open_task_dialog(target.uid)
        elif label == "Delete":
            request_delete(
                f"{key}_{target.uid}",
                f"task '{target.summary}'",
                lambda uid=target.uid: caldav.delete_task(uid),
            )

    def _handle_inline_edit() -> None:
        state = st.session_state.get(key) or {}
        edited_rows = state.get("edited_rows") or {}
        if not edited_rows:
            return
        any_changed = False
        for row_idx_str, changes in edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx >= len(table_tasks):
                continue
            original = table_tasks[row_idx]

            update: TaskUpdate = TaskUpdate()
            touched = False
            if "Summary" in changes:
                update = update.model_copy(
                    update={"summary": str(changes["Summary"])}
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
                scheduled: object = (
                    None
                    if pd.isna(new_val)
                    else pd.Timestamp(new_val).to_pydatetime()
                )
                update = update.model_copy(update={"scheduled_start": scheduled})
                touched = True
            if "Deadline" in changes:
                new_val = changes["Deadline"]
                deadline: object = (
                    None
                    if pd.isna(new_val)
                    else pd.Timestamp(new_val).to_pydatetime()
                )
                update = update.model_copy(update={"hard_deadline": deadline})
                touched = True
            if "Assigned" in changes:
                new_val = changes["Assigned"]
                assigned: object = (
                    None if pd.isna(new_val) else pd.Timestamp(new_val).date()
                )
                update = update.model_copy(update={"assigned_date": assigned})
                touched = True

            if not touched:
                continue
            caldav.update_task(original.uid, update)
            any_changed = True
        if any_changed:
            st.rerun()

    column_config: dict = {
        "uid": None,  # hidden
        "Summary": st.column_config.TextColumn("Summary", width="large"),
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
        "actions": st.column_config.ButtonColumn(
            "Actions", on_click=_handle_action, key=action_key
        ),
    }
    if show_project_column:
        column_config["Project"] = st.column_config.TextColumn(
            "Project", disabled=True
        )
    else:
        column_config["Project"] = None

    st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        on_change=_handle_inline_edit,
        key=key,
    )
