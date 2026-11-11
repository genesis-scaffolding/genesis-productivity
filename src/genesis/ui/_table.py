"""Shared Streamlit data widgets for the genesis UI.

This module hosts the canonical tables: the tasks table, the
projects table, the journal table, and the contacts table. Page
modules pass in their filtered rows and a unique widget-key
prefix; the tables handle inline edits, per-row actions, and the
empty-state message.

A table hides its Actions column when the caller does not supply
an action handler. The onboarding wizard uses that: its lists are
read-only views of the account, and the Actions column would let
the user leave the wizard.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

import pandas as pd
import streamlit as st

from genesis.entities import Contact, ContactUpdate, Project, ProjectStatus, ProjectUpdate, Task, TaskStatus, TaskUpdate
from genesis.journal.facade import Entry
from genesis.tz import to_local, to_utc
from genesis.ui._render import (
    EDIT_SCOPE_ALL_FUTURE,
    EDIT_SCOPE_CURRENT,
    delete_confirm_dialog,
    focus_create_dialog,
    session_log_dialog,
    task_edit_dialog,
)
from genesis.ui._state import get_facade

if TYPE_CHECKING:
    from collections.abc import Callable

    from genesis.caldav import CalDAVFacade


# Default sort for task tables (project detail, global tasks, dashboard).
# Each entry is one sort level:
#
#   (field_name, none_default)
#   (field_name, none_default, descending)
#
# ``none_default`` substitutes for ``None`` when building the sort key
# so ``None`` rows land in a deterministic slot. The optional third
# element flips the direction for that level (True = descending).
#
# Levels, in display order:
#   1. hard_deadline ascending, None -> datetime.max in UTC
#      (no deadline sorts last; the field is typed as tz-aware UTC
#      datetime, so the placeholder must also be tz-aware).
#   2. priority DESCENDING, None -> 5
#      (larger integer = more urgent task at the top. The genesis
#      convention is the opposite of RFC 5545 (iCalendar PRIORITY)
#      where 1 is the highest value; see ``Task.priority``'s
#      docstring for the full rationale. PRIORITY 9 was historically
#      rendered above PRIORITY 5 in ``pages/projects.py`` via
#      ``-(t.priority or 5)``; this constant preserves that UX.)
#   3. created ascending, None -> datetime.max in UTC
#      (oldest first; missing creation date sorts last).
#
# Pages pass this through the ``sort_keys`` parameter on
# ``render_tasks_table`` / ``render_filterable_tasks_table`` to enable
# the cache-based stable sort (ADR-023).
type SortKey = tuple[str, Any] | tuple[str, Any, bool]
_DATETIME_MAX_UTC = datetime.max.replace(tzinfo=UTC)
TASK_TABLE_DEFAULT_SORT_KEYS: list[SortKey] = [
    ("hard_deadline", _DATETIME_MAX_UTC),
    ("priority", 5, True),
    ("created", _DATETIME_MAX_UTC),
]


# -----------------------------------------------------------------------------
# Deferred page switches
# -----------------------------------------------------------------------------

# A table row action runs inside a widget callback, and Streamlit
# refuses to navigate from there: ``st.switch_page`` inside an
# ``on_click`` callback raises the rerun exception inside the
# callback, so the queued navigation is dropped and Streamlit warns
# "Calling st.rerun() within a callback is a no-op.". The callback
# therefore only records the target; the table performs the switch
# from the script body, right after it rendered the table.
_PENDING_PAGE_SWITCH_KEY = "_pending_page_switch"


def request_page_switch(page: str, *, query_params: dict[str, str]) -> None:
    """Record a page switch a table row action asked for.

    Call from a widget callback. The switch is performed by
    :func:`run_pending_page_switch` once the script body runs.
    """
    st.session_state[_PENDING_PAGE_SWITCH_KEY] = (page, dict(query_params))


def run_pending_page_switch() -> None:
    """Perform the page switch a table row action asked for, if any.

    Call from the script body. A widget callback cannot navigate:
    ``st.switch_page`` there is a no-op and Streamlit warns about it.
    """
    pending = st.session_state.pop(_PENDING_PAGE_SWITCH_KEY, None)
    if pending is None:
        return
    page, query_params = pending
    st.switch_page(page, query_params=query_params)


# -----------------------------------------------------------------------------
# Task table
# -----------------------------------------------------------------------------


# Key the inline-edit handler stores an error message in. A widget
# callback cannot render on the page itself, so the message is handed
# to the script body through the session state.
_INLINE_EDIT_ERROR_KEY = "_inline_edit_error"


def request_task_detail(uid: str) -> None:
    """Ask for the Tasks page in its detail state.

    The task table is shared by the Tasks, Projects, Dashboard, and
    Calendar pages, so the Open action has to leave the current page:
    setting a ``uid`` query parameter in place would leak the task
    detail state onto a page that has no such view. ``st.switch_page``
    resolves the target relative to the main script, so the Tasks page
    lives at ``pages/tasks.py`` from that base.

    The switch itself is deferred to the script body: a widget
    callback cannot navigate (see :func:`request_page_switch`).
    """
    request_page_switch("pages/tasks.py", query_params={"uid": uid})


def request_journal_entry(uid: str) -> None:
    """Ask for the Journal page in its entry view.

    The Journal page owns opening, editing and deleting an entry; the
    task page only links to it. The switch itself is deferred to the
    script body (see :func:`request_page_switch`).
    """
    request_page_switch("pages/journal.py", query_params={"uid": uid})


def handle_task_action(label: str, task: Task, *, caldav: CalDAVFacade) -> None:
    """Run the action behind a task table row button.

    "Open" navigates to the task detail view; the other labels open
    the matching dialog.
    """
    if label == "Open":
        request_task_detail(task.uid)
    elif label == "Edit":
        task_edit_dialog(task.uid)
    elif label == "Focus":
        focus_create_dialog(task=task)
    elif label == "Log Session":
        session_log_dialog(task=task)
    elif label == "Delete":
        delete_confirm_dialog(
            f"task '{task.title}'",
            lambda: caldav.delete_task(task.uid),
        )


def _parse_sort_key_entry(entry: tuple) -> tuple[str, Any, bool]:
    """Split a ``sort_keys`` entry into ``(field, none_default, reverse)``.

    Accepts a 2-tuple ``(field, none_default)`` (ascending) or a 3-tuple
    ``(field, none_default, reverse)``. Raises ``ValueError`` for any
    other length so callers get a clear failure rather than a silent
    mis-sort.
    """
    if len(entry) == 2:
        field, none_default = entry  # type: ignore[misc]
        return field, none_default, False
    if len(entry) == 3:
        field, none_default, reverse = entry  # type: ignore[misc]
        return field, none_default, bool(reverse)
    raise ValueError(
        f"sort_keys entry must be (field, none_default) or (field, none_default, reverse); got {entry!r}",
    )


def _apply_cached_sort(
    tasks: list[Task],
    *,
    cached_uids: list[str] | None,
    sort_keys: list[SortKey],
) -> tuple[list[Task], list[str]]:
    """Pure sort helper: cache-aware stable ordering for tasks.

    With ``cached_uids=None`` (first render), computes the sort from
    ``sort_keys`` and returns the resulting UID list. The widget writes
    that list into ``st.session_state``.

    With a non-None ``cached_uids`` (subsequent renders), walks the
    cached UID list, emits tasks in that order, and appends any tasks
    whose UID is not in the cache (e.g. a newly created task) at the
    end. The returned UID list mirrors the emitted order so the caller
    can keep the cache in sync.

    This is the regression fix for the data_editor row-drift bug
    (streamlit/streamlit#17026): while the cache is present, the
    order never shifts under the widget, so positional edits in
    ``edited_rows`` keep mapping to the same tasks.

    Each level can be ascending or descending (see ``SortKey``). The
    sort is composed by sorting once per level in reverse order --
    Python's sort is stable, so this is equivalent to a multi-key
    sort and supports ``reverse`` per level without custom comparison
    logic (which would require a lambda, which the page API forbids).
    """
    if cached_uids is None:
        ordered = list(tasks)
        for entry in reversed(sort_keys):
            field, none_default, reverse = _parse_sort_key_entry(entry)

            def _single_sort_key(
                t: Task,
                f: str = field,
                n: Any = none_default,
            ) -> Any:
                value = getattr(t, f, None)
                return n if value is None else value

            ordered.sort(key=_single_sort_key, reverse=reverse)
        return ordered, [t.uid for t in ordered]

    by_uid = {t.uid: t for t in tasks}
    out: list[Task] = []
    new_cache: list[str] = []
    seen: set[str] = set()
    for uid in cached_uids:
        if uid in by_uid:
            out.append(by_uid[uid])
            new_cache.append(uid)
            seen.add(uid)
    for t in tasks:
        if t.uid not in seen:
            out.append(t)
            new_cache.append(t.uid)
            seen.add(t.uid)
    return out, new_cache


def _stable_sort_first_render(
    tasks: list[Task],
    *,
    sort_keys: list[SortKey],
    cache_key: str,
) -> list[Task]:
    """Session_state wrapper around ``_apply_cached_sort``.

    Reads ``st.session_state[cache_key]`` for the cached UID list,
    delegates to ``_apply_cached_sort``, writes a new cache only on
    first render (when the cached list was missing), and returns the
    ordered tasks.
    """
    cached = st.session_state.get(cache_key)
    ordered, new_cache = _apply_cached_sort(
        tasks,
        cached_uids=cached,
        sort_keys=sort_keys,
    )
    if cached is None:
        st.session_state[cache_key] = new_cache
    return ordered


def render_filterable_tasks_table(
    tasks: list[Task],
    *,
    caldav: CalDAVFacade,
    key: str,
    show_project_column: bool = True,
    filter_key: str | None = None,
    default_statuses: list[TaskStatus] | None = None,
    sort_keys: list[SortKey] | None = None,
    show_actions: bool = True,
) -> None:
    """Render status filter pills above a tasks table.

    When ``filter_key`` is None, render the table only (no pills).
    When set, render the pills first and filter ``tasks`` to the
    selected statuses before passing to ``render_tasks_table``.
    ``default_statuses`` seeds the initial pill selection (defaults
    to all statuses when None).

    ``sort_keys`` is forwarded to ``render_tasks_table`` and enables
    the cache-based stable sort (ADR-023). When provided, the table
    freezes its display order on first render, does not re-sort on
    inline edits, and shows a Re-sort button to force a refresh.

    An explicit edit-scope control is rendered above the table
    (ADR-033). Inline edits of a recurring task follow the selected
    scope; the default is "this instance".
    """
    statuses_all = [s.value for s in TaskStatus]
    default = [s.value for s in (default_statuses or TaskStatus)]
    if filter_key is not None:
        active_statuses = st.session_state.get(filter_key, default)
        active_statuses = st.pills(
            "Status",
            options=statuses_all,
            default=active_statuses,
            selection_mode="multi",
            key=filter_key,
        )
        st.write("")
        if active_statuses:
            selected = {TaskStatus(v) for v in active_statuses}
            tasks = [t for t in tasks if t.status in selected]
    render_tasks_table(
        tasks,
        caldav=caldav,
        key=key,
        show_project_column=show_project_column,
        sort_keys=sort_keys,
        show_actions=show_actions,
    )


def _render_edit_scope_control(key: str) -> None:
    """Render the inline edit-scope control for recurring tasks.

    An inline edit is an edit path, so it must not silently pick a
    scope. The control is stored under ``f"{key}__edit_scope"`` and
    read by the inline-edit handler.
    """
    scope_key = f"{key}__edit_scope"
    st.selectbox(
        "Inline edit scope",
        options=[EDIT_SCOPE_CURRENT, EDIT_SCOPE_ALL_FUTURE],
        key=scope_key,
        format_func=lambda v: "This instance" if v == EDIT_SCOPE_CURRENT else "All future instances",
        help="Applies inline edits of recurring tasks to this occurrence only, or to every later occurrence.",
    )


def _edit_scope(key: str) -> str:
    """Return the inline edit scope selected for the table."""
    scope = st.session_state.get(f"{key}__edit_scope", EDIT_SCOPE_CURRENT)
    return scope if scope in (EDIT_SCOPE_CURRENT, EDIT_SCOPE_ALL_FUTURE) else EDIT_SCOPE_CURRENT


def _without_status(update: TaskUpdate) -> TaskUpdate:
    """Return the update without its status field.

    A status change is a per-occurrence operation, so it never rides
    along with a future-scope edit.
    """
    dump = update.model_dump(exclude_unset=True)
    dump.pop("status", None)
    return TaskUpdate(**dump)


def plan_inline_edit(key: str, task: Task, update: TaskUpdate) -> tuple[str, TaskUpdate, TaskUpdate | None]:
    """Decide how an inline edit is applied.

    Returns ``(scope, content_update, status_update)``. The scope
    comes from the table's explicit scope control; it only applies to
    a task that belongs to a recurrence series. A status change is
    always a per-occurrence operation, so in the all-future scope the
    content goes to the later occurrences and the status to the
    selected one.
    """
    scope = _edit_scope(key)
    if scope != EDIT_SCOPE_ALL_FUTURE:
        return scope, update, None
    if not task.recurrence_series_uid:
        # Not a recurrent task: the scope does not apply.
        return EDIT_SCOPE_CURRENT, update, None
    if update.status is None:
        return scope, update, None
    return scope, _without_status(update), TaskUpdate(status=update.status)


def apply_inline_edit(
    caldav: CalDAVFacade,
    key: str,
    task: Task,
    update: TaskUpdate,
) -> str | None:
    """Apply an inline table edit and return an error message, if any.

    The scope comes from the table's explicit scope control. An edit
    the chosen scope rejects is *not* applied under another scope: the
    error is returned so the caller can surface it, because silently
    routing the edit to a different scope would apply a change the
    user did not ask for.
    """
    scope, content_update, status_update = plan_inline_edit(key, task, update)
    if scope != EDIT_SCOPE_ALL_FUTURE:
        caldav.update_task(task.uid, update)
        return None
    try:
        caldav.update_task_scoped(task.uid, content_update, scope=EDIT_SCOPE_ALL_FUTURE)
    except ValueError as exc:
        return str(exc)
    if status_update is not None:
        caldav.update_task(task.uid, status_update)
    return None


def recurrence_labels(tasks: list[Task], caldav: CalDAVFacade) -> dict[str, tuple[str, str, str]]:
    """Return per-task (recurrence, role, series) labels.

    The series of every distinct series is fetched once, not once per
    task. The label pairs are:

    - ``recurrence``: e.g. "Monthly, 4 ahead";
    - ``role``: "Current", "Future 1 of 4", or "History";
    - ``series``: the series UID (empty for ordinary tasks).
    """
    series_uids = {t.recurrence_series_uid for t in tasks if t.recurrence_series_uid}
    states: dict[str, Any] = {}
    for uid in series_uids:
        state = caldav.get_series_state(uid)
        if state is not None:
            states[uid] = state

    labels: dict[str, tuple[str, str, str]] = {}
    for task in tasks:
        series_uid = task.recurrence_series_uid
        state = states.get(series_uid or "")
        if state is None:
            labels[task.uid] = ("", "", "")
            continue
        recurrence_label = f"{state.frequency.value.capitalize()}, {state.materialized_future_count} ahead"
        future_uids = [occurrence.uid for occurrence in state.future_occurrences]
        if task.uid == state.current_task_uid:
            role = "Current"
        elif task.uid in future_uids:
            role = f"Future {future_uids.index(task.uid) + 1} of {len(future_uids)}"
        else:
            role = "History"
        labels[task.uid] = (recurrence_label, role, series_uid or "")
    return labels


def render_tasks_table(
    tasks: list[Task],
    *,
    caldav: CalDAVFacade,
    key: str,
    show_project_column: bool = True,
    sort_keys: list[SortKey] | None = None,
    show_actions: bool = True,
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
        sort_keys: Optional list of sort-key entries that enables the
            cache-based stable sort (ADR-023). Each entry is either
            ``(field_name, none_default)`` (ascending) or
            ``(field_name, none_default, descending)`` (descending).
            On first render the widget sorts ``tasks`` by ``sort_keys``
            and caches the UID list in
            ``st.session_state[f"{key}__sort"]``. Subsequent renders
            reuse that cache so the display order does not shift under
            inline edits — this is the fix for the data_editor
            row-drift bug (streamlit/streamlit#17026). A "Re-sort"
            button is shown above the table; clicking it pops the
            cache and reruns.
        show_actions: Show the per-row Actions column. Pass
            ``False`` to hide it; the table then has no way to open
            an edit or delete dialog, which is what the onboarding
            wizard wants.

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

    # An inline edit of a recurring task needs an explicit scope, so
    # the control is rendered whenever the table holds at least one
    # occurrence of a series.
    if any(t.recurrence_series_uid for t in tasks):
        _render_edit_scope_control(key)

    if sort_keys is not None:
        sort_cache_key = f"{key}__sort"
        if st.button(
            "Re-sort",
            key=f"{key}__resort_btn",
            help="Re-sort the table by the configured sort keys.",
        ):
            st.session_state.pop(sort_cache_key, None)
            st.rerun()
        tasks = _stable_sort_first_render(
            tasks,
            sort_keys=sort_keys,
            cache_key=sort_cache_key,
        )

    statuses_all = [s.value for s in TaskStatus]

    projects_list = list(caldav.list_projects()) if show_project_column else []
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
    recurrence_info = recurrence_labels(tasks, caldav)
    for t in tasks:
        # Strip tz for the dataframe; on edit, the value comes back as
        # naive local and we convert via to_utc before storing.
        sched = to_local(t.scheduled_start) if t.scheduled_start else None
        # Deadline is a date-only concept for the user. We still store
        # a tz-aware UTC datetime on the wire (the entity field is
        # ``datetime``), with the time fixed to 23:59:59 local so two
        # tasks with the same calendar date sort to the same slot and
        # the next level (priority) breaks ties. See _handle_inline_edit
        # for the write path.
        dl_local = to_local(t.hard_deadline) if t.hard_deadline else None
        recurrence_label, role_label, series_label = recurrence_info.get(t.uid, ("", "", ""))
        row: dict = {
            "uid": t.uid,
            "Title": t.title,
            "Priority": t.priority if t.priority is not None else 5,
            "Scheduled": sched.replace(tzinfo=None) if sched else None,
            "Deadline": dl_local.date() if dl_local else None,
            "Assigned": t.assigned_date,
            "Status": t.status.value,
            "Recurrence": recurrence_label,
            "Role": role_label,
            "Series": series_label,
        }
        if show_project_column:
            row["Project"] = _project_label(t.project_id)
        if show_actions:
            row["actions"] = ["Open", "Focus", "Log Session", "Edit", "Delete"]
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
        handle_task_action(label, target, caldav=caldav)

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
            completing = False
            if "Title" in changes:
                update = update.model_copy(update={"title": str(changes["Title"])})
                touched = True
            if "Priority" in changes:
                update = update.model_copy(update={"priority": int(changes["Priority"])})
                touched = True
            if "Status" in changes:
                new_status = TaskStatus(str(changes["Status"]))
                update = update.model_copy(update={"status": new_status})
                touched = True
                completing = new_status == TaskStatus.COMPLETED
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
                    # Deadline is a date-only UX: snap the picked date
                    # to 23:59:59 local before storing. Two tasks with
                    # the same calendar date then share the same stored
                    # datetime and the sort's priority level breaks the
                    # tie, which is what the user expects when scanning
                    # the table.
                    py_date: date = deadline_ts.date()  # type: ignore[assignment]
                    py_dt = datetime.combine(py_date, time(23, 59, 59))
                    deadline = to_utc(py_dt)
                update = update.model_copy(update={"hard_deadline": deadline})
                touched = True
            if "Assigned" in changes:
                new_val = changes["Assigned"]
                assigned: object = None if pd.isna(new_val) else pd.Timestamp(new_val).date()
                update = update.model_copy(update={"assigned_date": assigned})
                touched = True
            if "Project" in changes and show_project_column:
                new_label = str(changes["Project"])
                if new_label in project_label_to_uid:
                    new_uid = project_label_to_uid[new_label]
                    if new_uid is None:
                        update = update.model_copy(update={"clear_project_id": True})
                    else:
                        update = update.model_copy(update={"project_id": new_uid})
                    touched = True

            if not touched:
                continue
            # A status change is always a per-occurrence operation; the
            # inline scope only governs the content columns.
            error = apply_inline_edit(caldav, key, original, update)
            if error is not None:
                # The chosen scope rejected the edit. The callback
                # cannot render on the page, so the message is handed
                # to the script body through the session state. The
                # edit was not applied, so there is nothing to reward.
                st.session_state[_INLINE_EDIT_ERROR_KEY] = error
                continue
            # ADR-026: if the user just transitioned this task to
            # COMPLETED via inline edit, fire the XP event. The
            # intent is read from the changes dict (already known
            # above), so no extra get_task round-trip is needed.
            if completing:
                from genesis.ui._state import get_facade

                facade = get_facade()
                facade._fire_xp_safely(
                    facade.record_xp_event,
                    task_id=original.uid,
                    occurred_at=datetime.now(UTC),
                )
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
        "Priority": st.column_config.NumberColumn("Priority", min_value=1, max_value=9),
        "Scheduled": st.column_config.DatetimeColumn("Scheduled", format="YYYY-MM-DD HH:mm"),
        "Deadline": st.column_config.DateColumn("Deadline", format="YYYY-MM-DD"),
        "Assigned": st.column_config.DateColumn("Assigned", format="YYYY-MM-DD"),
        "Status": st.column_config.SelectboxColumn("Status", options=statuses_all, required=True),
        "Recurrence": st.column_config.TextColumn("Recurrence", disabled=True, width="small"),
        "Role": st.column_config.TextColumn("Occurrence", disabled=True, width="small"),
        "Series": st.column_config.TextColumn("Series", disabled=True, width="small"),
    }
    if show_project_column:
        column_config["Project"] = st.column_config.SelectboxColumn(
            "Project",
            options=project_labels,
            required=False,
            help=("Reassign task to another project. Select (none) to unassign."),
        )
    else:
        column_config["Project"] = None
    if show_actions:
        column_config["actions"] = st.column_config.ButtonColumn("Actions", on_click=_handle_action, key=action_key)
    else:
        column_config["actions"] = None

    st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        on_change=_handle_inline_edit,
        key=key,
    )

    # An inline edit that the chosen scope rejected leaves a message
    # behind. The callback cannot render it, so the script body does.
    inline_error = st.session_state.pop(_INLINE_EDIT_ERROR_KEY, None)
    if inline_error:
        st.error(inline_error)

    # A row action may have asked for another page. Perform the switch
    # here, in the script body: st.switch_page inside the row-action
    # callback is a no-op and Streamlit warns about it.
    run_pending_page_switch()


# -----------------------------------------------------------------------------
# Projects table
# -----------------------------------------------------------------------------


def render_projects_table(
    projects: list[Project],
    *,
    caldav: CalDAVFacade,
    key: str,
    on_action: Callable[[str, Project], None] | None = None,
) -> None:
    """Render the projects table.

    Columns: Name, Description, Start, Deadline, Status, and the
    number of tasks on the project. Name, Description, and Status
    are editable inline; the dates are read-only here (the project
    edit dialog owns them).

    ``on_action`` handles the per-row Actions buttons ("Open",
    "Delete"). When it is None the Actions column is hidden, which
    is what the onboarding wizard wants: its list is a view of the
    account, not a way out of the wizard.
    """
    if not projects:
        st.info("No projects to show.")
        return

    task_counts: dict[str, int] = {}
    for task in caldav.list_tasks():
        if task.project_id:
            task_counts[task.project_id] = task_counts.get(task.project_id, 0) + 1

    rows: list[dict] = []
    for p in projects:
        row: dict = {
            "uid": p.uid,
            "Name": p.name,
            "Description": p.description or "",
            "Start": p.start_date,
            "Deadline": p.deadline,
            "Status": p.status.value,
            "Tasks": task_counts.get(p.uid, 0),
        }
        if on_action is not None:
            row["actions"] = ["Open", "Delete"]
        rows.append(row)
    df = pd.DataFrame(rows)

    action_key = f"{key}_action"
    table_projects = projects

    def _handle_action() -> None:
        click = st.session_state.get(action_key)
        if not click or on_action is None:
            return
        row_idx = click["row"]
        label = click["label"]
        if row_idx >= len(table_projects):
            return
        on_action(label, table_projects[row_idx])

    def _handle_inline_edit() -> None:
        state = st.session_state.get(key) or {}
        edited_rows = state.get("edited_rows") or {}
        if not edited_rows:
            return
        for row_idx_str, changes in edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx >= len(table_projects):
                continue
            original = table_projects[row_idx]
            update = ProjectUpdate()
            touched = False
            if "Name" in changes:
                update = update.model_copy(update={"name": str(changes["Name"])})
                touched = True
            if "Description" in changes:
                update = update.model_copy(update={"description": str(changes["Description"]) or None})
                touched = True
            if "Status" in changes:
                update = update.model_copy(update={"status": ProjectStatus(str(changes["Status"]))})
                touched = True
            if not touched:
                continue
            caldav.update_project(original.uid, update)
        state["edited_rows"] = {}

    column_config: dict = {
        "uid": None,  # hidden
        "Name": st.column_config.TextColumn("Name", width="medium", required=True),
        "Description": st.column_config.TextColumn("Description", width="large"),
        "Start": st.column_config.DateColumn("Start", disabled=True, format="YYYY-MM-DD"),
        "Deadline": st.column_config.DateColumn("Deadline", disabled=True, format="YYYY-MM-DD"),
        "Status": st.column_config.SelectboxColumn(
            "Status",
            options=[s.value for s in ProjectStatus],
            required=True,
        ),
        "Tasks": st.column_config.NumberColumn("Tasks", disabled=True),
    }
    if on_action is not None:
        column_config["actions"] = st.column_config.ButtonColumn("Actions", on_click=_handle_action, key=action_key)

    st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        on_change=_handle_inline_edit,
        key=key,
    )


# -----------------------------------------------------------------------------
# Journal table
# -----------------------------------------------------------------------------


def render_journal_table(
    entries: list[Entry],
    *,
    key: str,
    on_action: Callable[[str, Entry], None] | None = None,
) -> None:
    """Render the journal entries table.

    The Title column is editable inline; the rest is read-only.

    ``on_action`` handles the per-row Actions buttons ("Open",
    "Delete"). When it is None the Actions column is hidden, which
    is what the onboarding wizard wants.
    """
    if not entries:
        st.info("No journal entries to show.")
        return

    rows: list[dict] = []
    for e in entries:
        row: dict = {
            "date": e.date,
            "type": e.type.value if e.type else "",
            "title": e.title or e.date.strftime("%A, %B %d, %Y"),
            "preview": (e.content.split("\n", 1)[0][:100] if e.content else ""),
            "uid": e.uid,
        }
        if on_action is not None:
            row["actions"] = ["Open", "Delete"]
        rows.append(row)
    df = pd.DataFrame(rows)

    action_key = f"{key}_action"
    table_entries = entries

    def _handle_action() -> None:
        click = st.session_state.get(action_key)
        if not click or on_action is None:
            return
        row_idx = click["row"]
        label = click["label"]
        if row_idx >= len(table_entries):
            return
        on_action(label, table_entries[row_idx])

    def _handle_inline_edit() -> None:
        state = st.session_state.get(key) or {}
        edited_rows = state.get("edited_rows") or {}
        if not edited_rows:
            return
        journal = get_facade().journal
        for row_idx_str, changes in edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx >= len(table_entries):
                continue
            entry = table_entries[row_idx]
            if "title" not in changes:
                continue
            entry.title = changes["title"] or None
            try:
                journal.save_entry(entry)
            except ValueError as exc:
                st.error(str(exc))
                return
        state["edited_rows"] = {}

    column_config: dict = {
        "date": st.column_config.DateColumn("Date", disabled=True, format="YYYY-MM-DD"),
        "type": st.column_config.TextColumn("Type", disabled=True),
        "title": st.column_config.TextColumn("Title", width="large"),
        "preview": st.column_config.TextColumn("Preview", disabled=True, width="large"),
        "uid": None,  # hidden
    }
    if on_action is not None:
        column_config["actions"] = st.column_config.ButtonColumn("Actions", on_click=_handle_action, key=action_key)

    st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        on_change=_handle_inline_edit,
        key=key,
    )

    # A row action may have asked for another page. Perform the switch
    # here, in the script body: st.switch_page inside the row-action
    # callback is a no-op and Streamlit warns about it.
    run_pending_page_switch()


# -----------------------------------------------------------------------------
# Contacts table
# -----------------------------------------------------------------------------


def render_contacts_table(
    contacts: list[Contact],
    *,
    key: str,
    on_action: Callable[[str, Contact], None] | None = None,
) -> None:
    """Render the contacts table.

    The Name and Organization columns are editable inline; email
    and phone are read-only here (the contact edit form owns them).

    ``on_action`` handles the per-row Actions buttons ("Open",
    "Delete"). When it is None the Actions column is hidden, which
    is what the onboarding wizard wants.
    """
    if not contacts:
        st.info("No contacts to show.")
        return

    rows: list[dict] = []
    for c in contacts:
        row: dict = {
            "name": c.name,
            "organization": c.organization or "",
            "primary_email": c.emails[0] if c.emails else "",
            "primary_phone": c.phones[0] if c.phones else "",
            "uid": c.uid,
        }
        if on_action is not None:
            row["actions"] = ["Open", "Delete"]
        rows.append(row)
    df = pd.DataFrame(rows)

    action_key = f"{key}_action"
    table_contacts = contacts

    def _handle_action() -> None:
        click = st.session_state.get(action_key)
        if not click or on_action is None:
            return
        row_idx = click["row"]
        label = click["label"]
        if row_idx >= len(table_contacts):
            return
        on_action(label, table_contacts[row_idx])

    def _handle_inline_edit() -> None:
        state = st.session_state.get(key) or {}
        edited_rows = state.get("edited_rows") or {}
        if not edited_rows:
            return
        carddav = get_facade().carddav
        for row_idx_str, changes in edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx >= len(table_contacts):
                continue
            target = table_contacts[row_idx]
            update = ContactUpdate(
                name=changes.get("name", target.name) or "Unnamed",
                organization=changes.get("organization", target.organization),
            )
            carddav.update_contact(target.uid, update)
        state["edited_rows"] = {}

    column_config: dict = {
        "name": st.column_config.TextColumn("Name", width="medium", required=True),
        "organization": st.column_config.TextColumn("Organization", width="medium"),
        "primary_email": st.column_config.TextColumn("Email", width="medium", disabled=True),
        "primary_phone": st.column_config.TextColumn("Phone", width="small", disabled=True),
        "uid": None,  # hidden
    }
    if on_action is not None:
        column_config["actions"] = st.column_config.ButtonColumn("Actions", on_click=_handle_action, key=action_key)

    st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        on_change=_handle_inline_edit,
        key=key,
    )
