"""Dashboard page: active assignments and deadlines.

Surfaces two forward-looking tasks views plus a project count:

* Active assignments: tasks whose ``assigned_date`` is today or earlier,
  so you should look at them.
* Deadlines: tasks whose ``hard_deadline`` is within the next 7 days,
  or already overdue.

Both exclude tasks with status COMPLETED or CANCELED. A task may
appear in both lists if both fields are set.

The bottom of the page also hosts a closed-by-default expander
that shows today's briefing entry (when one exists). Briefings
are agent-written morning/nightly summaries; the user's daily
journal entry stays separate. The entry is rendered read-only
via the shared :func:`render_journal_entry_view` helper;
clicking Edit switches to the Journal page in edit mode rather
than editing inline.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date, datetime, time, timedelta

import streamlit as st

from genesis.entities import ProjectStatus, TaskStatus
from genesis.journal.facade import EntryType
from genesis.tz import to_utc
from genesis.ui._render import render_journal_entry_view
from genesis.ui._state import get_facade
from genesis.ui._table import TASK_TABLE_DEFAULT_SORT_KEYS, render_tasks_table

facade = get_facade()
caldav = facade.caldav
journal = facade.journal

st.title("Dashboard")
st.caption("What's on your plate.")

# --- Compute today / week window -------------------------------------------

today = date.today()
today_start = to_utc(datetime.combine(today, time.min))
week_end_dt = today_start + timedelta(days=7)

# --- Fetch tasks once, derive both views -----------------------------------

all_tasks = caldav.list_tasks()
done_statuses = {TaskStatus.COMPLETED, TaskStatus.CANCELED}

# Active assignments: assigned_date <= today, not done/canceled.
# The table widget owns the sort (cache-based, ADR-023).
active_assignments = [
    t for t in all_tasks if t.assigned_date is not None and t.assigned_date <= today and t.status not in done_statuses
]

# Deadlines: hard_deadline within 7 days or already overdue.
# The table widget owns the sort (cache-based, ADR-023).
deadlines = [
    t
    for t in all_tasks
    if t.hard_deadline is not None and t.hard_deadline <= week_end_dt and t.status not in done_statuses
]

active_projects = caldav.list_projects(status=ProjectStatus.IN_PROGRESS)

# --- Metric cards ----------------------------------------------------------

cols = st.columns(3, gap="medium")
with cols[0]:
    with st.container(border=True):
        st.caption("Active assignments")
        st.markdown(f"## {len(active_assignments)}")
        st.caption("Assigned today or earlier")
with cols[1]:
    with st.container(border=True):
        st.caption("Deadlines this week")
        st.markdown(f"## {len(deadlines)}")
        st.caption("Due in the next 7 days or overdue")
with cols[2]:
    with st.container(border=True):
        st.caption("Active projects")
        st.markdown(f"## {len(active_projects)}")
        st.caption("Projects currently in progress")

st.divider()

# --- Active assignments -----------------------------------------------------

st.subheader(f"Active assignments ({len(active_assignments)})")
st.caption("Assigned today or earlier.")
if not active_assignments:
    st.info("No overdue or today assignments.")
else:
    render_tasks_table(
        active_assignments,
        caldav=caldav,
        key="dashboard_assignments",
        show_project_column=True,
        sort_keys=TASK_TABLE_DEFAULT_SORT_KEYS,
    )

st.write("")

# --- Deadlines --------------------------------------------------------------

st.subheader(f"Deadlines ({len(deadlines)})")
st.caption("Due in the next 7 days or already overdue.")
if not deadlines:
    st.info("Nothing due in the next 7 days.")
else:
    render_tasks_table(
        deadlines,
        caldav=caldav,
        key="dashboard_deadlines",
        show_project_column=True,
        sort_keys=TASK_TABLE_DEFAULT_SORT_KEYS,
    )

st.write("")

# --- Daily briefing ---------------------------------------------------------

briefing_entry = journal.find_for_period(EntryType.BRIEFING, today)


def _open_journal_edit(entry) -> None:
    """Navigate to the journal page in edit mode for ``entry``.

    Used as the ``on_edit`` callback for the dashboard's daily
    journal expander so the Edit button switches pages instead of
    editing inline. The journal page's own dispatch picks up the
    ``uid`` + ``edit`` query params and renders its edit form.

    ``st.switch_page`` resolves the target relative to the main
    script (``src/genesis/ui/app.py``), so the journal page lives
    at ``pages/journal.py`` from that base — not at its
    ``url_path``.
    """
    st.switch_page("pages/journal.py", query_params={"uid": entry.uid, "edit": "1"})


with st.expander(f"Daily briefing — {today.isoformat()}", expanded=False):
    if briefing_entry is None:
        st.caption("No briefing yet for today.")
        if st.button(
            "+ Write briefing",
            key="dashboard_write_briefing",
            type="primary",
            icon=":material/edit:",
        ):
            # Land directly on the journal page's create form with
            # ``type=briefing`` preselected. ``st.switch_page``
            # resolves the target relative to the main script
            # (``src/genesis/ui/app.py``), so the journal page lives
            # at ``pages/journal.py`` from that base.
            st.switch_page(
                "pages/journal.py",
                query_params={
                    "uid": _uuid.uuid4().hex,
                    "new": "1",
                    "type": EntryType.BRIEFING.value,
                },
            )
    else:
        render_journal_entry_view(briefing_entry, on_edit=_open_journal_edit)
