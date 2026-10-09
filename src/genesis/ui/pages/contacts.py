"""Contacts page: list view (default) and contact view (when ?uid=...).

Routing is keyed by UID. The list view is a data_editor table
with inline name / organization edits, a search box, and per-row
Open / Delete actions. The contact view has view / edit / create
modes reached by setting ``?uid=`` plus ``?edit=`` or ``?new=``,
following the same convention as :mod:`genesis.ui.pages.journal`.
"""

from __future__ import annotations

import uuid as _uuid

import pandas as pd
import streamlit as st

from genesis.entities import ContactUpdate
from genesis.ui._render import delete_confirm_dialog
from genesis.ui._state import get_facade

facade = get_facade()
carddav = facade.carddav


# -----------------------------------------------------------------------------
# List view
# -----------------------------------------------------------------------------


def _render_list() -> None:
    st.title("Contacts")
    st.caption("People, organisations, and how to reach them.")

    if st.button(
        "+ New Contact",
        key="new_contact_btn",
        type="primary",
        icon=":material/add:",
    ):
        st.query_params["uid"] = _uuid.uuid4().hex
        st.query_params["new"] = "1"
        st.rerun()

    st.write("")

    # Search box. Pulled out of the data_editor so it can be a
    # free-text input the user can clear without touching the
    # table widget's session state.
    search = st.text_input(
        "Search",
        value=st.session_state.get("contacts_search", ""),
        key="contacts_search_input",
        placeholder="Name, organization, nickname, or email",
    )
    st.session_state["contacts_search"] = search

    contacts = carddav.list_contacts(search=search or None)

    if not contacts:
        _render_list_empty()
        return

    df = pd.DataFrame(
        {
            "name": [c.name for c in contacts],
            "organization": [c.organization or "" for c in contacts],
            "primary_email": [c.emails[0] if c.emails else "" for c in contacts],
            "primary_phone": [c.phones[0] if c.phones else "" for c in contacts],
            "uid": [c.uid for c in contacts],
            "actions": [["Open", "Delete"] for _ in contacts],
        }
    )

    def _handle_action() -> None:
        click = st.session_state.get("contacts_action")
        if not click:
            return
        row_idx = click["row"]
        label = click["label"]
        if row_idx >= len(contacts):
            return
        target = contacts[row_idx]
        if label == "Open":
            st.query_params["uid"] = target.uid
        elif label == "Delete":
            delete_confirm_dialog(
                f"contact {target.name}",
                lambda uid=target.uid: carddav.delete_contact(uid),
            )

    def _handle_inline_edit() -> None:
        state = st.session_state.get("contacts_list") or {}
        edited_rows = state.get("edited_rows") or {}
        if not edited_rows:
            return
        for row_idx_str, changes in edited_rows.items():
            row_idx = int(row_idx_str)
            if row_idx >= len(contacts):
                continue
            target = contacts[row_idx]
            update = ContactUpdate(
                name=changes.get("name", target.name) or "Unnamed",
                organization=changes.get("organization", target.organization),
            )
            carddav.update_contact(target.uid, update)
        state["edited_rows"] = {}

    st.data_editor(
        df,
        column_config={
            "name": st.column_config.TextColumn("Name", width="medium", required=True),
            "organization": st.column_config.TextColumn("Organization", width="medium"),
            "primary_email": st.column_config.TextColumn("Email", width="medium", disabled=True),
            "primary_phone": st.column_config.TextColumn("Phone", width="small", disabled=True),
            "uid": None,  # hidden
            "actions": st.column_config.ButtonColumn("Actions", on_click=_handle_action, key="contacts_action"),
        },
        hide_index=True,
        on_change=_handle_inline_edit,
        key="contacts_list",
    )


def _render_list_empty() -> None:
    st.write("")
    st.info("No contacts yet. Use **+ New Contact** above to add your first one.")


# -----------------------------------------------------------------------------
# Helpers shared by view / edit / create
# -----------------------------------------------------------------------------


def _split_lines(value: str) -> list[str]:
    """Split a multi-line text block into a clean list of values.

    Used for the multi-value fields (emails / phones / urls /
    addresses) which the user edits as one value per line. Empty
    lines are dropped so the user can leave a trailing newline
    without polluting the data.
    """
    return [line.strip() for line in value.splitlines() if line.strip()]


def _join_lines(values: list[str]) -> str:
    """Inverse of :func:`_split_lines`. ``\\n``-separated for the
    ``st.text_area`` widget."""
    return "\n".join(values)


# -----------------------------------------------------------------------------
# View
# -----------------------------------------------------------------------------


