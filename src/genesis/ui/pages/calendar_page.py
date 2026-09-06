"""Calendar page: week grid showing shadow VEVENTs from the events collection."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import streamlit as st

from genesis.entities import EventStatus
from genesis.ui._state import get_facade

facade = get_facade()
caldav = facade.caldav

# --- Week navigation --------------------------------------------------------

if "cal_week" not in st.session_state:
    st.session_state["cal_week"] = date.today()

today = date.today()
header_cols = st.columns([1, 4, 1, 1, 1])
with header_cols[0]:
    st.title("Calendar")
with header_cols[2]:
    if st.button("Today", key="cal_today"):
        st.session_state["cal_week"] = today
        st.rerun()
with header_cols[3]:
    if st.button("◀ Prev", key="cal_prev"):
        st.session_state["cal_week"] -= timedelta(days=7)
        st.rerun()
with header_cols[4]:
    if st.button("Next ▶", key="cal_next"):
        st.session_state["cal_week"] += timedelta(days=7)
        st.rerun()

# Compute Monday of the current week
week_start = st.session_state["cal_week"] - timedelta(days=st.session_state["cal_week"].weekday())
week_end = week_start + timedelta(days=6)
st.caption(f"Week of {week_start.isoformat()} → {week_end.isoformat()}")
st.write("")


# --- Load shadow events for this week ---------------------------------------

# All-day events whose start is in [week_start, week_end + 1 day)
week_window_start = datetime.combine(week_start, time.min)
week_window_end = datetime.combine(week_end + timedelta(days=1), time.min)

all_events = caldav.list_events(status=EventStatus.CONFIRMED)
# Only shadow events (have task_id) for the calendar — those are the
# time-blocked tasks. Standalone events would also show, but per the
# design those aren't currently used; include them anyway for completeness.
shadow_events = [e for e in all_events if e.start < week_window_end and e.end > week_window_start]


def _event_day(e) -> date:
    return e.start.date()


events_by_day: dict[date, list] = {week_start + timedelta(days=i): [] for i in range(7)}
for e in shadow_events:
    d = _event_day(e)
    if d in events_by_day:
        events_by_day[d].append(e)


# --- Week grid: all-day event strip + day columns ---------------------------

# Header row: day labels
day_cols = st.columns(7, gap="small")
for i, c in enumerate(day_cols):
    day = week_start + timedelta(days=i)
    with c:
        label = f"**{day.strftime('%a')}**\n\n{day.day}"
        if day == today:
            st.markdown(f":blue[{label}]")
        else:
            st.markdown(label)

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
                time_label = (
                    f"{e.start:%H:%M}–{e.end:%H:%M}"
                    if (e.end - e.start) < timedelta(days=1)
                    else "all day"
                )
                st.markdown(f"**{e.summary}**")
                st.caption(time_label)

st.write("")

# --- Detail list: tap-into view of the week's events -----------------------

with st.expander("This week in detail", expanded=True):
    if not shadow_events:
        st.info("No events scheduled this week.")
    else:
        for e in sorted(shadow_events, key=lambda e: e.start):
            task_uid = e.task_id
            task = caldav.get_task(task_uid) if task_uid else None
            with st.container(border=True):
                cols = st.columns([6, 2, 2])
                with cols[0]:
                    st.markdown(f"**{e.summary}**")
                    if e.location:
                        st.caption(f"📍 {e.location}")
                with cols[1]:
                    st.caption(
                        f"{e.start:%a %Y-%m-%d}\n{e.start:%H:%M}–{e.end:%H:%M}"
                    )
                with cols[2]:
                    if task and st.button("Open task", key=f"cal_open_{e.uid}"):
                        st.session_state["open_task_dialog"] = task.uid
                        st.rerun()


# Render task dialog if a button above triggered it
if "open_task_dialog" in st.session_state:
    from genesis.ui._render import _edit_task_dialog

    uid = st.session_state.pop("open_task_dialog")
    _edit_task_dialog(uid)
