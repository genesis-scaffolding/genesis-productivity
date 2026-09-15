"""Shared Streamlit widgets and dialogs for the genesis UI.

Page modules call these to render consistent status pills and to open
dialogs. The dialogs are streamlit-decorated functions; pages call them
directly from a button body (or ``on_click`` callback) — there is no
session-state flag indirection. ``on_dismiss="rerun"`` makes X-button
or ESC dismissal trigger an immediate rerun so any widget updates flush
cleanly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

import streamlit as st

from genesis.category.enums import TimeLogCategory
from genesis.entities import (
    Task,
    TimeLogEntryCreate,
    TimeLogMode,
    TimeLogStatus,
)
from genesis.tz import to_utc
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


# -----------------------------------------------------------------------------
# Task dialogs
# -----------------------------------------------------------------------------


@st.dialog("New task", width="large", on_dismiss="rerun")
def task_create_dialog(*, project_id: str | None = None) -> None:
    """Open the create-task dialog. Call from a button body.

    Pass ``project_id`` to pre-attach the new task to a project.
    """
    facade = get_facade()
    projects = facade.caldav.list_projects()
    project_options = [(p.uid, p.name) for p in projects]
    project_labels = ["(none)"] + [name for _, name in project_options]
    project_uids: list[str | None] = [None] + [uid for uid, _ in project_options]

    title = st.text_input("Title", key="_ntd_title")
    description = st.text_area(
        "Description",
        key="_ntd_desc",
        height=100,
        placeholder="Optional details…",
    )

    if project_id is not None and project_id in project_uids:
        proj_idx = project_uids.index(project_id)
        st.caption(f"Project: **{project_labels[proj_idx]}**")
        selected_project_uid: str | None = project_id
    else:
        selected_label = st.selectbox("Project", project_labels, index=0, key="_ntd_proj")
        selected_project_uid = project_uids[project_labels.index(selected_label)]

    sched_cols = st.columns(2)
    with sched_cols[0]:
        st.caption("Schedule on calendar")
        scheduled_start = st.datetime_input(
            "scheduled_start",
            key="_ntd_sched",
            value=None,
            label_visibility="collapsed",
        )
        scheduled_duration = st.number_input(
            "Duration (min)",
            min_value=5,
            max_value=480,
            value=30,
            key="_ntd_dur",
        )
    with sched_cols[1]:
        st.caption("Hard deadline")
        hard_deadline_date: date | None = st.date_input(
            "hard_deadline",
            key="_ntd_dl",
            value=None,
            label_visibility="collapsed",
            format="YYYY-MM-DD",
        )
        st.caption("Assigned")
        assigned_date = st.date_input(
            "assigned_date",
            key="_ntd_assigned",
            value=None,
            label_visibility="collapsed",
            format="YYYY-MM-DD",
        )

    priority = st.number_input("Priority", min_value=1, max_value=9, value=5, key="_ntd_prio")

    st.divider()
    action_cols = st.columns([1, 1, 6])
    if action_cols[0].button("Cancel", key="_ntd_cancel"):
        st.rerun()
    if action_cols[1].button("Add", type="primary", key="_ntd_add"):
        if not title.strip():
            st.error("Title is required")
            return
        from genesis.entities import TaskCreate

        # Build UTC datetimes from local-naive streamlit widgets.
        scheduled_start_utc = to_utc(scheduled_start) if scheduled_start else None
        hard_deadline_dt = datetime.combine(hard_deadline_date, time.min) if hard_deadline_date else None
        hard_deadline_utc = to_utc(hard_deadline_dt) if hard_deadline_dt else None

        facade.caldav.create_task(
            TaskCreate(
                title=title,
                description=description or None,
                project_id=selected_project_uid,
                scheduled_start=scheduled_start_utc,
                scheduled_duration=(scheduled_duration if scheduled_start_utc is not None else None),
                hard_deadline=hard_deadline_utc,
                assigned_date=assigned_date,
                priority=priority,
            )
        )
        st.toast(f"Created task: {title}")
        st.rerun()


def _on_task_edit_dismiss() -> None:
    """Reset the project-dropdown init flag when the dialog closes.

    The flag is set on the first render of each dialog session and
    blocks the explicit seed-to-task-project step (see ``task_edit_dialog``)
    on subsequent renders. Without this reset, a stale session_state
    value from a prior open survives across opens and the dropdown
    shows the wrong project.
    """
    for key in list(st.session_state.keys()):
        if key.startswith("etd_proj_init_"):
            st.session_state.pop(key, None)


@st.dialog("Edit task", width="large", on_dismiss=_on_task_edit_dismiss)
def task_edit_dialog(uid: str) -> None:
    """Open the edit-task dialog for the given task UID."""
    facade = get_facade()
    task = facade.caldav.get_task(uid)
    if task is None:
        st.error("Task not found")
        if st.button("Close"):
            st.rerun()
        return

    kp = _form_key("etd", uid)
    projects = facade.caldav.list_projects()
    project_options = [(p.uid, p.name) for p in projects]
    project_labels = ["(none)"] + [name for _, name in project_options]
    project_uids: list[str | None] = [None] + [uid for _, uid in project_options]
    cur_idx = project_uids.index(task.project_id) if task.project_id in project_uids else 0
    # Force the project dropdown to the task's current project on the
    # FIRST render of this dialog session. ``index=cur_idx`` only
    # seeds a never-used widget, and ``st.session_state.pop`` does not
    # reliably reset the Frontend's cached widget state in
    # Streamlit 1.61, so explicitly assign the initial value here. On
    # subsequent renders within the dialog, the flag below blocks the
    # override so the user's selection survives. ``_on_task_edit_dismiss``
    # resets the flag when the dialog closes, so the next open is fresh.
    init_key = f"etd_proj_init_{uid}"
    if not st.session_state.get(init_key, False):
        st.session_state[f"{kp}_proj"] = project_labels[cur_idx]
        st.session_state[init_key] = True

    title = st.text_input("Title", value=task.title, key=f"{kp}_title")
    description = st.text_area(
        "Description",
        value=task.description or "",
        key=f"{kp}_desc",
        height=100,
    )
    project_label = st.selectbox("Project", project_labels, key=f"{kp}_proj", index=cur_idx)
    project_uid = project_uids[project_labels.index(project_label)]
    from genesis.entities import TaskStatus

    status_label = st.selectbox(
        "Status",
        [s.value for s in TaskStatus],
        index=[s.value for s in TaskStatus].index(task.status.value),
        key=f"{kp}_status",
    )
    priority = st.number_input(
        "Priority",
        min_value=1,
        max_value=9,
        value=task.priority or 5,
        key=f"{kp}_prio",
    )

    sched_cols = st.columns(2)
    with sched_cols[0]:
        st.caption("Schedule on calendar")
        scheduled_start_date: date | None = st.date_input(
            "scheduled_start_date",
            value=(task.scheduled_start.astimezone().date() if task.scheduled_start else None),
            key=f"{kp}_sch_d",
            label_visibility="collapsed",
            format="YYYY-MM-DD",
        )
        schedule_time = st.time_input(
            "schedule_time",
            value=(task.scheduled_start.astimezone().time() if task.scheduled_start else None),
            key=f"{kp}_sch_t",
        )
        scheduled_duration = st.number_input(
            "Duration (min)",
            min_value=5,
            max_value=480,
            value=task.scheduled_duration or 30,
            key=f"{kp}_dur",
        )
    with sched_cols[1]:
        st.caption("Hard deadline")
        hard_deadline_date: date | None = st.date_input(
            "hard_deadline",
            value=(task.hard_deadline.astimezone().date() if task.hard_deadline else None),
            key=f"{kp}_dl",
            label_visibility="collapsed",
            format="YYYY-MM-DD",
        )
        st.caption("Assigned")
        assigned_date = st.date_input(
            "assigned_date",
            value=task.assigned_date,
            key=f"{kp}_assigned",
            label_visibility="collapsed",
            format="YYYY-MM-DD",
        )

    combined_scheduled: datetime | None = None
    if scheduled_start_date is not None and schedule_time is not None:
        combined_scheduled = datetime.combine(scheduled_start_date, schedule_time)

    st.divider()
    action_cols = st.columns([1, 1, 1, 6])
    if action_cols[0].button("Save", type="primary", key=f"{kp}_save"):
        from genesis.entities import TaskUpdate

        new_status = TaskStatus(status_label)
        # Build UTC datetimes from local-naive streamlit widgets.
        combined_scheduled_utc = to_utc(combined_scheduled) if combined_scheduled else None
        hard_deadline_dt = datetime.combine(hard_deadline_date, time.min) if hard_deadline_date else None
        hard_deadline_utc = to_utc(hard_deadline_dt) if hard_deadline_dt else None

        update_kwargs: dict = {
            "title": title,
            "description": description or None,
            "hard_deadline": hard_deadline_utc,
            "assigned_date": assigned_date,
            "priority": priority,
            "status": new_status,
            "project_id": project_uid,
            "clear_project_id": (project_uid is None),
        }
        if combined_scheduled_utc is None and task.scheduled_start is not None:
            update_kwargs["clear_scheduled_start"] = True
        elif combined_scheduled_utc is not None:
            update_kwargs["scheduled_start"] = combined_scheduled_utc
            update_kwargs["scheduled_duration"] = scheduled_duration
        facade.caldav.update_task(uid, TaskUpdate(**update_kwargs))
        st.toast("Task updated")
        st.rerun()
    if action_cols[1].button("Cancel", key=f"{kp}_cancel"):
        st.rerun()
    if action_cols[2].button("Delete", key=f"{kp}_del"):
        facade.caldav.delete_task(uid)
        st.toast("Task deleted")
        st.rerun()


# -----------------------------------------------------------------------------
# Project dialogs
# -----------------------------------------------------------------------------


@st.dialog("New project", on_dismiss="rerun")
def project_create_dialog() -> None:
    """Open the create-project dialog. Call from a button body."""
    facade = get_facade()
    name = st.text_input("Name", key="_cpd_name")
    description = st.text_area("Description", key="_cpd_desc", height=160)
    cols = st.columns([1, 1])
    if cols[0].button("Create", type="primary", key="_cpd_save"):
        if not name.strip():
            st.error("Name is required")
            return
        from genesis.entities import ProjectCreate

        facade.caldav.create_project(ProjectCreate(name=name, description=description or None))
        st.toast(f"Created project: {name}")
        st.rerun()
    if cols[1].button("Cancel", key="_cpd_cancel"):
        st.rerun()


@st.dialog("Edit project", on_dismiss="rerun")
def project_edit_dialog(uid: str) -> None:
    """Open the edit-project dialog for the given project UID."""
    facade = get_facade()
    project = facade.caldav.get_project(uid)
    if project is None:
        st.error("Project not found")
        if st.button("Close", key="_epd_close"):
            st.rerun()
        return

    kp = _form_key("epd", uid)
    name = st.text_input("Name", value=project.name, key=f"{kp}_name")
    description = st.text_area("Description", value=project.description or "", key=f"{kp}_desc", height=160)
    from genesis.entities import ProjectStatus

    status_label = st.selectbox(
        "Status",
        [s.value for s in ProjectStatus],
        index=[s.value for s in ProjectStatus].index(project.status.value),
        key=f"{kp}_status",
    )
    has_start = st.checkbox("Set start date", value=project.start_date is not None, key=f"{kp}_has_s")
    start_date = st.date_input("Start", value=project.start_date, key=f"{kp}_sd") if has_start else None
    has_deadline = st.checkbox("Set deadline", value=project.deadline is not None, key=f"{kp}_has_d")
    deadline = st.date_input("Deadline", value=project.deadline, key=f"{kp}_dd") if has_deadline else None

    cols = st.columns([1, 1, 1])
    if cols[0].button("Save", type="primary", key=f"{kp}_save"):
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
        st.toast("Project updated")
        st.rerun()
    if cols[1].button("Cancel", key=f"{kp}_cancel"):
        st.rerun()
    if cols[2].button("Delete", key=f"{kp}_del"):
        facade.caldav.delete_project(uid)
        st.toast(f"Deleted project: {name}")
        st.rerun()


# -----------------------------------------------------------------------------
# Delete confirm dialog
# -----------------------------------------------------------------------------


@st.dialog("Confirm delete", on_dismiss="rerun")
def delete_confirm_dialog(label: str, callback: Callable[[], Any]) -> None:
    """Open the delete-confirmation dialog. Call from a button body.

    ``label`` is the human-readable name of the thing being deleted.
    ``callback`` runs on confirmation.
    """
    st.write(f"Delete **{label}**? This cannot be undone.")
    cols = st.columns(2)
    if cols[0].button("Delete", type="primary", key=f"delcfm_{label}"):
        callback()
        st.rerun()
    if cols[1].button("Cancel", key=f"delcn_{label}"):
        st.rerun()


# -----------------------------------------------------------------------------
# Time log dialogs
# -----------------------------------------------------------------------------


def build_retrospective_entry(
    *,
    label: str,
    category: TimeLogCategory,
    start: datetime,
    duration_minutes: int,
    task: Task | None,
) -> TimeLogEntryCreate:
    """Build a TimeLogEntryCreate for a session logged after the fact.

    The session is recorded as already ``COMPLETED``: ``due`` and
    ``completed_at`` are both ``start + duration``. ``mode`` is
    ``COUNTDOWN`` because the user is specifying a bounded target
    duration, mirroring how the focus page writes auto-stopped
    countdown sessions. ``task_id`` is set only when a task is
    aligned; unaligned entries carry no task link.
    """
    start_utc = to_utc(start)
    end_utc = start_utc + timedelta(minutes=duration_minutes)
    return TimeLogEntryCreate(
        label=label,
        start=start_utc,
        due=end_utc,
        completed_at=end_utc,
        status=TimeLogStatus.COMPLETED,
        mode=TimeLogMode.COUNTDOWN,
        task_id=task.uid if task is not None else None,
        category=category,
    )


@st.dialog("Log past session", width="large", on_dismiss="rerun")
def session_log_dialog() -> None:
    """Open the retrospective-log dialog. Call from a button body.

    For when the user forgot to start a timer or the system was
    unavailable. The submitted session is written directly as
    ``COMPLETED`` so it shows up in the Statistics rollups without
    needing a stop action.
    """
    facade = get_facade()
    caldav = facade.caldav

    label = st.text_input(
        "Session label",
        key="_spl_label",
        placeholder="What did you do?",
    )

    tasks = caldav.list_tasks()
    task_options: list[Task | None] = [None, *tasks]
    task_labels = ["(unaligned)"] + [t.title for t in tasks]
    selected_task_label = st.selectbox(
        "Linked task",
        task_labels,
        index=0,
        key="_spl_task",
        help="Pick a task to count this session against it, or leave unaligned.",
    )
    selected_task = task_options[task_labels.index(selected_task_label)]

    time_cols = st.columns(2)
    with time_cols[0]:
        start_dt = st.datetime_input(
            "Starting time",
            key="_spl_start",
            value=datetime.now(),
        )
    with time_cols[1]:
        duration = st.number_input(
            "Duration (minutes)",
            min_value=1,
            max_value=1440,
            value=25,
            key="_spl_dur",
            help="How long the session lasted.",
        )

    category_options = [c.value for c in TimeLogCategory]
    category_label = st.selectbox(
        "Category",
        category_options,
        index=category_options.index(TimeLogCategory.WORK.value),
        key="_spl_cat",
        format_func=lambda v: v.capitalize(),
    )

    st.divider()
    action_cols = st.columns([1, 1, 6])
    if action_cols[0].button("Log", type="primary", key="_spl_save"):
        label_clean = (label or "").strip()
        if not label_clean:
            st.error("Session label is required")
            return
        entry = build_retrospective_entry(
            label=label_clean,
            category=TimeLogCategory(category_label),
            start=start_dt,
            duration_minutes=int(duration),
            task=selected_task,
        )
        facade.caldav.create_time_log_entry(entry)
        st.toast(f"Logged: {label_clean}")
        st.rerun()
    if action_cols[1].button("Cancel", key="_spl_cancel"):
        st.rerun()
