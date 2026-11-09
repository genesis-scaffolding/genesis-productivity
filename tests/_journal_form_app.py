"""Streamlit entry script for the shared journal create form.

``AppTest.from_file`` runs this file as a Streamlit script. It
renders :func:`genesis.ui._render.journal_create_form` on its own —
the same call the create dialog makes — so a test can drive the form
without going through a dialog.

``GENESIS_TEST_JOURNAL_UID`` seeds the entry UID,
``GENESIS_TEST_JOURNAL_TYPE`` the entry type, and
``GENESIS_TEST_JOURNAL_TASK_UID`` the preselected task link.
"""

from __future__ import annotations

import os

from genesis.ui._render import journal_create_form

journal_create_form(
    uid=os.environ.get("GENESIS_TEST_JOURNAL_UID", "j-test0001"),
    key_prefix="jf",
    default_type=os.environ.get("GENESIS_TEST_JOURNAL_TYPE", "daily"),
    default_task_id=os.environ.get("GENESIS_TEST_JOURNAL_TASK_UID") or None,
)
