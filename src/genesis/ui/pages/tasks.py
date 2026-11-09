"""Tasks page: global list (default) and task detail (when ?uid=<uid>).

The page renders one of two layouts based on the ``uid`` query param.
The list view is the shared task table with inline edits and per-row
Open / Focus / Log Session / Edit / Delete actions. The detail view
shows the task itself plus the journal entries linked to it.

The detail view is reached by setting ``?uid=<uid>``, either from the
list's Open action (which switches to this page) or by editing the
URL. It stays off the sidebar, like the Project detail view.
"""

from __future__ import annotations

import streamlit as st

from genesis.entities import Task, TaskStatus
from genesis.journal.facade import Entry
from genesis.tz import format_local
from genesis.ui._render import (
    delete_confirm_dialog,
    journal_create_dialog,
    status_badge,
    task_create_dialog,
    task_edit_dialog,
)
from genesis.ui._state import get_facade
from genesis.ui._table import (
    TASK_TABLE_DEFAULT_SORT_KEYS,
    render_filterable_tasks_table,
    render_journal_table,
    request_journal_entry,
)

facade = get_facade()
caldav = facade.caldav
journal = facade.journal


# -----------------------------------------------------------------------------
# List view
# -----------------------------------------------------------------------------


def _render_list() -> None:
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
        sort_keys=TASK_TABLE_DEFAULT_SORT_KEYS,
    )


# -----------------------------------------------------------------------------
# Detail view
# -----------------------------------------------------------------------------


def _project_name(task: Task) -> str:
    """Return the task's project name, or a placeholder."""
    if not task.project_id:
        return "(none)"
    project = next((p for p in caldav.list_projects() if p.uid == task.project_id), None)
    return project.name if project is not None else "(missing)"


def _render_metadata(task: Task) -> None:
    """Render the task's read-only metadata as a grid of captions."""
    fields: list[tuple[str, str]] = [
        ("Project", _project_name(task)),
        ("Priority", str(task.priority) if task.priority is not None else "-"),
        ("Assigned", task.assigned_date.isoformat() if task.assigned_date else "-"),
        ("Deadline", format_local(task.hard_deadline) or "-"),
        ("Scheduled", format_local(task.scheduled_start) or "-"),
        ("Duration", f"{task.scheduled_duration} min" if task.scheduled_duration else "-"),
        ("Completed", format_local(task.completed_at) or "-"),
    ]
    cols = st.columns(4)
    for index, (label, value) in enumerate(fields):
        with cols[index % 4]:
            st.caption(label)
            st.write(value)


def _render_journal_section(uid: str) -> None:
    """Render the journal entries linked to the task."""
    st.subheader("Journal Entries")
    if st.button("+ Add Journal Entry", key=f"td_add_journal_{uid}"):
        journal_create_dialog(default_type="task", default_task_id=uid)

    entries = journal.list_entries(task_id=uid)

    def _handle_action(label: str, entry: Entry) -> None:
        if label == "Open":
            # The navigation is deferred to the script body: st.switch_page
            # inside the row-action callback is a no-op and Streamlit warns
            # "Calling st.rerun() within a callback is a no-op.".
            request_journal_entry(entry.uid)
        elif label == "Delete":
            delete_confirm_dialog(
                f"entry {entry.date.isoformat()}",
                lambda entry_uid=entry.uid: journal.delete_entry(entry_uid),
            )

    render_journal_table(entries, key=f"task_journals_{uid}", on_action=_handle_action)


def _render_detail(uid: str) -> None:
    task = caldav.get_task(uid)
    if task is None:
        st.error("Task not found.")
        if st.button("Back to Tasks", key="td_missing_back"):
            st.query_params.pop("uid", None)
            st.rerun()
        return

    # Breadcrumb on the first row.
    st.caption("Productivity > Tasks")
    # Subtle back link directly under the breadcrumb, above the title.
    if st.button(
        "Back",
        key="td_back",
        icon=":material/arrow_back:",
        type="tertiary",
    ):
        st.query_params.pop("uid", None)
        st.rerun()
    # Title on its own row.
    st.title(task.title)
    # Status badge on its own row.
    status_badge(task.status)

    if task.description:
        st.write(task.description)

    # Edit + Delete on a single row beneath the description, left-aligned.
    with st.container(horizontal=True, horizontal_alignment="left"):
        if st.button("Edit", key="td_edit"):
            task_edit_dialog(uid)
        if st.button("Delete", key="td_del"):
            delete_confirm_dialog(
                f"task '{task.title}'",
                lambda: caldav.delete_task(uid),
            )

    st.write("")

    _render_metadata(task)

    st.write("")

    _render_journal_section(uid)


# -----------------------------------------------------------------------------
# Dispatch
# -----------------------------------------------------------------------------


task_uid = st.query_params.get("uid")

if task_uid:
    _render_detail(task_uid)
else:
    _render_list()
