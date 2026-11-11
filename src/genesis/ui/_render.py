"""Shared Streamlit widgets and dialogs for the genesis UI.

Page modules call these to render consistent status pills and to open
dialogs. The dialogs are streamlit-decorated functions; pages call them
directly from a button body (or ``on_click`` callback) — there is no
session-state flag indirection. ``on_dismiss="rerun"`` makes X-button
or ESC dismissal trigger an immediate rerun so any widget updates flush
cleanly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

import streamlit as st

from genesis.category.enums import TimeLogCategory
from genesis.entities import (
    Contact,
    ContactCreate,
    RecurrenceAnchor,
    RecurrenceFrequency,
    RecurrenceRule,
    Task,
    TaskStatus,
    TaskUpdate,
    TimeLogEntryCreate,
    TimeLogMode,
    TimeLogStatus,
    occurrence_date,
)
from genesis.journal.facade import Entry, EntryType
from genesis.notify import messages
from genesis.tz import to_utc
from genesis.ui._state import get_facade

logger = logging.getLogger(__name__)

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


def render_sessions_kpi(*, count: int, caption: str) -> None:
    """Render the shared Sessions KPI."""
    with st.container(border=True):
        st.caption("Sessions")
        st.markdown(f"## {count}")
        st.caption(caption)


# -----------------------------------------------------------------------------
# Task dialogs
# -----------------------------------------------------------------------------

EDIT_SCOPE_CURRENT = "current"
"""Edit scope: only the selected occurrence."""

EDIT_SCOPE_ALL_FUTURE = "all-future"
"""Edit scope: the selected occurrence and every later one."""


def default_materialized_future_count(facade) -> int:
    """Return the per-user default number of future occurrences to keep ready.

    Falls back to 1 when the facade carries no configuration (the
    in-memory test facade, for instance).
    """
    configs = getattr(facade, "configs", None)
    recurrence_cfg = getattr(configs, "recurrence", None)
    count = getattr(recurrence_cfg, "default_materialized_future_count", None)
    if isinstance(count, int) and count > 0:
        return count
    return 1


def _anchor_preview_date(
    anchor: str,
    assigned_date: date | None,
    hard_deadline_date: date | None,
    scheduled_start: datetime | None,
) -> date | None:
    """Return the date the recurrence would anchor on, or None."""
    if anchor == RecurrenceAnchor.ASSIGNED_DATE.value:
        return assigned_date
    if anchor == RecurrenceAnchor.HARD_DEADLINE.value:
        return hard_deadline_date
    return scheduled_start.date() if scheduled_start is not None else None


def next_occurrence_date(base: date, frequency: str, interval: int) -> date:
    """Return the date of the occurrence after ``base``."""
    rule = RecurrenceRule(
        frequency=RecurrenceFrequency(frequency),
        interval=interval,
        anchor=RecurrenceAnchor.ASSIGNED_DATE,
    )
    return occurrence_date(base, rule, 1)


_CREATE_DIALOG_STATE_PREFIX = "_ntd_"


def _clear_create_dialog_state() -> None:
    """Drop the create-dialog widget state when the dialog closes.

    The widgets of a closed dialog would otherwise be reused the next
    time it opens, including the materialized-future count that the
    global default seeds. The global default must win over a value
    typed during an earlier open.
    """
    for key in [k for k in st.session_state.keys() if str(k).startswith(_CREATE_DIALOG_STATE_PREFIX)]:
        st.session_state.pop(key, None)


@st.dialog("New task", width="large", on_dismiss=_clear_create_dialog_state)
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
    st.caption("Recurrence")
    is_recurrent = st.checkbox(
        "Recurring task",
        key="_ntd_recurring",
        help="Renew this task automatically: the next occurrence appears by itself.",
    )
    frequency_value = RecurrenceFrequency.MONTHLY.value
    interval_value = 1
    anchor_value = RecurrenceAnchor.ASSIGNED_DATE.value
    horizon_value = default_materialized_future_count(facade)
    if is_recurrent:
        recurrence_cols = st.columns(3)
        with recurrence_cols[0]:
            frequency_value = st.selectbox(
                "Repeats",
                [f.value for f in RecurrenceFrequency],
                key="_ntd_freq",
            )
        with recurrence_cols[1]:
            interval_value = int(
                st.number_input(
                    "Every (units)",
                    min_value=1,
                    max_value=52,
                    value=1,
                    key="_ntd_interval",
                    help="1 means every day / week / month / year.",
                )
            )
        with recurrence_cols[2]:
            anchor_value = st.selectbox(
                "Anchor",
                [a.value for a in RecurrenceAnchor],
                key="_ntd_anchor",
                help="The date field the recurrence is measured from.",
            )
        horizon_value = int(
            st.number_input(
                "Future occurrences to keep ready",
                min_value=1,
                max_value=100,
                value=horizon_value,
                # The key carries the resolved default, so a change of
                # the global default starts a fresh widget instead of
                # restoring the value typed during an earlier open.
                key=f"_ntd_horizon_{horizon_value}",
                help="How many future occurrences stay materialized ahead of the current one.",
            )
        )
        preview_base = _anchor_preview_date(anchor_value, assigned_date, hard_deadline_date, scheduled_start)
        if preview_base is None:
            st.warning("The anchor date is missing: set the anchored date field to make this task recurrent.")
        else:
            st.caption(
                f"Next occurrence: {next_occurrence_date(preview_base, frequency_value, interval_value).isoformat()} "
                f"({horizon_value} ahead)",
            )

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

        task_data = TaskCreate(
            title=title,
            description=description or None,
            project_id=selected_project_uid,
            scheduled_start=scheduled_start_utc,
            scheduled_duration=(scheduled_duration if scheduled_start_utc is not None else None),
            hard_deadline=hard_deadline_utc,
            assigned_date=assigned_date,
            priority=priority,
        )
        if is_recurrent:
            try:
                facade.create_recurrent_task(
                    task_data,
                    frequency=frequency_value,
                    interval=interval_value,
                    anchor=anchor_value,
                    materialized_future_count=horizon_value,
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            st.toast(f"Created recurring task: {title}")
        else:
            facade.caldav.create_task(task_data)
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
    is_recurrent = bool(task.recurrence_series_uid)
    series_state = None
    if is_recurrent:
        series_state = facade.caldav.get_series_state(task.recurrence_series_uid or "")

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

    edit_scope = EDIT_SCOPE_CURRENT
    if is_recurrent:
        scope_labels = ["This instance", "All future instances"]
        # The radio keeps its own key, so Streamlit restores the
        # scope the user picked the last time this dialog was open.
        scope_choice = st.radio(
            "Applies to",
            scope_labels,
            index=0,
            key=f"{kp}_scope_choice",
            horizontal=True,
            help="Edit this occurrence only, or every later occurrence of the series.",
        )
        edit_scope = EDIT_SCOPE_ALL_FUTURE if scope_choice == scope_labels[1] else EDIT_SCOPE_CURRENT
        if edit_scope == EDIT_SCOPE_ALL_FUTURE:
            st.caption("Status changes always apply to this instance only.")

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

    if is_recurrent and series_state is not None:
        _render_series_section(facade, series_state, kp, uid)

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

        if edit_scope == EDIT_SCOPE_ALL_FUTURE:
            # The status is a per-occurrence operation: it never rides
            # along with a future-scope edit.
            status_value = update_kwargs.pop("status")
            try:
                result = facade.caldav.update_task_scoped(
                    uid,
                    TaskUpdate(**update_kwargs),
                    scope=EDIT_SCOPE_ALL_FUTURE,
                )
            except ValueError as exc:
                st.error(str(exc))
                return
            if status_value != task.status:
                facade.caldav.update_task(uid, TaskUpdate(status=status_value))
            st.toast(f"Updated {len(result.affected_uids)} future occurrences")
        elif is_recurrent:
            # The scope is explicit even when it is the current
            # occurrence, so the same operation serves both scopes.
            facade.caldav.update_task_scoped(uid, TaskUpdate(**update_kwargs), scope=EDIT_SCOPE_CURRENT)
            st.toast("Task updated")
        else:
            facade.caldav.update_task(uid, TaskUpdate(**update_kwargs))
            st.toast("Task updated")
        st.rerun()
    if action_cols[1].button("Cancel", key=f"{kp}_cancel"):
        st.rerun()
    if action_cols[2].button("Delete", key=f"{kp}_del"):
        facade.caldav.delete_task(uid)
        st.toast("Task deleted")
        st.rerun()


def _render_series_section(facade, series_state, kp: str, uid: str) -> None:
    """Render the series inspection, horizon control, and cancel action.

    The section shows the rule, the effective number of materialized
    future occurrences, the current occurrence, and the ordered future
    occurrences. The horizon can be changed on the running series;
    shrinking it cancels the farthest extra future occurrences and asks
    for a confirmation first.
    """
    st.divider()
    st.subheader("Series")
    st.caption(
        f"{series_state.frequency.value.capitalize()} every {series_state.interval} "
        f"| {series_state.materialized_future_count} ahead "
        f"| series {series_state.uid}"
        f"{'' if series_state.active else ' | cancelled'}",
    )
    st.write(f"Current occurrence: {series_state.current_task_uid or '(none)'}")
    with st.expander(f"Future occurrences ({len(series_state.future_occurrences)})"):
        if series_state.future_occurrences:
            for occurrence in series_state.future_occurrences:
                st.write(f"{occurrence.occurrence_date} - {occurrence.uid}")
        else:
            st.write("(none)")

    horizon = int(
        st.number_input(
            "Future occurrences to keep ready",
            min_value=1,
            max_value=100,
            value=series_state.materialized_future_count,
            key=f"{kp}_horizon",
            disabled=not series_state.active,
        )
    )
    shrinking = horizon < series_state.materialized_future_count
    confirmed = True
    if shrinking:
        confirmed = st.checkbox(
            f"Cancel the {series_state.materialized_future_count - horizon} farthest future occurrences",
            key=f"{kp}_horizon_confirm",
        )
        st.caption("The current occurrence and the completed history are kept.")
    if st.button(
        "Apply horizon",
        key=f"{kp}_horizon_apply",
        disabled=not confirmed or horizon == series_state.materialized_future_count,
    ):
        result = facade.caldav.resize_series(series_state.uid, horizon)
        st.toast(
            f"Horizon set to {result.materialized_future_count} "
            f"({len(result.created_task_uids)} created, {len(result.canceled_task_uids)} cancelled)",
        )
        st.rerun()

    st.caption(
        "Cancel the future to stop the renewal: every occurrence after this one is "
        "cancelled. The current occurrence and the completed history stay.",
    )
    cancel_confirmed = st.checkbox(
        "I understand that all future occurrences are cancelled",
        key=f"{kp}_cancel_future_confirm",
    )
    if st.button(
        "Cancel future",
        key=f"{kp}_cancel_future",
        disabled=not cancel_confirmed or not series_state.active,
    ):
        result = facade.caldav.cancel_future(uid)
        st.toast(f"Cancelled {len(result.canceled_uids)} future occurrences")
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


def split_multiline(value: str) -> list[str]:
    """Split a multi-line text block into a clean list of values.

    One value per line; empty lines are dropped so a trailing
    newline does not produce an empty entry. Used by the contact
    form for its multi-value fields.
    """
    return [line.strip() for line in value.splitlines() if line.strip()]


# -----------------------------------------------------------------------------
# Contact create form and dialog
# -----------------------------------------------------------------------------


def contact_create_form(
    *,
    uid: str,
    key_prefix: str,
    on_created: Callable[[Contact], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """Render the contact create form.

    One form, two callers: the Contacts page's create view and the
    ``contact_create_dialog`` the onboarding wizard opens. The
    wizard must not have its own contact form.

    ``on_created`` runs with the new contact after it is created;
    the Contacts page uses it to open the contact it just made.
    ``on_cancel`` runs when the user cancels; the Contacts page
    uses it to go back to the list.
    """
    facade = get_facade()
    with st.form(f"{key_prefix}_form"):
        ftitle = st.text_input("Name", key=f"{key_prefix}_name")
        col1, col2 = st.columns(2)
        with col1:
            fgiven = st.text_input("Given name", key=f"{key_prefix}_given")
        with col2:
            ffamily = st.text_input("Family name", key=f"{key_prefix}_family")
        col3, col4 = st.columns(2)
        with col3:
            fnickname = st.text_input("Nickname", key=f"{key_prefix}_nickname")
        with col4:
            forganization = st.text_input("Organization", key=f"{key_prefix}_organization")
        ftitle_field = st.text_input("Title", key=f"{key_prefix}_title")
        femails = st.text_area(
            "Emails (one per line)",
            height=100,
            key=f"{key_prefix}_emails",
        )
        fphones = st.text_area(
            "Phones (one per line)",
            height=100,
            key=f"{key_prefix}_phones",
        )
        furl = st.text_area(
            "URLs (one per line)",
            height=80,
            key=f"{key_prefix}_urls",
        )
        faddress = st.text_area(
            "Addresses (one per line)",
            height=80,
            key=f"{key_prefix}_addresses",
        )
        fnote = st.text_area("Note", height=120, key=f"{key_prefix}_note")
        fbirthday = st.date_input(
            "Birthday",
            value=None,
            format="YYYY-MM-DD",
            key=f"{key_prefix}_birthday",
        )

        action_row = st.container(horizontal=True, horizontal_alignment="right")
        with action_row:
            save_clicked = st.form_submit_button(
                "Create",
                type="primary",
                icon=":material/save:",
            )
            cancel_clicked = st.form_submit_button(
                "Cancel",
                type="tertiary",
            )

    if save_clicked:
        if not ftitle.strip():
            st.error("Name is required.")
            return
        contact = facade.carddav.create_contact(
            ContactCreate(
                name=ftitle.strip(),
                given_name=fgiven or None,
                family_name=ffamily or None,
                nickname=fnickname or None,
                organization=forganization or None,
                title=ftitle_field or None,
                emails=split_multiline(femails),
                phones=split_multiline(fphones),
                urls=split_multiline(furl),
                addresses=split_multiline(faddress),
                note=fnote or None,
                birthday=fbirthday,
            )
        )
        if on_created is not None:
            on_created(contact)
        else:
            st.toast("Created")
            st.rerun()
    if cancel_clicked:
        if on_cancel is not None:
            on_cancel()
        else:
            st.rerun()


@st.dialog("New contact", width="large", on_dismiss="rerun")
def contact_create_dialog() -> None:
    """Open the create-contact dialog. Call from a button body.

    The dialog holds the same form the Contacts page uses.
    """
    import uuid as _uuid

    contact_create_form(uid=_uuid.uuid4().hex, key_prefix="_ccd")


# -----------------------------------------------------------------------------
# Journal create form and dialog
# -----------------------------------------------------------------------------


def task_link_options(current_task_id: str | None = None) -> tuple[list[str], list[str | None]]:
    """Return the (labels, uids) pair for the journal task selector.

    The labels embed the task UID so two tasks with the same title stay
    distinguishable; the UID list is what gets stored on the entry.
    ``(none)`` is the first option and maps to ``None``.

    ``current_task_id`` is the link the entry already carries. When it
    points at a task that no longer exists it is appended as a
    "missing task" option so the link stays visible and is preserved
    on save instead of being silently dropped.
    """
    facade = get_facade()
    tasks = facade.caldav.list_tasks()
    labels: list[str] = ["(none)"]
    uids: list[str | None] = [None]
    for task in tasks:
        labels.append(f"{task.title} ({task.uid})")
        uids.append(task.uid)
    if current_task_id is not None and current_task_id not in uids:
        labels.append(f"(missing task {current_task_id})")
        uids.append(current_task_id)
    return labels, uids


def journal_create_form(
    *,
    uid: str,
    key_prefix: str,
    default_type: str = "daily",
    default_task_id: str | None = None,
    on_created: Callable[[Entry], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """Render the journal entry create form.

    One form, two callers: the Journal page's create view and the
    ``journal_create_dialog`` the onboarding wizard opens.

    ``default_type`` preselects the entry type. ``default_task_id``
    preselects the task the entry links to; it is optional, so the
    Journal page and the onboarding wizard are unaffected.
    ``on_created`` runs with the new entry after it is saved; the
    Journal page uses it to open the entry it just made.
    ``on_cancel`` runs when the user cancels.
    """
    facade = get_facade()
    journal = facade.journal
    type_labels = ["(none)"] + [t.value for t in EntryType]
    today = date.today()
    if default_type not in type_labels:
        default_type = "daily"
    cur_idx = type_labels.index(default_type)

    task_labels, task_uids = task_link_options(default_task_id)
    cur_task_idx = task_uids.index(default_task_id) if default_task_id in task_uids else 0
    task_key = f"{key_prefix}_task"
    if task_key not in st.session_state:
        # Seed the widget before it is created. Seeding on every render
        # would overwrite the user's pick on the form's submit rerun.
        st.session_state[task_key] = task_labels[cur_task_idx]

    with st.form(f"{key_prefix}_form"):
        ftitle = st.text_input("Title", key=f"{key_prefix}_title")
        ftype = st.selectbox("Type", type_labels, index=cur_idx, key=f"{key_prefix}_type")
        ftask = st.selectbox("Task", task_labels, key=task_key)
        fbody = st.text_area(
            "Content",
            height=400,
            placeholder="What's on your mind?",
            key=f"{key_prefix}_body",
        )
        st.caption(f"Date: {today.isoformat()} (set at creation, not editable)")

        action_row = st.container(horizontal=True, horizontal_alignment="right")
        with action_row:
            save_clicked = st.form_submit_button(
                "Create",
                type="primary",
                icon=":material/save:",
            )
            cancel_clicked = st.form_submit_button(
                "Cancel",
                type="tertiary",
            )

    if save_clicked:
        new_type = EntryType(ftype) if ftype != "(none)" else None
        new_task_id = task_uids[task_labels.index(ftask)]
        existing = journal.find_for_period(new_type, today) if new_type is not None else None
        if existing is not None and new_type is not None:
            st.error(
                f"A {new_type.value} entry for this period already exists; edit it instead",
            )
        else:
            entry = Entry(
                uid=uid,
                date=today,
                title=ftitle or None,
                type=new_type,
                task_id=new_task_id,
                content=fbody,
            )
            try:
                journal.save_entry(entry)
            except ValueError as exc:
                st.error(str(exc))
            else:
                if on_created is not None:
                    on_created(entry)
                else:
                    st.toast("Created")
                    st.rerun()
    if cancel_clicked:
        if on_cancel is not None:
            on_cancel()
        else:
            st.rerun()


_JOURNAL_DIALOG_KEY_PREFIX = "_jcd"


def journal_create_dialog(*, default_type: str = "daily", default_task_id: str | None = None) -> None:
    """Open the create-journal-entry dialog. Call from a button body.

    ``default_task_id`` preselects the task the new entry links to —
    the task detail page passes its own UID.

    The widget state is reset here, in the caller-facing wrapper, so
    each open starts from the requested defaults. Doing it inside the
    dialog body would wipe the user's pick when the form is submitted.
    """
    # Reset both dropdowns so index= re-applies on each open; otherwise a
    # stale pick (e.g. "task" from a prior task-journal open) survives and
    # the requested default is silently ignored (keyed selectbox ignores
    # index once the key exists in session state).
    st.session_state.pop(f"{_JOURNAL_DIALOG_KEY_PREFIX}_task", None)
    st.session_state.pop(f"{_JOURNAL_DIALOG_KEY_PREFIX}_type", None)
    _journal_create_dialog_body(default_type=default_type, default_task_id=default_task_id)


@st.dialog("New journal entry", width="large", on_dismiss="rerun")
def _journal_create_dialog_body(*, default_type: str, default_task_id: str | None) -> None:
    """Render the create-journal-entry dialog body."""
    import uuid as _uuid

    journal_create_form(
        uid=_uuid.uuid4().hex,
        key_prefix=_JOURNAL_DIALOG_KEY_PREFIX,
        default_type=default_type,
        default_task_id=default_task_id,
    )


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
        persisted = facade.caldav.create_time_log_entry(entry)

        # ADR-026: fire the XP event for the retrospective
        # session. The game facade computes duration from the
        # entry's ``completed_at - start`` and applies the
        # category-based stat / delta rules. Failure is best-
        # effort — the session is logged either way.
        if persisted is not None:
            facade._fire_xp_safely(
                facade.record_xp_event,
                session_id=persisted.uid,
                occurred_at=persisted.completed_at,
            )

        # If the user opted in via the completion checkbox, flip
        # the linked task to COMPLETED in the same action. The
        # checkbox only renders when a task is selected, so the
        # flag is meaningful only in that case.
        completed_task = False
        if selected_task is not None and st.session_state.get(_SESSION_LOG_DIALOG_COMPLETE_TASK_KEY, False):
            facade.complete_task(selected_task.uid)
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


@dataclass(frozen=True)
class FocusTemplate:
    """The last successfully started focus form's reusable values.

    Duration is deliberately excluded. ``label_baseline`` records the
    automatically selected label so a later task change can tell a
    task-derived label from a user-typed one.
    """

    label: str
    task_uid: str | None
    category: TimeLogCategory
    mode: TimeLogMode
    label_baseline: str


@dataclass(frozen=True)
class FocusFormValues:
    """Resolved values used to seed the focus form."""

    task_uid: str | None
    label: str
    label_baseline: str
    category: TimeLogCategory
    mode: TimeLogMode


def focus_template_state_key(username: str) -> str:
    """Return the per-user session-state key for the focus template."""
    return f"_foc_last_template_{username}"


def load_focus_template(
    state: MutableMapping[Any, Any],
    username: str,
) -> FocusTemplate | None:
    """Load one user's focus template from a state mapping."""
    value = state.get(focus_template_state_key(username))
    return value if isinstance(value, FocusTemplate) else None


