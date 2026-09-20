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
    TaskStatus,
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
        # Deadline is a date-only field; snap to 23:59:59 so two tasks
        # with the same calendar date share the same stored datetime
        # and sort ties break on the next level (priority).
        hard_deadline_dt = datetime.combine(hard_deadline_date, time(23, 59, 59)) if hard_deadline_date else None
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


@st.dialog("Edit task", width="large", on_dismiss="rerun")
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
    project_uids: list[str | None] = [None] + [uid for uid, _ in project_options]
    cur_idx = project_uids.index(task.project_id) if task.project_id in project_uids else 0
    # Reset the project dropdown so index=cur_idx re-applies on each open;
    # otherwise a stale "(none)" left over from a prior open silently unlinks
    # the task on Save.
    st.session_state.pop(f"{kp}_proj", None)

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
        # Deadline is a date-only field; snap to 23:59:59 so two tasks
        # with the same calendar date share the same stored datetime
        # and sort ties break on the next level (priority).
        hard_deadline_dt = datetime.combine(hard_deadline_date, time(23, 59, 59)) if hard_deadline_date else None
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


# Keys for the retrospective-log dialog widgets. The dialog is
# opened from multiple entry points (the Statistics page button
# and the Tasks table "Log Session" action), so the keys are
# intentionally unprefixed with any entity UID. State is reset on
# each close so the caller-supplied ``task`` parameter always wins.
_SESSION_LOG_DIALOG_LABEL_KEY = "_spl_label"
# Tracks the label value the dialog last set automatically from
# the selected task. The label auto-updates when the user picks a
# different task only if its current value still matches this
# baseline; a user-typed label that has drifted is preserved.
_SESSION_LOG_DIALOG_LABEL_BASELINE_KEY = "_spl_label_baseline"
_SESSION_LOG_DIALOG_TASK_KEY = "_spl_task"
_SESSION_LOG_DIALOG_START_KEY = "_spl_start"
_SESSION_LOG_DIALOG_DUR_KEY = "_spl_dur"
_SESSION_LOG_DIALOG_CAT_KEY = "_spl_cat"
# When a task is selected, the user can tick this checkbox to also
# flip the task status to COMPLETED on Log. Cleared on close so
# each dialog open starts unchecked.
_SESSION_LOG_DIALOG_COMPLETE_TASK_KEY = "_spl_complete_task"


def _clear_session_log_dialog_state() -> None:
    """Drop the retrospective-log dialog widget keys from session state.

    Called from Log and Cancel so the next open starts with the
    task-based defaults instead of the previous session's values.
    """
    for key in (
        _SESSION_LOG_DIALOG_LABEL_KEY,
        _SESSION_LOG_DIALOG_LABEL_BASELINE_KEY,
        _SESSION_LOG_DIALOG_TASK_KEY,
        _SESSION_LOG_DIALOG_CAT_KEY,
        _SESSION_LOG_DIALOG_COMPLETE_TASK_KEY,
    ):
        st.session_state.pop(key, None)


