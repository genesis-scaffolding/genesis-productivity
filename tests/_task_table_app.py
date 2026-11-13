"""Streamlit entry script for the shared task table ``AppTest``.

Renders the canonical task table with the tasks of the facade, so a
test can drive an inline edit and observe which operation the table
chose.
"""

from __future__ import annotations

from genesis.ui._state import get_facade
from genesis.ui._table import render_tasks_table

facade = get_facade()
render_tasks_table(
    facade.caldav.list_tasks(),
    caldav=facade.caldav,
    key="test_tasks",
)
