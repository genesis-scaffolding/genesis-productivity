"""Streamlit entry script for the genesis productivity UI.

Sets the page config and runs ``st.navigation`` with one Productivity
section containing the seven page modules. Pages are wired in
``st.Page(...)`` so each gets a sidebar entry and a stable URL path.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

_UI_DIR = Path(__file__).parent
_PAGES = _UI_DIR / "pages"


def _page(path: Path, title: str, icon: str, url_path: str) -> st.Page:
    return st.Page(str(path), title=title, icon=icon, url_path=url_path)


# Eagerly initialize the Facade so Radicale is up before the first
# page tries to use it. cache_resource on get_facade() guarantees
# one instance for the session.
from genesis.ui._state import get_facade  # noqa: E402

facade = get_facade()
st.session_state["facade"] = facade
st.session_state["user"] = facade.configs.user

st.set_page_config(
    page_title="genesis",
    layout="wide",
    page_icon=":material/bolt:",
)

nav: dict[str, list[st.Page]] = {
    "Productivity": [
        _page(_PAGES / "dashboard.py", "Dashboard", ":material/dashboard:", "dashboard"),
        _page(_PAGES / "projects.py", "Projects", ":material/folder:", "projects"),
        _page(_PAGES / "tasks.py", "Tasks", ":material/checklist:", "tasks"),
        _page(_PAGES / "calendar_page.py", "Calendar", ":material/calendar_month:", "calendar"),
        _page(_PAGES / "journal.py", "Journal", ":material/article:", "journal"),
    ],
}

st.navigation(nav).run()
