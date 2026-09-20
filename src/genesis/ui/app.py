"""Streamlit entry script for the genesis productivity UI.

Sets the page config, configures the sidebar navigation, and runs
the selected page. Authentication is not gated in this script;
``genesis.ui._state.get_facade`` is the single source of truth and
self-heals when called without an authenticated user (it shows
the login form inline and stops the page module). Doing it this
way keeps the sidebar styled correctly on first load — if we
called ``st.stop()`` here, Streamlit would fall back to its
auto-discovered file list and render the sidebar as plain text
instead of our configured nav with icons and section grouping.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

_UI_DIR = Path(__file__).parent
_PAGES = _UI_DIR / "pages"


def _page(path: Path, title: str, icon: str, url_path: str) -> st.Page:
    return st.Page(str(path), title=title, icon=icon, url_path=url_path)


# ``st.set_page_config`` must be the first Streamlit call in the
# script.
st.set_page_config(
    page_title="genesis",
    layout="wide",
    page_icon=":material/bolt:",
)

nav: dict[str, list[st.Page]] = {
    "Productivity": [
        _page(_PAGES / "dashboard.py", "Dashboard", ":material/dashboard:", "dashboard"),
        _page(_PAGES / "focus.py", "Focus", ":material/timer:", "focus"),
        _page(_PAGES / "projects.py", "Projects", ":material/folder:", "projects"),
        _page(_PAGES / "tasks.py", "Tasks", ":material/checklist:", "tasks"),
        _page(_PAGES / "calendar_page.py", "Calendar", ":material/calendar_month:", "calendar"),
        _page(_PAGES / "journal.py", "Journal", ":material/article:", "journal"),
        _page(_PAGES / "statistics.py", "Statistics", ":material/analytics:", "statistics"),
    ],
    "Account": [
        _page(_PAGES / "settings.py", "Settings", ":material/settings:", "settings"),
    ],
}

from genesis.ui._auth import rehydrate_session, render_logout_button  # noqa: E402

# Restore session_state from the signed session cookie before any
# gate check, so a returning browser (e.g. phone just woken from
# sleep) lands on the app instead of the login form. ADR-021.
rehydrate_session()
render_logout_button()

st.navigation(nav).run()