def save_focus_template(
    state: MutableMapping[Any, Any],
    username: str,
    template: FocusTemplate,
) -> None:
    """Store one user's focus template in a state mapping."""
    state[focus_template_state_key(username)] = template


def resolve_focus_form_values(
    *,
    template: FocusTemplate | None,
    open_tasks: list[Task],
    explicit_task: Task | None = None,
) -> FocusFormValues:
    """Resolve focus-form defaults for an explicit or generic launch.

    An explicit task always wins and keeps the existing task-derived
    label and category defaults. A generic launch uses the previous
    template. If its task is no longer open, the task falls back to
    unaligned while the other template values are retained.
    """
    if explicit_task is not None:
        selected = next((candidate for candidate in open_tasks if candidate.uid == explicit_task.uid), None)
        if selected is None:
            return FocusFormValues(
                task_uid=None,
                label="Focus session",
                label_baseline="Focus session",
                category=TimeLogCategory.OTHER,
                mode=TimeLogMode.COUNTDOWN,
            )
        return FocusFormValues(
            task_uid=selected.uid,
            label=selected.title,
            label_baseline=selected.title,
            category=TimeLogCategory.WORK,
            mode=TimeLogMode.COUNTDOWN,
        )

    if template is None:
        return FocusFormValues(
            task_uid=None,
            label="Focus session",
            label_baseline="Focus session",
            category=TimeLogCategory.OTHER,
            mode=TimeLogMode.COUNTDOWN,
        )

    selected = next((candidate for candidate in open_tasks if candidate.uid == template.task_uid), None)
    return FocusFormValues(
        task_uid=selected.uid if selected is not None else None,
        label=template.label,
        label_baseline=template.label_baseline,
        category=template.category,
        mode=template.mode,
    )