@st.dialog("Log past session", width="large", on_dismiss=_clear_session_log_dialog_state)
def session_log_dialog(*, task: Task | None = None) -> None:
    """Open the retrospective-log dialog. Call from a button body.

    For when the user forgot to start a timer or the system was
    unavailable. The submitted session is written directly as
    ``COMPLETED`` so it shows up in the Statistics rollups without
    needing a stop action.

    When ``task`` is supplied, the dialog pre-selects it and
    pre-fills the label with ``task.title``. As with
    ``focus_create_dialog``, picking a different task in the
    dropdown auto-updates the label unless the user has typed a
    custom value.
    """
    facade = get_facade()
    caldav = facade.caldav

    # Candidate set is the full task list (not just open tasks).
    # Retrospective logging is meant for sessions against any task
    # the user worked on, including ones that have since been
    # completed or canceled.
    tasks = caldav.list_tasks()
    task_options: list[Task | None] = [None, *tasks]
    task_labels = ["(unaligned)"] + [t.title for t in tasks]
    initial_idx = 0
    if task is not None:
        for i, candidate in enumerate(task_options):
            if candidate is not None and candidate.uid == task.uid:
                initial_idx = i
                break

    selected_task_label = st.selectbox(
        "Linked task",
        task_labels,
        index=initial_idx,
        key=_SESSION_LOG_DIALOG_TASK_KEY,
        help="Pick a task to count this session against it, or leave unaligned.",
    )
    selected_task = task_options[task_labels.index(selected_task_label)]

    # Only show the completion checkbox when a task is selected.
    # Default off: the user opts in to flip the task status to
    # COMPLETED alongside the session log.
    if selected_task is not None:
        st.checkbox(
            f"Also mark **{selected_task.title}** as completed",
            key=_SESSION_LOG_DIALOG_COMPLETE_TASK_KEY,
            help="Tick if this session finished the task. The task status will be set to completed when you click Log.",
        )

    # If a task is supplied (or one was selected on a prior open
    # before the dialog was reset), default the label to the task
    # title. Without a task, the field starts empty with the
    # placeholder visible. The baseline tracks the last
    # automatically-set value so subsequent in-dialog task changes
    # auto-update the label unless the user has typed something
    # custom.
    label_default = selected_task.title if selected_task is not None else ""
    if _SESSION_LOG_DIALOG_LABEL_BASELINE_KEY not in st.session_state:
        st.session_state[_SESSION_LOG_DIALOG_LABEL_BASELINE_KEY] = label_default
    baseline_label = st.session_state[_SESSION_LOG_DIALOG_LABEL_BASELINE_KEY]
    current_label = st.session_state.get(_SESSION_LOG_DIALOG_LABEL_KEY, label_default)
    if current_label == baseline_label:
        st.session_state[_SESSION_LOG_DIALOG_LABEL_KEY] = label_default
    st.session_state[_SESSION_LOG_DIALOG_LABEL_BASELINE_KEY] = label_default

    label = st.text_input(
        "Session label",
        key=_SESSION_LOG_DIALOG_LABEL_KEY,
        placeholder="What did you do?",
    )

    time_cols = st.columns(2)
    with time_cols[0]:
        start_dt = st.datetime_input(
            "Starting time",
            key=_SESSION_LOG_DIALOG_START_KEY,
            value=datetime.now(),
        )
    with time_cols[1]:
        duration = st.number_input(
            "Duration (minutes)",
            min_value=1,
            max_value=1440,
            value=25,
            key=_SESSION_LOG_DIALOG_DUR_KEY,
            help="How long the session lasted.",
        )

    category_options = [c.value for c in TimeLogCategory]
    st.selectbox(
        "Category",
        category_options,
        index=category_options.index(TimeLogCategory.WORK.value),
        key=_SESSION_LOG_DIALOG_CAT_KEY,
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
            category=TimeLogCategory(
                st.session_state[_SESSION_LOG_DIALOG_CAT_KEY],
            ),
            start=start_dt,
            duration_minutes=int(duration),
            task=selected_task,
        )
        facade.caldav.create_time_log_entry(entry)
        # If the user opted in via the completion checkbox, flip
        # the linked task to COMPLETED in the same action. The
        # checkbox only renders when a task is selected, so the
        # flag is meaningful only in that case.
        completed_task = False
        if selected_task is not None and st.session_state.get(_SESSION_LOG_DIALOG_COMPLETE_TASK_KEY, False):
            from genesis.entities import TaskStatus, TaskUpdate

            facade.caldav.update_task(
                selected_task.uid,
                TaskUpdate(status=TaskStatus.COMPLETED),
            )
            completed_task = True
        _clear_session_log_dialog_state()
        if completed_task:
            st.toast(f"Logged and completed: {selected_task.title if selected_task else label_clean}")
        else:
            st.toast(f"Logged: {label_clean}")
        st.rerun()
    if action_cols[1].button("Cancel", key="_spl_cancel"):
        _clear_session_log_dialog_state()
        st.rerun()


# -----------------------------------------------------------------------------
# Focus session dialogs
# -----------------------------------------------------------------------------


# Keys for the focus-create dialog widgets. The dialog is opened
# from multiple entry points (the Focus page button and the Tasks
# table "Focus" action), so the keys are intentionally unprefixed
# with any entity UID. State is reset on each open so the
# caller-supplied ``task`` parameter always wins.
_FOCUS_DIALOG_TASK_KEY = "_foc_task"
_FOCUS_DIALOG_LABEL_KEY = "_foc_label"
# Tracks the label value the dialog last set automatically (i.e.
# derived from the selected task). When the user picks a different
# task in the dropdown, the label auto-updates only if its current
# value still matches this baseline. A user-typed label that has
# drifted from the baseline is preserved across task changes.
_FOCUS_DIALOG_LABEL_BASELINE_KEY = "_foc_label_baseline"
_FOCUS_DIALOG_MODE_KEY = "_foc_mode"
_FOCUS_DIALOG_DUR_KEY = "_foc_dur"
_FOCUS_DIALOG_CAT_KEY = "_foc_cat"

