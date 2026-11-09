"""Streamlit entry script for the journal create dialog ``AppTest``.

Renders a button that opens the shared create-journal-entry dialog —
the same call the task detail page and the onboarding wizard make —
so a test can drive the dialog (and the wrapper's reset of the
widget state) without a browser.

``GENESIS_TEST_DIALOG_TYPE`` is the ``default_type`` passed on the
open; ``GENESIS_TEST_DIALOG_TASK_UID`` the ``default_task_id``.
"""

from __future__ import annotations

import os

import streamlit as st

from genesis.ui._render import journal_create_dialog

if st.button("open create dialog", key="open_create_dialog"):
    journal_create_dialog(
        default_type=os.environ.get("GENESIS_TEST_DIALOG_TYPE", "daily"),
        default_task_id=os.environ.get("GENESIS_TEST_DIALOG_TASK_UID") or None,
    )