def focus_label_for_task(
    current_label: str,
    baseline_label: str,
    task_label: str,
) -> str:
    """Update a task-derived label while preserving a custom label."""
    if current_label == baseline_label:
        return task_label
    return current_label


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
# Tracks the selected task UID independently of the display label so
# the label auto-update only runs when the task actually changes.
_FOCUS_DIALOG_SELECTED_TASK_UID_KEY = "_foc_selected_task_uid"
# Marks that this dialog launch has already been seeded. Streamlit
# reruns the dialog body after widget interactions; reseeding on every
# rerun would overwrite the user's in-flight changes.
_FOCUS_DIALOG_OPEN_KEY = "_foc_open"

_FOCUS_MODE_COUNTDOWN = "Count down"
_FOCUS_MODE_COUNTUP = "Count up"

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


def _session_username() -> str:
    """Return the authenticated username from session state."""
    username = st.session_state.get("username")
    if not isinstance(username, str) or not username:
        raise RuntimeError("focus template requires an authenticated session")
    return username


def _seed_focus_dialog_state(values: FocusFormValues, tasks: list[Task]) -> None:
    """Seed the disposable focus-form widget state for one launch."""
    task_label = "(unaligned)"
    for candidate in tasks:
        if candidate.uid == values.task_uid:
            task_label = candidate.title
            break

    st.session_state[_FOCUS_DIALOG_TASK_KEY] = task_label
    st.session_state[_FOCUS_DIALOG_LABEL_KEY] = values.label
    st.session_state[_FOCUS_DIALOG_LABEL_BASELINE_KEY] = values.label_baseline
    st.session_state[_FOCUS_DIALOG_CAT_KEY] = values.category.value
    st.session_state[_FOCUS_DIALOG_MODE_KEY] = (
        _FOCUS_MODE_COUNTUP if values.mode == TimeLogMode.COUNTUP else _FOCUS_MODE_COUNTDOWN
    )
    st.session_state[_FOCUS_DIALOG_SELECTED_TASK_UID_KEY] = values.task_uid
    st.session_state[_FOCUS_DIALOG_OPEN_KEY] = True


