"""Journal page: list view (default) and entry view (when ?uid=...).

Routing is keyed by UID. The list view is a dataframe table with
inline title edits and per-row Open/Delete actions. The entry view
has view / edit / create modes. The date is set at entry creation
and is not editable from the UI (it is the entry's created date).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from genesis.journal.facade import Entry, EntryType
from genesis.ui._render import delete_confirm_dialog
from genesis.ui._state import get_facade

facade = get_facade()
journal = facade.journal


# -----------------------------------------------------------------------------
# List view
# -----------------------------------------------------------------------------


def _render_list() -> None:
    st.title("Journal")
    st.caption("Reflect on your progress and plan your future.")

    today = date.today()
    existing_daily = journal.find_for_period(EntryType.DAILY, today)
    existing_weekly = journal.find_for_period(EntryType.WEEKLY, today)
    existing_monthly = journal.find_for_period(EntryType.MONTHLY, today)
    existing_yearly = journal.find_for_period(EntryType.YEARLY, today)

    # Top-right action row. "+ New Entry" is always available so the
    # user can add any entry type on any day. When the periodic
    # entry for the current period exists, a button opens it for
    # editing.
    import uuid as _uuid

    def _open(existing) -> None:
        st.query_params["uid"] = existing.uid
        st.query_params["edit"] = "1"
        st.rerun()

    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("+ New Entry", key="new_entry_btn"):
            st.query_params["uid"] = _uuid.uuid4().hex
            st.query_params["new"] = "1"
            st.rerun()
        if existing_daily is not None:
            if st.button("Open daily", key="open_today_daily_btn"):
                _open(existing_daily)
        if existing_weekly is not None:
            if st.button("Open weekly", key="open_this_week_btn"):
                _open(existing_weekly)
        if existing_monthly is not None:
            if st.button("Open monthly", key="open_this_month_btn"):
                _open(existing_monthly)
        if existing_yearly is not None:
            if st.button("Open yearly", key="open_this_year_btn"):
                _open(existing_yearly)

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
        st.info("No journal entries yet. Use the **+ New Entry** button to start.")
        return

    df = pd.DataFrame(
        {
            "date": [e.date for e in entries],
            "type": [e.type.value if e.type else "" for e in entries],
            "title": [e.title or e.date.strftime("%A, %B %d, %Y") for e in entries],
            "preview": [(e.content.split("\n", 1)[0][:100] if e.content else "") for e in entries],
            "uid": [e.uid for e in entries],
            "actions": [["Open", "Delete"] for _ in entries],
        }
    )

    def _handle_action() -> None:
        click = st.session_state.get("journal_action")
        if not click:
            return
        row_idx = click["row"]
        label = click["label"]
        if row_idx >= len(entries):
            return
        target = entries[row_idx]
        if label == "Open":
            st.query_params["uid"] = target.uid
        elif label == "Delete":
            delete_confirm_dialog(
                f"entry {target.date.isoformat()}",
                lambda uid=target.uid: journal.delete_entry(uid),
            )
        # No explicit st.rerun() — this is an on_click callback.
        # Streamlit's natural rerun picks up the query_params change
        # on the next render.

    def _handle_inline_edit() -> None:
        state = st.session_state.get("journal_list") or {}
        edited_rows = state.get("edited_rows") or {}
        if not edited_rows:
            return
        for row_idx_str, changes in edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx >= len(entries):
                continue
            entry = entries[row_idx]
            if "title" in changes:
                entry.title = changes["title"] or None
                try:
                    journal.save_entry(entry)
                except ValueError as exc:
                    st.error(str(exc))
                    return
        # Clear in-flight edits so they are not re-applied to the new
        # df on the next render (same reason as in _table.py).
        state["edited_rows"] = {}

    st.data_editor(
        df,
        column_config={
            "date": st.column_config.DateColumn("Date", disabled=True, format="YYYY-MM-DD"),
            "type": st.column_config.TextColumn("Type", disabled=True),
            "title": st.column_config.TextColumn("Title", width="large"),
            "preview": st.column_config.TextColumn("Preview", disabled=True, width="large"),
            "uid": None,  # hidden
            "actions": st.column_config.ButtonColumn("Actions", on_click=_handle_action, key="journal_action"),
        },
        hide_index=True,
        on_change=_handle_inline_edit,
        key="journal_list",
    )


# -----------------------------------------------------------------------------
# Entry view
# -----------------------------------------------------------------------------


def _render_view(uid: str) -> None:
    entry = journal.get_entry(uid)

    # Back button on its own row
    back_col, _ = st.columns([1, 8])
    with back_col:
        if st.button("← Back", key="je_back"):
            st.query_params.clear()
            st.rerun()

    if entry is None:
        st.title("Entry not found")
        if st.button("Create entry at this UID", key="je_recreate"):
            st.query_params["new"] = "1"
            st.rerun()
        return

    # Breadcrumb + title + actions on the next row
    st.caption(f"Productivity > Journal > {entry.date.isoformat()}")
    header_cols = st.columns([8, 1, 1])
    with header_cols[0]:
        title = entry.title or entry.date.strftime("%A, %B %d, %Y")
        st.title(title)
        if entry.type:
            st.badge(entry.type.value, color="blue")
    with header_cols[1]:
        if st.button("✎ Edit", key="je_edit_btn"):
            st.query_params["edit"] = "1"
            st.rerun()
    with header_cols[2]:
        if st.button("🗑 Delete", key="je_del_btn"):
            delete_confirm_dialog(
                f"entry {entry.date.isoformat()}",
                lambda: journal.delete_entry(entry.uid),
            )

    st.divider()
    if entry.content.strip():
        st.markdown(entry.content)
    else:
        st.caption("Empty entry.")


def _render_edit(uid: str) -> None:
    entry = journal.get_entry(uid)
    if entry is None:
        st.warning("Entry not found.")
        return

    type_options = [t.value for t in EntryType]
    type_labels = ["(none)"] + type_options
    cur_type_label = entry.type.value if entry.type else "(none)"
    cur_idx = type_labels.index(cur_type_label) if cur_type_label in type_labels else 0

    header_cols = st.columns([1, 6])
    with header_cols[0]:
        if st.button("← Back", key="je_edit_back"):
            st.query_params.pop("edit", None)
            st.rerun()
    with header_cols[1]:
        st.caption(f"Productivity > Journal > {entry.date.isoformat()} > Edit")
        st.title(f"Edit {entry.date.isoformat()}")

    with st.form(f"je_edit_{uid}"):
        ftitle = st.text_input("Title", value=entry.title or "")
        ftype = st.selectbox("Type", type_labels, index=cur_idx)
        fbody = st.text_area("Content", value=entry.content, height=400)
        st.caption(f"Date: {entry.date.isoformat()} (set at creation, not editable)")
        cols = st.columns([1, 1, 6])
        if cols[0].form_submit_button("Save"):
            new_type = EntryType(ftype) if ftype != "(none)" else None
            entry.title = ftitle or None
            entry.type = new_type
            entry.content = fbody
            try:
                journal.save_entry(entry)
                st.query_params.pop("edit", None)
                st.toast("Saved")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if cols[1].form_submit_button("Cancel"):
            st.query_params.pop("edit", None)
            st.rerun()


def _render_create(uid: str) -> None:
    type_labels = ["(none)"] + [t.value for t in EntryType]
    today = date.today()

    header_cols = st.columns([1, 6])
    with header_cols[0]:
        if st.button("← Back", key="je_create_back"):
            st.query_params.clear()
            st.rerun()
    with header_cols[1]:
        st.caption("Productivity > Journal > New Entry")
        st.title("New Journal Entry")

    with st.form("je_create"):
        ftitle = st.text_input("Title")
        ftype = st.selectbox("Type", type_labels, index=type_labels.index("daily"))
        fbody = st.text_area("Content", height=400, placeholder="What's on your mind?")
        st.caption(f"Date: {today.isoformat()} (set at creation, not editable)")
        cols = st.columns([1, 1, 6])
        if cols[0].form_submit_button("Create"):
            new_type = EntryType(ftype) if ftype != "(none)" else None
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
                    content=fbody,
                )
                try:
                    journal.save_entry(entry)
                    st.query_params.clear()
                    st.query_params["uid"] = entry.uid
                    st.toast("Created")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if cols[1].form_submit_button("Cancel"):
            st.query_params.clear()
            st.rerun()


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
