"""Journal page: list view (default) and entry view (when ?uid=...).

Routing is keyed by UID. The list view is the shared journal
table with inline title edits and per-row Open/Delete actions.
The entry view has view / edit / create modes. The date is set at
entry creation and is not editable from the UI (it is the entry's
created date).

The create form and the table are shared with the onboarding
wizard: the form lives in :func:`genesis.ui._render.journal_create_form`
and the table in :func:`genesis.ui._table.render_journal_table`.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date

import streamlit as st

from genesis.journal.facade import Entry, EntryType
from genesis.ui._render import (
    delete_confirm_dialog,
    journal_create_form,
    render_journal_entry_view,
    task_link_options,
)
from genesis.ui._state import get_facade
from genesis.ui._table import render_journal_table

facade = get_facade()
journal = facade.journal


# -----------------------------------------------------------------------------
# List view
# -----------------------------------------------------------------------------


_PERIODIC_TYPES: tuple[EntryType, ...] = (
    EntryType.DAILY,
    EntryType.BRIEFING,
    EntryType.WEEKLY,
    EntryType.MONTHLY,
    EntryType.YEARLY,
)


def _render_list() -> None:
    st.title("Journal")
    st.caption("Reflect on your progress and plan your future.")

    today = date.today()
    existing = {t: journal.find_for_period(t, today) for t in _PERIODIC_TYPES}

    # Primary action row: the single most prominent button is "+ New
    # Entry". The periodic openers sit below as a quiet "today" row
    # so the primary action is never competing with four siblings.
    if st.button(
        "+ New Entry",
        key="new_entry_btn",
        type="primary",
        icon=":material/add:",
    ):
        st.query_params["uid"] = _uuid.uuid4().hex
        st.query_params["new"] = "1"
        st.rerun()

    st.write("")
    st.markdown("**Today**")
    st.caption("Jump straight to the entry that owns the current period.")
    today_cols = st.columns(len(_PERIODIC_TYPES))
    for col, entry_type in zip(today_cols, _PERIODIC_TYPES, strict=True):
        with col:
            label = entry_type.value.capitalize()
            current = existing[entry_type]
            if current is not None:
                if st.button(
                    f"Open {label}",
                    key=f"open_this_{entry_type.value}_btn",
                    type="secondary",
                    use_container_width=True,
                ):
                    st.query_params["uid"] = current.uid
                    st.query_params["edit"] = "1"
                    st.rerun()
            else:
                if st.button(
                    f"Write {label}",
                    key=f"write_this_{entry_type.value}_btn",
                    type="secondary",
                    use_container_width=True,
                ):
                    st.query_params["uid"] = _uuid.uuid4().hex
                    st.query_params["new"] = "1"
                    st.query_params["type"] = entry_type.value
                    st.rerun()

    st.write("")
    type_options = [t.value for t in EntryType]
    type_filter = st.session_state.get("journal_type_filter", list(type_options))
    type_filter = st.pills(
        "Type",
        options=type_options,
        default=type_filter,
        selection_mode="multi",
        key="journal_type_filter",
    )

    st.write("")

    entries = journal.list_entries()
    selected_types = set(type_filter or [])
    if selected_types:
        entries = [e for e in entries if e.type and e.type.value in selected_types]

    if not entries:
        _render_list_empty()
        return

    def _handle_action(label: str, entry: Entry) -> None:
        if label == "Open":
            st.query_params["uid"] = entry.uid
        elif label == "Delete":
            delete_confirm_dialog(
                f"entry {entry.date.isoformat()}",
                lambda uid=entry.uid: journal.delete_entry(uid),
            )
        # No explicit st.rerun() — this is an on_click callback.
        # Streamlit's natural rerun picks up the query_params change
        # on the next render.

    render_journal_table(entries, key="journal_list", on_action=_handle_action)


def _render_list_empty() -> None:
    """Render the empty state for the list view.

    Mirrors the empty-state pattern from the profile page: a
    friendly hint + the primary CTA already rendered above, so the
    user can act without scrolling.
    """
    st.write("")
    st.info(
        "No journal entries yet. Use **+ New Entry** above to write your first one. Daily entries are a good default."
    )


# -----------------------------------------------------------------------------
# Entry view
# -----------------------------------------------------------------------------


def _render_view(uid: str) -> None:
    entry = journal.get_entry(uid)

    if entry is None:
        st.title("Entry not found")
        if st.button(
            "Create entry at this UID",
            key="je_recreate",
            type="primary",
        ):
            st.query_params["new"] = "1"
            st.rerun()
        return

    # Breadcrumb + back link on the first row.
    st.caption(f"Productivity > Journal > {entry.date.isoformat()}")
    if st.button(
        "Back",
        key="je_back",
        icon=":material/arrow_back:",
        type="tertiary",
    ):
        st.query_params.clear()
        st.rerun()

    # Title row + content + actions are shared with the dashboard's
    # daily-journal expander via the helper. Edit defaults to the
    # same-page behaviour (sets ?edit=1 and reruns); Delete is shown
    # only on this full-page view.
    render_journal_entry_view(entry, show_delete=True)


def _render_edit(uid: str) -> None:
    entry = journal.get_entry(uid)
    if entry is None:
        st.warning("Entry not found.")
        return

    type_options = [t.value for t in EntryType]
    type_labels = ["(none)"] + type_options
    cur_type_label = entry.type.value if entry.type else "(none)"
    cur_idx = type_labels.index(cur_type_label) if cur_type_label in type_labels else 0

    task_labels, task_uids = task_link_options(entry.task_id)
    cur_task_idx = task_uids.index(entry.task_id) if entry.task_id in task_uids else 0

    st.caption(f"Productivity > Journal > {entry.date.isoformat()} > Edit")
    if st.button(
        "Back",
        key="je_edit_back",
        icon=":material/arrow_back:",
        type="tertiary",
    ):
        st.query_params.pop("edit", None)
        st.rerun()
    st.title(f"Edit {entry.date.isoformat()}")

    with st.form(f"je_edit_{uid}"):
        ftitle = st.text_input("Title", value=entry.title or "")
        ftype = st.selectbox("Type", type_labels, index=cur_idx)
        ftask = st.selectbox("Task", task_labels, index=cur_task_idx)
        fbody = st.text_area("Content", value=entry.content, height=400)
        st.caption(f"Date: {entry.date.isoformat()} (set at creation, not editable)")

        action_row = st.container(horizontal=True, horizontal_alignment="right")
        with action_row:
            save_clicked = st.form_submit_button(
                "Save",
                type="primary",
                icon=":material/save:",
            )
            cancel_clicked = st.form_submit_button(
                "Cancel",
                type="tertiary",
            )

        if save_clicked:
            new_type = EntryType(ftype) if ftype != "(none)" else None
            entry.title = ftitle or None
            entry.type = new_type
            entry.task_id = task_uids[task_labels.index(ftask)]
            entry.content = fbody
            try:
                journal.save_entry(entry)
                st.query_params.pop("edit", None)
                st.toast("Saved")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if cancel_clicked:
            st.query_params.pop("edit", None)
            st.rerun()


def _render_create(uid: str) -> None:
    st.caption("Productivity > Journal > New Entry")
    if st.button(
        "Back",
        key="je_create_back",
        icon=":material/arrow_back:",
        type="tertiary",
    ):
        st.query_params.clear()
        st.rerun()
    st.title("New Journal Entry")

    # If the list view routed here with a preselected type, honour it.
    preselected_type = st.query_params.get("type", "daily")

    def _after_create(entry: Entry) -> None:
        st.query_params.clear()
        st.query_params["uid"] = entry.uid
        st.toast("Created")
        st.rerun()

    def _go_back() -> None:
        st.query_params.clear()
        st.rerun()

    journal_create_form(
        uid=uid,
        key_prefix=f"je_create_{uid}",
        default_type=preselected_type,
        on_created=_after_create,
        on_cancel=_go_back,
    )


# -----------------------------------------------------------------------------
# Dispatch
# -----------------------------------------------------------------------------


uid = st.query_params.get("uid")
is_new = "new" in st.query_params
is_edit = "edit" in st.query_params

if uid:
    if is_new:
        _render_create(uid)
    elif is_edit:
        _render_edit(uid)
    else:
        _render_view(uid)
else:
    _render_list()
