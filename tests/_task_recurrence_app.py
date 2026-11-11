"""Streamlit entry script for the task dialogs ``AppTest``.

Renders the buttons that open the shared create-task and edit-task
dialogs, so a test can drive them without a browser.

The dialogs stay open once opened (the buttons set a session flag
instead of relying on the one-shot click), so a test can interact with
the dialog widgets over several runs.

``GENESIS_TEST_TASK_UID`` selects the task the edit dialog opens.
"""

from __future__ import annotations

import os

import streamlit as st

from genesis.ui._render import task_create_dialog, task_edit_dialog

if st.button("open create dialog", key="open_create_dialog"):
    st.session_state["_open_create_dialog"] = True
if st.session_state.get("_open_create_dialog"):
    task_create_dialog()

_task_uid = os.environ.get("GENESIS_TEST_TASK_UID")
if _task_uid and st.button("open edit dialog", key="open_edit_dialog"):
    st.session_state["_open_edit_dialog"] = True
if _task_uid and st.session_state.get("_open_edit_dialog"):
    task_edit_dialog(_task_uid)
