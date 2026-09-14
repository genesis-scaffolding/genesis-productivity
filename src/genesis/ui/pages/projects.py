"""Projects page: list view (default) and project detail (when ?project=<uid>).

The page renders one of two layouts based on the ``project`` query
param. The list view groups project cards by status; the detail view
shows progress, task list, quick-add, edit, and delete. The detail
view is reached by setting ``?project=<uid>`` and re-running, not
via ``st.switch_page`` — that keeps the detail view off the sidebar
while still being reachable from the list.
"""

from __future__ import annotations

import streamlit as st

from genesis.entities import ProjectStatus, TaskStatus
from genesis.ui._render import (
    delete_confirm_dialog,
    project_create_dialog,
    project_edit_dialog,
    status_badge,
    task_create_dialog,
)
from genesis.ui._state import get_facade
from genesis.ui._table import render_filterable_tasks_table

facade = get_facade()
caldav = facade.caldav


# -----------------------------------------------------------------------------
# List view
# -----------------------------------------------------------------------------


def _render_list() -> None:
    st.title("Projects")
    st.caption("Long-term goals and project-specific task groups.")

    if st.button("+ New Project", key="new_project_btn"):
        project_create_dialog()

    projects = caldav.list_projects(sort_by=["status", "name"])
    groups: dict[ProjectStatus, list] = {}
    for p in projects:
        groups.setdefault(p.status, []).append(p)

    SECTION_LABELS = {
        ProjectStatus.IN_PROGRESS: "In Progress",
        ProjectStatus.NEEDS_ACTION: "To Do",
        ProjectStatus.COMPLETED: "Completed",
        ProjectStatus.CANCELED: "Canceled",
    }

    for status in [
        ProjectStatus.IN_PROGRESS,
        ProjectStatus.NEEDS_ACTION,
        ProjectStatus.COMPLETED,
        ProjectStatus.CANCELED,
    ]:
        items = groups.get(status, [])
        if not items:
            continue
        st.subheader(f"{SECTION_LABELS[status]} ({len(items)})")
        PER_ROW = 3
        for row_start in range(0, len(items), PER_ROW):
            row = items[row_start : row_start + PER_ROW]
            cols = st.columns(PER_ROW, gap="medium")
            for col, project in zip(cols, row, strict=False):
                with col:
                    with st.container(border=True):
                        st.markdown(f"### {project.name}")
                        if project.description:
                            st.caption(project.description[:160])
                        status_badge(project.status)
                        if project.deadline:
                            st.caption(f"Deadline: {project.deadline}")
                        btn_cols = st.columns(2)
                        with btn_cols[0]:
                            if st.button("Open", key=f"proj_open_{project.uid}"):
                                st.query_params["project"] = project.uid
                                st.rerun()
                        with btn_cols[1]:
                            if st.button("Delete", key=f"proj_del_{project.uid}"):
                                delete_confirm_dialog(
                                    f"project '{project.name}'",
                                    lambda uid=project.uid: caldav.delete_project(uid),
                                )
        st.write("")


# -----------------------------------------------------------------------------
# Detail view
# -----------------------------------------------------------------------------


def _render_detail(uid: str) -> None:
    project = caldav.get_project(uid)
    if project is None:
        st.error("Project not found.")
        if st.button("Back to Projects"):
            st.query_params.pop("project", None)
            st.rerun()
        return

    # Back button on its own row
    back_col, _ = st.columns([1, 8])
    with back_col:
        if st.button("← Back", key="pd_back"):
            st.query_params.pop("project", None)
            st.rerun()

    # Breadcrumb + title + actions on the next row.
    st.caption("Productivity > Projects")
    header_cols = st.columns([8, 1, 1])
    with header_cols[0]:
        st.title(project.name)
        status_badge(project.status)
    with header_cols[1]:
        if st.button("Edit", key="pd_edit"):
            project_edit_dialog(uid)
    with header_cols[2]:
        if st.button("Delete", key="pd_del"):
            delete_confirm_dialog(
                f"project '{project.name}'",
                lambda: caldav.delete_project(uid),
            )

    if project.description:
        st.write(project.description)

    st.write("")

    # Metrics
    tasks = caldav.list_tasks(project_id=uid)
    total = len(tasks)
    open_count = sum(1 for t in tasks if t.status != TaskStatus.COMPLETED)
    completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
    progress = (completed / total * 100.0) if total else 0.0

    mcols = st.columns(4, gap="medium")
    with mcols[0]:
        with st.container(border=True):
            st.caption("Progress")
            st.markdown(f"## {progress:.0f}%")
            st.progress(progress / 100.0)
    with mcols[1]:
        with st.container(border=True):
            st.caption("Tasks")
            st.markdown(f"## {total}")
    with mcols[2]:
        with st.container(border=True):
            st.caption("Open")
            st.markdown(f"## {open_count}")
    with mcols[3]:
        with st.container(border=True):
            st.caption("Deadline")
            if project.deadline:
                st.markdown(f"## {project.deadline}")
            else:
                st.markdown("## None")

    st.write("")

    # Task list header with "+ Add Task" button (pre-attached to this project)
    st.subheader("Project Tasks")
    if st.button("+ Add Task", key=f"pd_add_task_{uid}"):
        task_create_dialog(project_id=uid)

    sorted_tasks = sorted(tasks, key=lambda t: (t.status.value, -(t.priority or 5)))
    render_filterable_tasks_table(
        sorted_tasks,
        caldav=caldav,
        key=f"project_tasks_{uid}",
        show_project_column=False,
        filter_key=f"pd_filter_statuses_{uid}",
        default_statuses=[TaskStatus.NEEDS_ACTION, TaskStatus.IN_PROGRESS],
    )


# -----------------------------------------------------------------------------
# Dispatch
# -----------------------------------------------------------------------------


project_uid = st.query_params.get("project")

if project_uid:
    _render_detail(project_uid)
else:
    _render_list()
