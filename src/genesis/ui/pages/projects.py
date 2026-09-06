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

from genesis.entities import ProjectStatus, TaskCreate, TaskStatus
from genesis.ui._render import (
    maybe_show_delete_confirm,
    maybe_show_project_dialog,
    maybe_show_task_dialog,
    open_project_dialog,
    open_task_dialog,
    request_delete,
    status_badge,
)
from genesis.ui._state import get_facade

facade = get_facade()
caldav = facade.caldav

maybe_show_project_dialog()
maybe_show_task_dialog()
maybe_show_delete_confirm()


# -----------------------------------------------------------------------------
# List view
# -----------------------------------------------------------------------------


def _render_list() -> None:
    st.title("Projects")
    st.caption("Long-term goals and project-specific task groups.")

    header_cols = st.columns([6, 1])
    with header_cols[1]:
        if st.button("+ New Project", key="new_project_btn", use_container_width=True):
            open_project_dialog(None)

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
                            if st.button(
                                "Open",
                                key=f"proj_open_{project.uid}",
                                use_container_width=True,
                            ):
                                st.query_params["project"] = project.uid
                                st.rerun()
                        with btn_cols[1]:
                            if st.button(
                                "Delete",
                                key=f"proj_del_{project.uid}",
                                use_container_width=True,
                            ):
                                request_delete(
                                    f"proj_{project.uid}",
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

    # Breadcrumb
    bc_cols = st.columns([1, 6])
    with bc_cols[0]:
        if st.button("← Back", key="pd_back"):
            st.query_params.pop("project", None)
            st.rerun()
    with bc_cols[1]:
        st.caption("Productivity > Projects")
        st.title(project.name)
        status_badge(project.status)

    action_cols = st.columns([1, 1, 6])
    with action_cols[0]:
        if st.button("Edit", key="pd_edit", use_container_width=True):
            open_project_dialog(uid)
    with action_cols[1]:
        if st.button("Delete", key="pd_del", use_container_width=True):
            request_delete(
                f"pd_{uid}",
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

    # Quick-add task
    with st.form(f"pd_quickadd_{uid}", clear_on_submit=True):
        st.subheader("Add task")
        fcols = st.columns([4, 2, 1])
        summary = fcols[0].text_input(
            "Summary", label_visibility="collapsed", placeholder="Task name…"
        )
        priority = fcols[1].number_input(
            "Priority", min_value=1, max_value=9, value=5, label_visibility="collapsed"
        )
        submitted = fcols[2].form_submit_button("Add", use_container_width=True)
        if submitted and summary.strip():
            caldav.create_task(
                TaskCreate(summary=summary.strip(), project_id=uid, priority=priority)
            )
            st.toast(f"Added: {summary.strip()}")
            st.rerun()

    st.write("")

    # Task list
    st.subheader("Project Tasks")
    if not tasks:
        st.info("No tasks yet.")
    else:
        for task in sorted(tasks, key=lambda t: (t.status.value, -(t.priority or 5))):
            with st.container(border=True):
                cols = st.columns([1, 6, 1])
                with cols[0]:
                    if st.button(
                        "Open", key=f"pd_open_{task.uid}", use_container_width=True
                    ):
                        open_task_dialog(task.uid)
                with cols[1]:
                    st.markdown(f"**{task.summary}**")
                    meta_bits = []
                    if task.scheduled_start:
                        meta_bits.append(f"Scheduled: {task.scheduled_start:%Y-%m-%d %H:%M}")
                    if task.hard_deadline:
                        meta_bits.append(f"Due: {task.hard_deadline:%Y-%m-%d}")
                    if task.priority:
                        meta_bits.append(f"P{task.priority}")
                    if meta_bits:
                        st.caption(" · ".join(meta_bits))
                with cols[2]:
                    status_badge(task.status)


# -----------------------------------------------------------------------------
# Dispatch
# -----------------------------------------------------------------------------


project_uid = st.query_params.get("project")

if project_uid:
    _render_detail(project_uid)
else:
    _render_list()
