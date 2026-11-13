"""Streamlit entry script for the task dialogs ``AppTest``.

Renders the buttons that open the shared create-task and edit-task
dialogs, so a test can drive them without a browser.

The dialogs stay open once opened (the buttons set a session flag
instead of relying on the one-shot click), so a test can interact with
the dialog widgets over several runs.

``GENESIS_TEST_TASK_UID`` selects the task the edit dialog opens.
``GENESIS_TEST_SERIES_UID`` selects the series the series form and
the series cancellation open.
"""

from __future__ import annotations

import os

import streamlit as st

from genesis.ui._render import (
    series_cancel_dialog,
    series_edit_dialog,
    task_create_dialog,
    task_delete_dialog,
    task_edit_dialog,
)
from genesis.ui._state import get_facade

if st.button("open create dialog", key="open_create_dialog"):
    st.session_state["_open_create_dialog"] = True
if st.session_state.get("_open_create_dialog"):
    task_create_dialog()

_task_uid = os.environ.get("GENESIS_TEST_TASK_UID")
if _task_uid and st.button("open edit dialog", key="open_edit_dialog"):
    st.session_state["_open_edit_dialog"] = True
if _task_uid and st.session_state.get("_open_edit_dialog"):
    task_edit_dialog(_task_uid)

_series_uid = os.environ.get("GENESIS_TEST_SERIES_UID")
if _series_uid and st.button("open series dialog", key="open_series_dialog"):
    st.session_state["_open_series_dialog"] = True
if _series_uid and st.session_state.get("_open_series_dialog"):
    series_edit_dialog(_series_uid)

if _series_uid and st.button("open series cancel dialog", key="open_series_cancel_dialog"):
    st.session_state["_open_series_cancel_dialog"] = True
if _series_uid and st.session_state.get("_open_series_cancel_dialog"):
    series_cancel_dialog(_series_uid, "Pay rent")

if _task_uid and st.button("open delete dialog", key="open_delete_dialog"):
    st.session_state["_open_delete_dialog"] = True
if _task_uid and st.session_state.get("_open_delete_dialog"):
    _caldav = get_facade().caldav
    _task = _caldav.get_task(_task_uid)
    if _task is not None:
        task_delete_dialog(_caldav, _task)
