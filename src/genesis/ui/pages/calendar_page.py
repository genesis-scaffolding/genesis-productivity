"""Calendar page: week grid showing shadow VEVENTs from the events collection."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import streamlit as st

from genesis.entities import EventStatus
from genesis.tz import to_local, to_utc
from genesis.ui._state import get_facade
from genesis.ui._table import render_tasks_table

facade = get_facade()
caldav = facade.caldav

# --- Week navigation --------------------------------------------------------

if "cal_week" not in st.session_state:
    st.session_state["cal_week"] = date.today()

today = date.today()
with st.container(horizontal=True, vertical_alignment="center"):
    st.title("Calendar")
    spacer_left, _, nav = st.columns([6, 0.5, 4])
    with nav:
        b1, b2, b3 = st.columns(3)
        if b1.button("Today", key="cal_today"):
            st.session_state["cal_week"] = today
            st.rerun()
        if b2.button("◀ Prev", key="cal_prev"):
            st.session_state["cal_week"] -= timedelta(days=7)
            st.rerun()
        if b3.button("Next ▶", key="cal_next"):
            st.session_state["cal_week"] += timedelta(days=7)
            st.rerun()

# Compute Monday of the current week
week_start = st.session_state["cal_week"] - timedelta(days=st.session_state["cal_week"].weekday())
week_end = week_start + timedelta(days=6)
st.caption(f"Week of {week_start.isoformat()} → {week_end.isoformat()}")
st.write("")


# --- Load shadow events for this week ---------------------------------------

# All-day events whose start is in [week_start, week_end + 1 day)
week_window_start = to_utc(datetime.combine(week_start, time.min))
week_window_end = to_utc(datetime.combine(week_end + timedelta(days=1), time.min))

# Server-side time-range filter: VEVENT overlap with the week window
# (RFC 4791 §9.9). Both standalone and shadow (task_id set) events are
# returned; the design deliberately shows both kinds.
shadow_events = caldav.list_events(
    status=EventStatus.CONFIRMED,
    start=week_window_start,
    end=week_window_end,
)


def _event_day(e) -> date:
    return e.start.date()


events_by_day: dict[date, list] = {week_start + timedelta(days=i): [] for i in range(7)}
for e in shadow_events:
    d = _event_day(e)
    if d in events_by_day:
        events_by_day[d].append(e)


# --- Week grid: all-day event strip + day columns ---------------------------

# Header row: day labels. Use the :blue[...] syntax carefully: each
# colored piece is its own bracketed expression because Streamlit's
# :color[...] syntax does not handle nested markdown or newlines well.
day_cols = st.columns(7, gap="small")
for i, c in enumerate(day_cols):
    day = week_start + timedelta(days=i)
    with c:
        if day == today:
            st.markdown(f":blue[**{day.strftime('%a')}**]  \n:blue[{day.day}]")
        else:
            st.markdown(f"**{day.strftime('%a')}**  \n{day.day}")

st.write("")

# All-day events strip: render a row per day with their events
strip_cols = st.columns(7, gap="small")
for i, c in enumerate(strip_cols):
    day = week_start + timedelta(days=i)
    with c:
        day_events = sorted(events_by_day.get(day, []), key=lambda e: e.start)
        if not day_events:
            st.caption("—")
            continue
        for e in day_events:
            with st.container(border=True):
                local_start = to_local(e.start)
                local_end = to_local(e.end)
                time_label = (
                    f"{local_start:%H:%M}–{local_end:%H:%M}"
                    if (local_end - local_start) < timedelta(days=1)
                    else "all day"
                )
                st.markdown(f"**{e.summary}**")
                st.caption(time_label)

st.write("")

# --- Detail list: this week's tasks via shared table component -------------

with st.expander("This week in detail", expanded=True):
    # Server-side time-range narrows to tasks whose DTSTART/DUE/DTEND
    # falls in the week. The server-side result is a SUPERSET of
    # "scheduled this week" (RFC 4791 VTODO time-range matches any of
    # those three anchors), so we additionally client-side refine to
    # tasks whose scheduled_start is strictly in the window.
    week_tasks = caldav.list_tasks(start=week_window_start, end=week_window_end)
    week_tasks = [
        t
        for t in week_tasks
        if t.scheduled_start is not None and week_window_start <= t.scheduled_start < week_window_end
    ]
    week_tasks.sort(key=lambda t: t.scheduled_start or datetime.min)
    render_tasks_table(
        week_tasks,
        caldav=caldav,
        key="cal_this_week_tasks",
        show_project_column=False,
    )