def _clear_focus_dialog_state() -> None:
    """Drop the focus-create dialog widget keys from session state.

    The per-user focus template is intentionally retained. It is
    separate from these disposable widget values and is replaced only
    after a successful focus-form start.
    """
    for key in (
        _FOCUS_DIALOG_TASK_KEY,
        _FOCUS_DIALOG_LABEL_KEY,
        _FOCUS_DIALOG_LABEL_BASELINE_KEY,
        _FOCUS_DIALOG_MODE_KEY,
        _FOCUS_DIALOG_CAT_KEY,
        _FOCUS_DIALOG_SELECTED_TASK_UID_KEY,
        _FOCUS_DIALOG_OPEN_KEY,
    ):
        st.session_state.pop(key, None)


@st.dialog("Start focus session", width="large", on_dismiss=_clear_focus_dialog_state)
def focus_create_dialog(*, task: Task | None = None, use_previous_template: bool = False) -> None:
    """Open the new-session dialog. Call from a button body.

    When ``task`` is supplied, the dialog pre-selects it, defaults
    the label to ``task.title`` (editable), and defaults the
    category to ``WORK``. The explicit task takes precedence over any
    previous template.

    A generic launch with ``use_previous_template=True`` starts from
    the current user's last successfully started focus form. A missing
    task falls back to unaligned while retaining the template's other
    values.

    Widget state is seeded only on the first execution of a dialog
    launch. Streamlit re-runs the dialog body when the user interacts
    with a widget, and reseeding on every rerun would erase the
    user's in-flight changes.
    """
    facade = get_facade()
    caldav = facade.caldav

    # Task list mirrors the Focus page: open tasks (NEEDS_ACTION +
    # IN_PROGRESS). Already-completed and canceled tasks are not
    # useful alignment targets.
    tasks = [t for t in caldav.list_tasks() if t.status in (TaskStatus.NEEDS_ACTION, TaskStatus.IN_PROGRESS)]
    task_options: list[Task | None] = [None, *tasks]
    task_labels = ["(unaligned)"] + [t.title for t in tasks]

    if _FOCUS_DIALOG_OPEN_KEY not in st.session_state:
        template = None
        if use_previous_template:
            template = load_focus_template(st.session_state, _session_username())
        values = resolve_focus_form_values(
            template=template,
            open_tasks=tasks,
            explicit_task=task,
        )
        _seed_focus_dialog_state(values, tasks)

    selected_label = st.selectbox(
        "Task",
        task_labels,
        key=_FOCUS_DIALOG_TASK_KEY,
        help="Pick a task to count this session against it, or leave unaligned.",
    )
    selected_task = task_options[task_labels.index(selected_label)]
    selected_task_uid = selected_task.uid if selected_task is not None else None

    # Label and category defaults depend on whether a task is
    # selected. The label is editable; the category is the
    # recommended start but the user can pick anything.
    label_default = selected_task.title if selected_task is not None else "Focus session"
    category_options = [c.value for c in TimeLogCategory]

    # Auto-update the label only when the selected task changes. The
    # baseline records the value we last set automatically; if the
    # current label still equals it, the user has not edited, so we
    # replace it with the new task's expected label. If they have
    # edited, the comparison fails and the typed value is preserved.
    current_label = st.session_state.get(_FOCUS_DIALOG_LABEL_KEY, label_default)
    baseline_label = st.session_state.get(_FOCUS_DIALOG_LABEL_BASELINE_KEY, label_default)
    if not isinstance(current_label, str):
        current_label = label_default
    if not isinstance(baseline_label, str):
        baseline_label = label_default
    previous_task_uid = st.session_state.get(_FOCUS_DIALOG_SELECTED_TASK_UID_KEY)
    if previous_task_uid != selected_task_uid:
        st.session_state[_FOCUS_DIALOG_LABEL_KEY] = focus_label_for_task(
            current_label,
            baseline_label,
            label_default,
        )
        st.session_state[_FOCUS_DIALOG_LABEL_BASELINE_KEY] = label_default
        st.session_state[_FOCUS_DIALOG_SELECTED_TASK_UID_KEY] = selected_task_uid

    label = st.text_input(
        "Session label",
        key=_FOCUS_DIALOG_LABEL_KEY,
        help=(
            "Shown on the active session card and in the time log. "
            "Defaults to the task summary when a task is selected."
        ),
    )
    mode_label = st.radio(
        "Mode",
        [_FOCUS_MODE_COUNTDOWN, _FOCUS_MODE_COUNTUP],
        key=_FOCUS_DIALOG_MODE_KEY,
        horizontal=True,
    )
    is_countdown = mode_label == _FOCUS_MODE_COUNTDOWN
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
        username = _session_username()
        entry = caldav.create_time_log_entry(
            build_focus_session_entry(
                label=label_clean,
                task=selected_task,
                mode=mode,
                countdown_minutes=int(duration) if is_countdown else None,
                category=chosen_category,
            )
        )
        label_baseline = st.session_state.get(_FOCUS_DIALOG_LABEL_BASELINE_KEY, label_clean)
        if not isinstance(label_baseline, str):
            label_baseline = label_clean
        save_focus_template(
            st.session_state,
            username,
            FocusTemplate(
                label=label_clean,
                task_uid=selected_task_uid,
                category=chosen_category,
                mode=mode,
                label_baseline=label_baseline,
            ),
        )
        # Manual-testing convenience: notify on Start so the user
        # does not have to wait 30 minutes to see the wiring
        # work. ADR-027. ``_notify_safely`` is the same
        # try/except wrapper the focus page uses; we duplicate
        # the one-line try/except here rather than cross-import
        # from the page module because that would create a
        # ui-pages -> ui-shared circular import.
        try:
            task_title = selected_task.title if selected_task is not None else None
            payload = messages.session_started(entry, task_title=task_title)
            title, body, tags = payload
            facade.notify.send(title, body, tags=tags)
        except Exception as exc:
            logger.warning("ntfy notification failed: %s", exc)
        # Starting a session against a task means the task is now
        # being worked on; flip it to IN_PROGRESS so the task list
        # reflects it. Skip the write when it is already there.
        if selected_task is not None and selected_task.status != TaskStatus.IN_PROGRESS:
            caldav.update_task(selected_task.uid, TaskUpdate(status=TaskStatus.IN_PROGRESS))
        _clear_focus_dialog_state()
        st.toast(f"Started: {entry.label}")
        # Land on the Focus page so the user sees the session ticking.
        # No-op when started from the Focus page itself.
        st.switch_page("pages/focus.py")
    if action_cols[1].button("Cancel", key="_foc_cancel"):
        _clear_focus_dialog_state()
        st.rerun()


