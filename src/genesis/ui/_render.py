"""Shared Streamlit widgets and dialogs for the genesis UI.

Page modules call these to render consistent status pills, project cards,
and edit dialogs. The dialogs are streamlit-decorated functions: pages
set a trigger in ``st.session_state`` and call the dialog function on
the next run; the dialog handles its own Save/Cancel and calls
``st.rerun()`` to dismiss itself.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any

import streamlit as st

from genesis.ui._state import get_facade

# -----------------------------------------------------------------------------
# Status badges
# -----------------------------------------------------------------------------


_STATUS_COLOR: dict[str, str] = {
    "needs-action": "gray",
    "in-progress": "blue",
    "completed": "green",
    "canceled": "red",
    "tentative": "orange",
    "confirmed": "blue",
}


def status_badge(status: StrEnum | str) -> None:
    """Render a status enum as a Streamlit badge.

    Falls back to gray for unknown values. Accepts either an enum member
    or a raw string; in the latter case the string is used verbatim.
    """
    raw = status.value if isinstance(status, StrEnum) else str(status)
    color = _STATUS_COLOR.get(raw, "gray")
    st.badge(raw, color=color)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# Form helpers
# -----------------------------------------------------------------------------


def _form_key(prefix: str, uid: str) -> str:
    """Stable widget key for a given (form, entity) pair."""
    return f"{prefix}_{uid or 'new'}"


@st.dialog("New task")
def _create_task_dialog() -> None:
    """Internal: create-task dialog body."""
    facade = get_facade()
    projects = facade.caldav.list_projects()
    project_options = [(p.uid, p.name) for p in projects]
    project_labels = ["(none)"] + [name for _, name in project_options]
    project_uids = [None] + [uid for uid, _ in project_options]

    summary = st.text_input("Summary", key="_ctd_summary")
    description = st.text_area("Description", key="_ctd_description", height=120)
    project_label = st.selectbox(
        "Project", project_labels, key="_ctd_project", index=0
    )
    project_uid = project_uids[project_labels.index(project_label)]
    priority = st.number_input(
        "Priority (1-9, 1=highest)", min_value=1, max_value=9, value=5,
        key="_ctd_priority",
    )
    has_deadline = st.checkbox("Set hard deadline", key="_ctd_has_deadline")
    hard_deadline: datetime | None = None
    if has_deadline:
        hard_deadline = st.datetime_input(
            "Hard deadline", key="_ctd_deadline", value=datetime.now()
        )
    has_schedule = st.checkbox("Schedule on calendar", key="_ctd_has_schedule")
    scheduled_start: datetime | None = None
    scheduled_duration: int | None = None
    if has_schedule:
        scheduled_start = st.datetime_input(
            "Start", key="_ctd_sched_start", value=datetime.now()
        )
        scheduled_duration = st.number_input(
            "Duration (minutes)", min_value=5, max_value=480, value=30,
            key="_ctd_sched_dur",
        )

    cols = st.columns(2)
    if cols[0].button("Create", type="primary", key="_ctd_create", use_container_width=True):
        if not summary.strip():
            st.error("Summary is required")
            return
        from genesis.entities import TaskCreate

        facade.caldav.create_task(
            TaskCreate(
                summary=summary,
                description=description or None,
                hard_deadline=hard_deadline,
                scheduled_start=scheduled_start,
                scheduled_duration=scheduled_duration,
                priority=priority,
                project_id=project_uid,
            )
        )
        st.session_state.pop("open_task_dialog", None)
        st.toast(f"Created task: {summary}")
        st.rerun()
    if cols[1].button("Cancel", key="_ctd_cancel", use_container_width=True):
        st.session_state.pop("open_task_dialog", None)
        st.rerun()


@st.dialog("Edit task")
def _edit_task_dialog(uid: str) -> None:
    """Internal: edit-task dialog body."""
    facade = get_facade()
    task = facade.caldav.get_task(uid)
    if task is None:
        st.error("Task not found")
        if st.button("Close"):
            st.session_state.pop("open_task_dialog", None)
            st.rerun()
        return

    kp = _form_key("etd", uid)
    projects = facade.caldav.list_projects()
    project_options = [(p.uid, p.name) for p in projects]
    project_labels = ["(none)"] + [name for _, name in project_options]
    project_uids = [None] + [uid for _, uid in project_options]
    cur_idx = (
        project_uids.index(task.project_id)
        if task.project_id in project_uids
        else 0
    )

    summary = st.text_input("Summary", value=task.summary, key=f"{kp}_summary")
    description = st.text_area(
        "Description", value=task.description or "", key=f"{kp}_desc", height=140
    )
    project_label = st.selectbox(
        "Project", project_labels, key=f"{kp}_proj", index=cur_idx
    )
    project_uid = project_uids[project_labels.index(project_label)]
    from genesis.entities import TaskStatus

    status_label = st.selectbox(
        "Status",
        [s.value for s in TaskStatus],
        index=[s.value for s in TaskStatus].index(task.status.value),
        key=f"{kp}_status",
    )
    priority = st.number_input(
        "Priority (1-9)", min_value=1, max_value=9,
        value=task.priority or 5, key=f"{kp}_prio",
    )
    has_deadline = st.checkbox(
        "Set hard deadline",
        value=task.hard_deadline is not None,
        key=f"{kp}_has_dl",
    )
    hard_deadline = (
        st.datetime_input(
            "Hard deadline", key=f"{kp}_dl", value=task.hard_deadline or datetime.now()
        )
        if has_deadline
        else None
    )
    has_schedule = st.checkbox(
        "Schedule on calendar",
        value=task.scheduled_start is not None,
        key=f"{kp}_has_sch",
    )
    scheduled_start: datetime | None = None
    scheduled_duration: int | None = None
    if has_schedule:
        scheduled_start = st.datetime_input(
            "Start",
            key=f"{kp}_sch_s",
            value=task.scheduled_start or datetime.now(),
        )
        scheduled_duration = st.number_input(
            "Duration (minutes)",
            min_value=5,
            max_value=480,
            value=task.scheduled_duration or 30,
            key=f"{kp}_sch_d",
        )

    cols = st.columns([1, 1, 1])
    if cols[0].button("Save", type="primary", key=f"{kp}_save", use_container_width=True):
        from genesis.entities import TaskUpdate

        new_status = TaskStatus(status_label)
        facade.caldav.update_task(
            uid,
            TaskUpdate(
                summary=summary,
                description=description or None,
                hard_deadline=hard_deadline,
                clear_scheduled_start=(not has_schedule),
                scheduled_start=scheduled_start,
                scheduled_duration=scheduled_duration,
                priority=priority,
                status=new_status,
                project_id=project_uid,
                clear_project_id=(project_uid is None),
            ),
        )
        st.session_state.pop("open_task_dialog", None)
        st.toast("Task updated")
        st.rerun()
    if cols[1].button("Cancel", key=f"{kp}_cancel", use_container_width=True):
        st.session_state.pop("open_task_dialog", None)
        st.rerun()
    if cols[2].button("Delete", key=f"{kp}_del", use_container_width=True):
        facade.caldav.delete_task(uid)
        st.session_state.pop("open_task_dialog", None)
        st.toast("Task deleted")
        st.rerun()


def open_task_dialog(uid: str | None = None) -> None:
    """Open the task create-or-edit dialog. Pass uid to edit, None to create.

    Sets the trigger in session state and reruns so the dialog opens
    on the next render.
    """
    st.session_state["open_task_dialog"] = uid or ""
    st.rerun()


def maybe_show_task_dialog() -> None:
    """Call at the top of any page that uses task dialogs.

    Renders the dialog if the trigger is set. Pops the trigger so the
    dialog closes on the next rerun unless something else opens it.
    """
    if "open_task_dialog" in st.session_state:
        uid = st.session_state.pop("open_task_dialog")
        if uid:
            _edit_task_dialog(uid)
        else:
            _create_task_dialog()


# -----------------------------------------------------------------------------
# Project dialogs
# -----------------------------------------------------------------------------


@st.dialog("New project")
def _create_project_dialog() -> None:
    facade = get_facade()
    name = st.text_input("Name", key="_cpd_name")
    description = st.text_area("Description", key="_cpd_desc", height=160)
    cols = st.columns([1, 1])
    if cols[0].button("Create", type="primary", key="_cpd_save", use_container_width=True):
        if not name.strip():
            st.error("Name is required")
            return
        from genesis.entities import ProjectCreate

        facade.caldav.create_project(ProjectCreate(name=name, description=description or None))
        st.session_state.pop("open_project_dialog", None)
        st.toast(f"Created project: {name}")
        st.rerun()
    if cols[1].button("Cancel", key="_cpd_cancel", use_container_width=True):
        st.session_state.pop("open_project_dialog", None)
        st.rerun()


@st.dialog("Edit project")
def _edit_project_dialog(uid: str) -> None:
    facade = get_facade()
    project = facade.caldav.get_project(uid)
    if project is None:
        st.error("Project not found")
        if st.button("Close", key="_epd_close"):
            st.session_state.pop("open_project_dialog", None)
            st.rerun()
        return

    kp = _form_key("epd", uid)
    name = st.text_input("Name", value=project.name, key=f"{kp}_name")
    description = st.text_area(
        "Description", value=project.description or "", key=f"{kp}_desc", height=160
    )
    from genesis.entities import ProjectStatus

    status_label = st.selectbox(
        "Status",
        [s.value for s in ProjectStatus],
        index=[s.value for s in ProjectStatus].index(project.status.value),
        key=f"{kp}_status",
    )
    has_start = st.checkbox("Set start date", value=project.start_date is not None, key=f"{kp}_has_s")
    start_date = (
        st.date_input("Start", value=project.start_date, key=f"{kp}_sd")
        if has_start
        else None
    )
    has_deadline = st.checkbox("Set deadline", value=project.deadline is not None, key=f"{kp}_has_d")
    deadline = (
        st.date_input("Deadline", value=project.deadline, key=f"{kp}_dd")
        if has_deadline
        else None
    )

    cols = st.columns([1, 1, 1])
    if cols[0].button("Save", type="primary", key=f"{kp}_save", use_container_width=True):
        from genesis.entities import ProjectUpdate

        facade.caldav.update_project(
            uid,
            ProjectUpdate(
                name=name,
                description=description or None,
                status=ProjectStatus(status_label),
                start_date=start_date,
                deadline=deadline,
            ),
        )
        st.session_state.pop("open_project_dialog", None)
        st.toast("Project updated")
        st.rerun()
    if cols[1].button("Cancel", key=f"{kp}_cancel", use_container_width=True):
        st.session_state.pop("open_project_dialog", None)
        st.rerun()
    if cols[2].button("Delete", key=f"{kp}_del", use_container_width=True):
        facade.caldav.delete_project(uid)
        st.session_state.pop("open_project_dialog", None)
        st.toast(f"Deleted project: {name}")
        st.rerun()


def open_project_dialog(uid: str | None = None) -> None:
    """Open the project create-or-edit dialog."""
    st.session_state["open_project_dialog"] = uid or ""
    st.rerun()


def maybe_show_project_dialog() -> None:
    """Call at the top of any page that uses project dialogs."""
    if "open_project_dialog" in st.session_state:
        uid = st.session_state.pop("open_project_dialog")
        if uid:
            _edit_project_dialog(uid)
        else:
            _create_project_dialog()


# -----------------------------------------------------------------------------
# Delete confirm dialog
# -----------------------------------------------------------------------------


@st.dialog("Confirm delete")
def _delete_confirm_dialog(label: str, kind: str, callback: Callable[[], Any]) -> None:
    st.write(f"Delete **{label}**? This cannot be undone.")
    cols = st.columns(2)
    if cols[0].button("Delete", type="primary", key=f"delcfm_{kind}", use_container_width=True):
        callback()
        st.session_state.pop("pending_delete", None)
        st.rerun()
    if cols[1].button("Cancel", key=f"delcn_{kind}", use_container_width=True):
        st.session_state.pop("pending_delete", None)
        st.rerun()


def request_delete(kind: str, label: str, callback: Callable[[], Any]) -> None:
    """Stage a delete confirmation. Call from a "Delete" button.

    The dialog opens on the next render and runs ``callback()`` on
    confirmation. ``kind`` is a short tag used to keep the dialog's
    widget keys unique across pages.
    """
    st.session_state["pending_delete"] = (kind, label, callback)
    st.rerun()


def maybe_show_delete_confirm() -> None:
    """Call at the top of any page that uses request_delete()."""
    if "pending_delete" in st.session_state:
        kind, label, callback = st.session_state["pending_delete"]
        _delete_confirm_dialog(label, kind, callback)
