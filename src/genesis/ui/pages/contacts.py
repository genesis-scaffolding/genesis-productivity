"""Contacts page: list view (default) and contact view (when ?uid=...).

Routing is keyed by UID. The list view is the shared contacts
table with inline name / organization edits, a search box, and
per-row Open / Delete actions. The contact view has view / edit /
create modes reached by setting ``?uid=`` plus ``?edit=`` or
``?new=``, following the same convention as
:mod:`genesis.ui.pages.journal`.

The create form and the table are shared with the onboarding
wizard: the form lives in :func:`genesis.ui._render.contact_create_form`
and the table in :func:`genesis.ui._table.render_contacts_table`.
"""

from __future__ import annotations

import uuid as _uuid

import streamlit as st

from genesis.entities import Contact, ContactUpdate
from genesis.ui._render import contact_create_form, delete_confirm_dialog, split_multiline
from genesis.ui._state import get_facade
from genesis.ui._table import render_contacts_table

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

    def _handle_action(label: str, contact: Contact) -> None:
        if label == "Open":
            st.query_params["uid"] = contact.uid
        elif label == "Delete":
            delete_confirm_dialog(
                f"contact {contact.name}",
                lambda uid=contact.uid: carddav.delete_contact(uid),
            )

    render_contacts_table(contacts, key="contacts_list", on_action=_handle_action)


def _render_list_empty() -> None:
    st.write("")
    st.info("No contacts yet. Use **+ New Contact** above to add your first one.")


# -----------------------------------------------------------------------------
# Helpers shared by view / edit / create
# -----------------------------------------------------------------------------


def _join_lines(values: list[str]) -> str:
    """Inverse of :func:`split_multiline`. ``\\n``-separated for the
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
                emails=split_multiline(femails),
                phones=split_multiline(fphones),
                urls=split_multiline(furl),
                addresses=split_multiline(faddress),
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

    def _after_create(contact: Contact) -> None:
        st.query_params.clear()
        st.query_params["uid"] = contact.uid
        st.toast("Created")
        st.rerun()

    def _go_back() -> None:
        st.query_params.clear()
        st.rerun()

    contact_create_form(
        uid=uid,
        key_prefix="cc",
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
