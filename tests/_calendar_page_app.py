"""Streamlit entry script for the calendar page ``AppTest``.

``AppTest.from_file`` runs this file as a Streamlit script. The page
module is dropped from ``sys.modules`` first so that every run
re-executes its top-level code against the facade the test patched
in. The page module is a script, not a library: importing it renders
the whole page.
"""

from __future__ import annotations

import importlib
import sys

sys.modules.pop("genesis.ui.pages.calendar_page", None)
importlib.import_module("genesis.ui.pages.calendar_page")
