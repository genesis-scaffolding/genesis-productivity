"""Shared Streamlit data widgets for the genesis UI.

This module hosts the canonical ``tasks table`` so it has a single
source of truth. Page modules pass in their filtered task list and
a unique widget-key prefix; the table handles inline edits, per-row
Edit/Delete actions, and the empty-state message.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

import pandas as pd
import streamlit as st

from genesis.entities import Task, TaskStatus, TaskUpdate
from genesis.tz import to_local, to_utc
from genesis.ui._render import (
    delete_confirm_dialog,
    focus_create_dialog,
    session_log_dialog,
    task_edit_dialog,
)

if TYPE_CHECKING:
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
    )


def render_tasks_table(
    tasks: list[Task],
    *,
    caldav: CalDAVFacade,
    key: str,
    show_project_column: bool = True,
    sort_keys: list[SortKey] | None = None,
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
        row: dict = {
            "uid": t.uid,
            "Title": t.title,
            "Priority": t.priority if t.priority is not None else 5,
            "Scheduled": sched.replace(tzinfo=None) if sched else None,
            "Deadline": dl_local.date() if dl_local else None,
            "Assigned": t.assigned_date,
            "Status": t.status.value,
        }
        if show_project_column:
            row["Project"] = _project_label(t.project_id)
        row["actions"] = ["Focus", "Log Session", "Edit", "Delete"]
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
        elif label == "Focus":
            focus_create_dialog(task=target)
        elif label == "Log Session":
            session_log_dialog(task=target)
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
                update = update.model_copy(update={"title": str(changes["Title"])})
                touched = True
            if "Priority" in changes:
                update = update.model_copy(update={"priority": int(changes["Priority"])})
                touched = True
            if "Status" in changes:
                update = update.model_copy(update={"status": TaskStatus(str(changes["Status"]))})
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
        "Priority": st.column_config.NumberColumn("Priority", min_value=1, max_value=9),
        "Scheduled": st.column_config.DatetimeColumn("Scheduled", format="YYYY-MM-DD HH:mm"),
        "Deadline": st.column_config.DateColumn("Deadline", format="YYYY-MM-DD"),
        "Assigned": st.column_config.DateColumn("Assigned", format="YYYY-MM-DD"),
        "Status": st.column_config.SelectboxColumn("Status", options=statuses_all, required=True),
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
    column_config["actions"] = st.column_config.ButtonColumn("Actions", on_click=_handle_action, key=action_key)

    st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        on_change=_handle_inline_edit,
        key=key,
    )