def _render_view(uid: str) -> None:
    contact = carddav.get_contact(uid)

    if contact is None:
        st.title("Contact not found")
        if st.button(
            "Create contact at this UID",
            key="cv_recreate",
            type="primary",
        ):
            st.query_params["new"] = "1"
            st.rerun()
        return

    st.caption(f"Productivity > Contacts > {contact.name}")
    if st.button(
        "Back",
        key="cv_back",
        icon=":material/arrow_back:",
        type="tertiary",
    ):
        st.query_params.clear()
        st.rerun()
    st.title(contact.name)
    if contact.organization:
        st.caption(contact.organization)

    # Edit + Delete row, matching the projects page convention.
    with st.container(horizontal=True, horizontal_alignment="left"):
        if st.button("Edit", key="cv_edit", icon=":material/edit:", type="primary"):
            st.query_params["edit"] = "1"
            st.rerun()
        if st.button("Delete", key="cv_del", icon=":material/delete:"):
            delete_confirm_dialog(
                f"contact {contact.name}",
                lambda: carddav.delete_contact(contact.uid),
            )

    st.write("")

    if contact.emails:
        st.markdown("**Emails**")
        for e in contact.emails:
            st.markdown(f"- {e}")
        st.write("")
    if contact.phones:
        st.markdown("**Phones**")
        for p in contact.phones:
            st.markdown(f"- {p}")
        st.write("")
    if contact.urls:
        st.markdown("**URLs**")
        for u in contact.urls:
            st.markdown(f"- {u}")
        st.write("")
    if contact.addresses:
        st.markdown("**Addresses**")
        for a in contact.addresses:
            st.markdown(f"- {a}")
        st.write("")
    if contact.nickname:
        st.markdown(f"**Nickname:** {contact.nickname}")
    if contact.title:
        st.markdown(f"**Title:** {contact.title}")
    if contact.birthday:
        st.markdown(f"**Birthday:** {contact.birthday.isoformat()}")
    if contact.note:
        st.markdown("**Note**")
        st.markdown(contact.note)


# -----------------------------------------------------------------------------
# Edit
# -----------------------------------------------------------------------------


def _render_edit(uid: str) -> None:
    contact = carddav.get_contact(uid)
    if contact is None:
        st.warning("Contact not found.")
        return

    st.caption(f"Productivity > Contacts > {contact.name} > Edit")
    if st.button(
        "Back",
        key="ce_back",
        icon=":material/arrow_back:",
        type="tertiary",
    ):
        st.query_params.pop("edit", None)
        st.rerun()
    st.title(f"Edit {contact.name}")

    with st.form(f"ce_form_{uid}"):
        ftitle = st.text_input("Name", value=contact.name)
        col1, col2 = st.columns(2)
        with col1:
            fgiven = st.text_input("Given name", value=contact.given_name or "")
        with col2:
            ffamily = st.text_input("Family name", value=contact.family_name or "")
        col3, col4 = st.columns(2)
        with col3:
            fnickname = st.text_input("Nickname", value=contact.nickname or "")
        with col4:
            forganization = st.text_input("Organization", value=contact.organization or "")
        ftitle_field = st.text_input("Title", value=contact.title or "")
        femails = st.text_area(
            "Emails (one per line)",
            value=_join_lines(contact.emails),
            height=100,
        )
        fphones = st.text_area(
            "Phones (one per line)",
            value=_join_lines(contact.phones),
            height=100,
        )
        furl = st.text_area(
            "URLs (one per line)",
            value=_join_lines(contact.urls),
            height=80,
        )
        faddress = st.text_area(
            "Addresses (one per line)",
            value=_join_lines(contact.addresses),
            height=80,
        )
        fnote = st.text_area("Note", value=contact.note or "", height=120)
        fbirthday = st.date_input(
            "Birthday",
            value=contact.birthday,
            format="YYYY-MM-DD",
        )

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
            update = ContactUpdate(
                name=ftitle,
                given_name=fgiven or None,
                family_name=ffamily or None,
                nickname=fnickname or None,
                organization=forganization or None,
                title=ftitle_field or None,
                emails=_split_lines(femails),
                phones=_split_lines(fphones),
                urls=_split_lines(furl),
                addresses=_split_lines(faddress),
                note=fnote or None,
                birthday=fbirthday,
            )
            carddav.update_contact(uid, update)
            st.query_params.pop("edit", None)
            st.toast("Saved")
            st.rerun()
        if cancel_clicked:
            st.query_params.pop("edit", None)
            st.rerun()


# -----------------------------------------------------------------------------
# Create
# -----------------------------------------------------------------------------


def _render_create(uid: str) -> None:
    st.caption("Productivity > Contacts > New Contact")
    if st.button(
        "Back",
        key="cc_back",
        icon=":material/arrow_back:",
        type="tertiary",
    ):
        st.query_params.clear()
        st.rerun()
    st.title("New Contact")

    with st.form("cc_form"):
        ftitle = st.text_input("Name")
        col1, col2 = st.columns(2)
        with col1:
            fgiven = st.text_input("Given name")
        with col2:
            ffamily = st.text_input("Family name")
        col3, col4 = st.columns(2)
        with col3:
            fnickname = st.text_input("Nickname")
        with col4:
            forganization = st.text_input("Organization")
        ftitle_field = st.text_input("Title")
        femails = st.text_area("Emails (one per line)", height=100)
        fphones = st.text_area("Phones (one per line)", height=100)
        furl = st.text_area("URLs (one per line)", height=80)
        faddress = st.text_area("Addresses (one per line)", height=80)
        fnote = st.text_area("Note", height=120)
        fbirthday = st.date_input(
            "Birthday",
            value=None,
            format="YYYY-MM-DD",
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
            from genesis.entities import ContactCreate

            contact = carddav.create_contact(
                ContactCreate(
                    name=ftitle.strip(),
                    given_name=fgiven or None,
                    family_name=ffamily or None,
                    nickname=fnickname or None,
                    organization=forganization or None,
                    title=ftitle_field or None,
                    emails=_split_lines(femails),
                    phones=_split_lines(fphones),
                    urls=_split_lines(furl),
                    addresses=_split_lines(faddress),
                    note=fnote or None,
                    birthday=fbirthday,
                )
            )
            st.query_params.clear()
            st.query_params["uid"] = contact.uid
            st.toast("Created")
            st.rerun()
        if cancel_clicked:
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