# Key the Focus page reads to decide whether to open the
# completion dialog after a session ends (auto-stop or manual
# stop). Set in the focus page; consumed there too.
FOCUS_COMPLETION_PENDING_KEY = "focus_completion_pending"


def build_focus_session_entry(
    *,
    label: str,
    task: Task | None,
    mode: TimeLogMode,
    countdown_minutes: int | None,
    category: TimeLogCategory,
) -> TimeLogEntryCreate:
    """Build a TimeLogEntryCreate for a new in-progress focus session.

    ``start`` is "now" (UTC); ``due`` is set when ``mode`` is
    ``COUNTDOWN`` and is ``start + countdown_minutes``. ``status``
    is ``IN_PROGRESS`` so the entry shows up in the active-session
    list. ``task_id`` carries the alignment when a task is
    supplied; unaligned sessions carry no task link.
    ``category`` is the user-chosen category for this session and
    is recorded on the entry from the start so the active-session
    card's "Will save as" matches what we will write on stop.
    """
    from genesis.caldav.ical import now_utc

    now = now_utc()
    if mode == TimeLogMode.COUNTDOWN:
        assert countdown_minutes is not None
        due = now + timedelta(minutes=countdown_minutes)
    else:
        due = None
    return TimeLogEntryCreate(
        label=label,
        start=now,
        due=due,
        mode=mode,
        status=TimeLogStatus.IN_PROGRESS,
        task_id=task.uid if task is not None else None,
        category=category,
    )


def _clear_focus_dialog_state() -> None:
    """Drop the focus-create dialog widget keys from session state.

    Called from Start and Cancel so the next open starts with the
    task-based defaults instead of the previous session's values.
    """
    for key in (
        _FOCUS_DIALOG_TASK_KEY,
        _FOCUS_DIALOG_LABEL_KEY,
        _FOCUS_DIALOG_LABEL_BASELINE_KEY,
        _FOCUS_DIALOG_CAT_KEY,
    ):
        st.session_state.pop(key, None)


