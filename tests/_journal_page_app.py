"""Streamlit entry script for the journal page ``AppTest``.

``AppTest.from_file`` runs this file as a Streamlit script. The page
module is dropped from ``sys.modules`` first so that every run
re-executes its top-level code against the facade the test patched
in. The page module is a script, not a library: importing it renders
the whole page.

``GENESIS_TEST_JOURNAL_UID`` seeds the ``uid`` query param, so the
test can drive the page's entry view. ``GENESIS_TEST_JOURNAL_NEW``
and ``GENESIS_TEST_JOURNAL_EDIT`` switch the page into its create
and edit states.
"""

from __future__ import annotations

import importlib
import os
import sys

import streamlit as st

_journal_uid = os.environ.get("GENESIS_TEST_JOURNAL_UID")
if _journal_uid and not st.session_state.get("_genesis_test_journal_uid_seeded"):
    # Seed once per session: the page pops the param when the user
    # goes back, and the seed must not put it back.
    st.session_state["_genesis_test_journal_uid_seeded"] = True
    st.query_params["uid"] = _journal_uid
    if os.environ.get("GENESIS_TEST_JOURNAL_NEW"):
        st.query_params["new"] = "1"
    if os.environ.get("GENESIS_TEST_JOURNAL_EDIT"):
        st.query_params["edit"] = "1"

sys.modules.pop("genesis.ui.pages.journal", None)
importlib.import_module("genesis.ui.pages.journal")