@st.dialog("Mark task completed?", on_dismiss="rerun")
def focus_completion_dialog(*, task_uid: str, task_title: str) -> None:
    """Open the post-session completion prompt. Call from a button body.

    Triggered by the Focus page after a session with a linked
    task ends (auto-stop or manual stop). Accepting marks the
    linked task ``COMPLETED`` via ``facade.complete_task``; this
    also fires the XP event (ADR-026). Declining leaves the
    task untouched. Both branches clear the
    ``focus_completion_pending`` flag so the dialog does not
    re-open on the next render.
    """
    facade = get_facade()
    st.write(f"Mark task **{task_title}** as completed now that the focus session on it has ended?")
    cols = st.columns(2)
    if cols[0].button("Yes, complete", type="primary", key="_focpl_yes"):
        facade.complete_task(task_uid)
        st.session_state.pop(FOCUS_COMPLETION_PENDING_KEY, None)
        st.toast(f"Completed: {task_title}")
        st.rerun()
    if cols[1].button("Not yet", key="_focpl_no"):
        st.session_state.pop(FOCUS_COMPLETION_PENDING_KEY, None)
        st.rerun()


# -----------------------------------------------------------------------------
# Journal view helper
# -----------------------------------------------------------------------------


def render_journal_entry_view(
    entry: Entry,
    *,
    on_edit: Callable[[Entry], None] | None = None,
    show_delete: bool = False,
) -> None:
    """Render the view-only display of a journal entry.

    Shared between the journal page (its own detail view) and the
    dashboard's daily-journal expander, so the two surfaces stay in
    lockstep. Renders the title row (``## {title}`` plus a type
    badge when the entry has a type), the linked task when the entry
    points at one, the markdown body (or an "Empty entry." caption),
    and an Edit button. With ``show_delete=True`` also renders a
    Delete button that opens :func:`delete_confirm_dialog`.

    The Edit button calls ``on_edit(entry)`` when supplied;
    otherwise it falls back to the same-page behaviour of setting
    ``?edit=1`` and rerunning, which is what the journal page
    itself uses to swap into its edit mode. Callers that surface
    the entry outside the journal page (the dashboard) pass a
    callback that calls :func:`streamlit.switch_page` to navigate
    to the journal page in edit mode.

    Widget keys are namespaced with ``jev_`` and the entry UID so
    the helper can be invoked multiple times on the same page
    without key collisions.
    """
    title_text = entry.title or entry.date.strftime("%A, %B %d, %Y")
    title_row = st.container(horizontal=True, vertical_alignment="center")
    with title_row:
        st.markdown(f"## {title_text}")
        if entry.type is not None:
            st.badge(entry.type.value, color="blue")

    if entry.task_id:
        linked = get_facade().caldav.get_task(entry.task_id)
        if linked is not None:
            st.caption(f"Task: {linked.title} ({linked.uid})")
        else:
            st.caption(f"Task: {entry.task_id} (missing)")

    if entry.content.strip():
        st.markdown(entry.content)
    else:
        st.caption("Empty entry.")

    st.divider()
    st.write("")
    if st.button(
        "Edit",
        key=f"jev_edit_{entry.uid}",
        icon=":material/edit:",
        type="primary",
    ):
        if on_edit is not None:
            on_edit(entry)
        else:
            st.query_params["edit"] = "1"
            st.rerun()
    if show_delete:
        if st.button(
            "Delete",
            key=f"jev_del_{entry.uid}",
            icon=":material/delete:",
        ):
            delete_confirm_dialog(
                f"entry {entry.date.isoformat()}",
                lambda: get_facade().journal.delete_entry(entry.uid),
            )