@st.dialog("Start focus session", width="large", on_dismiss=_clear_focus_dialog_state)
def focus_create_dialog(*, task: Task | None = None) -> None:
    """Open the new-session dialog. Call from a button body.

    When ``task`` is supplied, the dialog pre-selects it, defaults
    the label to ``task.title`` (editable), and defaults the
    category to ``WORK``. Without a task, the label defaults to
    ``"Focus session"`` and the category defaults to ``OTHER``.

    Widget state is intentionally NOT reset at the top of this
    function: Streamlit re-runs the dialog body when the user
    interacts with any widget inside it, and popping the keys
    here would erase the user's in-flight changes (most visibly,
    the task selectbox picks). Instead, Start and Cancel both
    call ``_clear_focus_dialog_state`` so the next open starts
    with task-based defaults.
    """
    facade = get_facade()
    caldav = facade.caldav

    # Task list mirrors the Focus page: open tasks (NEEDS_ACTION +
    # IN_PROGRESS). Already-completed and canceled tasks are not
    # useful alignment targets.
    tasks = [t for t in caldav.list_tasks() if t.status in (TaskStatus.NEEDS_ACTION, TaskStatus.IN_PROGRESS)]
    task_options: list[Task | None] = [None, *tasks]
    task_labels = ["(unaligned)"] + [t.title for t in tasks]
    initial_idx = 0
    if task is not None:
        for i, candidate in enumerate(task_options):
            if candidate is not None and candidate.uid == task.uid:
                initial_idx = i
                break

    selected_label = st.selectbox(
        "Task",
        task_labels,
        index=initial_idx,
        key=_FOCUS_DIALOG_TASK_KEY,
        help="Pick a task to count this session against it, or leave unaligned.",
    )
    selected_task = task_options[task_labels.index(selected_label)]

    # Label and category defaults depend on whether a task is
    # selected. The label is editable; the category is the
    # recommended start but the user can pick anything.
    label_default = selected_task.title if selected_task is not None else "Focus session"
    category_default = TimeLogCategory.WORK if selected_task is not None else TimeLogCategory.OTHER
    category_options = [c.value for c in TimeLogCategory]

    # Auto-update the label when the task changes, unless the user
    # has typed a custom value. The baseline records the value we
    # last set automatically; if the current label still equals it,
    # the user has not edited, so we replace it with the new task's
    # expected label. If they have edited, the comparison fails and
    # the typed value is preserved.
    if _FOCUS_DIALOG_LABEL_BASELINE_KEY not in st.session_state:
        st.session_state[_FOCUS_DIALOG_LABEL_BASELINE_KEY] = label_default
    baseline_label = st.session_state[_FOCUS_DIALOG_LABEL_BASELINE_KEY]
    current_label = st.session_state.get(_FOCUS_DIALOG_LABEL_KEY, label_default)
    if current_label == baseline_label:
        st.session_state[_FOCUS_DIALOG_LABEL_KEY] = label_default
    st.session_state[_FOCUS_DIALOG_LABEL_BASELINE_KEY] = label_default

    label = st.text_input(
        "Session label",
        value=label_default,
        key=_FOCUS_DIALOG_LABEL_KEY,
        help=(
            "Shown on the active session card and in the time log. "
            "Defaults to the task summary when a task is selected."
        ),
    )
    mode_label = st.radio(
        "Mode",
        ["Count down", "Count up"],
        index=0,
        key=_FOCUS_DIALOG_MODE_KEY,
        horizontal=True,
    )
    is_countdown = mode_label == "Count down"
    duration = st.number_input(
        "Duration (minutes)",
        min_value=1,
        max_value=240,
        value=25,
        key=_FOCUS_DIALOG_DUR_KEY,
        disabled=not is_countdown,
        help="Target session length when counting down.",
    )
    st.selectbox(
        "Category",
        category_options,
        index=category_options.index(category_default.value),
        key=_FOCUS_DIALOG_CAT_KEY,
        format_func=lambda v: v.capitalize(),
    )

    st.divider()
    action_cols = st.columns([1, 1, 6])
    if action_cols[0].button("Start", type="primary", key="_foc_start"):
        label_clean = (label or "").strip()
        if not label_clean:
            st.error("Session label is required")
            return
        mode = TimeLogMode.COUNTDOWN if is_countdown else TimeLogMode.COUNTUP
        chosen_category = TimeLogCategory(
            st.session_state[_FOCUS_DIALOG_CAT_KEY],
        )
        entry = caldav.create_time_log_entry(
            build_focus_session_entry(
                label=label_clean,
                task=selected_task,
                mode=mode,
                countdown_minutes=int(duration) if is_countdown else None,
                category=chosen_category,
            )
        )
        _clear_focus_dialog_state()
        st.toast(f"Started: {entry.label}")
        st.rerun(scope="app")
    if action_cols[1].button("Cancel", key="_foc_cancel"):
        _clear_focus_dialog_state()
        st.rerun()


@st.dialog("Mark task completed?", on_dismiss="rerun")
def focus_completion_dialog(*, task_uid: str, task_title: str) -> None:
    """Open the post-session completion prompt. Call from a button body.

    Triggered by the Focus page after a session with a linked
    task ends (auto-stop or manual stop). Accepting marks the
    linked task ``COMPLETED`` via ``update_task``; declining
    leaves the task untouched. Both branches clear the
    ``focus_completion_pending`` flag so the dialog does not
    re-open on the next render.
    """
    facade = get_facade()
    st.write(f"Mark task **{task_title}** as completed now that the focus session on it has ended?")
    cols = st.columns(2)
    if cols[0].button("Yes, complete", type="primary", key="_focpl_yes"):
        from genesis.entities import TaskStatus, TaskUpdate

        facade.caldav.update_task(
            task_uid,
            TaskUpdate(status=TaskStatus.COMPLETED),
        )
        st.session_state.pop(FOCUS_COMPLETION_PENDING_KEY, None)
        st.toast(f"Completed: {task_title}")
        st.rerun()
    if cols[1].button("Not yet", key="_focpl_no"):
        st.session_state.pop(FOCUS_COMPLETION_PENDING_KEY, None)
        st.rerun()
