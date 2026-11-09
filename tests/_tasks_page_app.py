"""Streamlit entry script for the tasks page ``AppTest``.

``AppTest.from_file`` runs this file as a Streamlit script. The page
module is dropped from ``sys.modules`` first so that every run
re-executes its top-level code against the facade the test patched
in. The page module is a script, not a library: importing it renders
the whole page.

``GENESIS_TEST_TASK_UID`` seeds the ``uid`` query param, so the test
can drive the page's detail state.
"""

from __future__ import annotations

import importlib
import os
import sys

import streamlit as st

_task_uid = os.environ.get("GENESIS_TEST_TASK_UID")
if _task_uid and not st.session_state.get("_genesis_test_task_uid_seeded"):
    # Seed once per session: the page pops the param when the user
    # goes back, and the seed must not put it back.
    st.session_state["_genesis_test_task_uid_seeded"] = True
    st.query_params["uid"] = _task_uid

sys.modules.pop("genesis.ui.pages.tasks", None)
importlib.import_module("genesis.ui.pages.tasks")
