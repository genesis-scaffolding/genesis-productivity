"""Focus page: start a focus session and watch active sessions tick.

Active focus sessions live in the time_log collection as VTODOs with
STATUS != COMPLETED. Completed sessions are filtered out; a future
"Logs" page will review them. See ADR-014.

The page renders:

- A form to start a new session: optional task, label (default task
  summary when aligned, "Focus session" otherwise), mode (count
  down vs count up), and countdown duration when count down is
  selected.
- A live fragment that shows all active sessions, ticking every
  second, with Stop and Discard buttons. Countdown sessions
  auto-stop in-fragment when their target elapses.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from genesis.caldav.ical import now_utc
from genesis.entities import Task, TimeLogEntryCreate, TimeLogMode, TimeLogStatus
from genesis.ui._state import get_facade

facade = get_facade()
caldav = facade.caldav

# Sync refetch interval (seconds) for active sessions. The fragment
# reruns every 1s for display; the CalDAV refetch is throttled.
REFRESH_INTERVAL_S = 30
DEFAULT_LABEL = "Focus session"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _format_elapsed(elapsed: timedelta) -> str:
    """Render a timedelta as HH:MM:SS or MM:SS depending on length."""
    total = int(elapsed.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _start_session(
    *,
    label: str,
    task: Task | None,
    mode: TimeLogMode,
    countdown_minutes: int | None,
) -> TimeLogEntryCreate:
    """Build a TimeLogEntryCreate for a new session."""
    now = now_utc()
    if mode == TimeLogMode.COUNTDOWN:
        assert countdown_minutes is not None
        due = now + timedelta(minutes=countdown_minutes)
    else:
        due = None
    return TimeLogEntryCreate(
        label=label,
        start=now,
        due=due,
        mode=mode,
        status=TimeLogStatus.IN_PROGRESS,
        task_id=task.uid if task is not None else None,
    )


# -----------------------------------------------------------------------------
# Start session form (static, in parent script)
# -----------------------------------------------------------------------------


def _render_start_form() -> None:
    tasks = caldav.list_tasks(
        status=st.session_state.get("focus_task_status_filter")
    )

    task_options: list[Task | None] = [None, *tasks]
    task_labels = ["(unaligned)"] + [
        t.summary for t in tasks
    ]
    cur_label = st.session_state.get("focus_form_task_label", "(unaligned)")
    cur_idx = task_labels.index(cur_label) if cur_label in task_labels else 0

    selected_label = st.selectbox(
        "Task",
        task_labels,
        index=cur_idx,
        key="focus_form_task_label",
    )
    selected_task = task_options[task_labels.index(selected_label)]

    label_default = selected_task.summary if selected_task is not None else DEFAULT_LABEL
    label = st.text_input(
        "Session label",
        value=st.session_state.get("focus_form_label", label_default),
        key="focus_form_label",
        help=(
            "Shown on the active session card and in the time log. "
            "Defaults to the task summary when a task is selected."
        ),
    )

    mode_label = st.radio(
        "Mode",
        ["Count down", "Count up"],
        index=0 if st.session_state.get("focus_form_mode", "Count down") == "Count down" else 1,
        key="focus_form_mode",
        horizontal=True,
    )
    is_countdown = mode_label == "Count down"

    duration = st.number_input(
        "Duration (minutes)",
        min_value=1,
        max_value=240,
        value=int(st.session_state.get("focus_form_duration", 25)),
        key="focus_form_duration",
        disabled=not is_countdown,
        help="Target session length when counting down.",
    )

    if st.button("Start session", type="primary", key="focus_start_btn"):
        label_clean = (label or "").strip()
        if not label_clean:
            st.error("Session label is required.")
            return
        mode = TimeLogMode.COUNTDOWN if is_countdown else TimeLogMode.COUNTUP
        entry = caldav.create_time_log_entry(
            _start_session(
                label=label_clean,
                task=selected_task,
                mode=mode,
                countdown_minutes=int(duration) if is_countdown else None,
            )
        )
        st.toast(f"Started: {entry.label}")
        st.rerun(scope="app")


# -----------------------------------------------------------------------------
# Live active sessions fragment
# -----------------------------------------------------------------------------


@st.fragment(run_every="1s")
def _render_active_sessions() -> None:
    now = now_utc()
    sessions = caldav.list_time_log(active_only=True)

    # Auto-stop any countdown sessions whose target has passed.
    for s in sessions:
        if (
            s.mode == TimeLogMode.COUNTDOWN
            and s.due is not None
            and now >= s.due
            and s.status != TimeLogStatus.COMPLETED
        ):
            caldav.stop_time_log_entry(
                s.uid, actual_due=s.due, completed_at=now,
            )
            st.toast(f"Auto-stopped: {s.label}")
            st.rerun(scope="app")
            return  # st.rerun() aborts; let next tick handle the rest

    if not sessions:
        st.info("No active sessions. Start one above.")
        return

    for s in sessions:
        _render_session_card(s, now)


def _render_session_card(s, now: datetime) -> None:
    elapsed = now - s.start
    is_countdown = s.mode == TimeLogMode.COUNTDOWN

    with st.container(border=True):
        head_cols = st.columns([6, 1])
        with head_cols[0]:
            st.markdown(f"#### {s.label}")
            if s.task_id:
                st.caption(f"Aligned to a task · {s.mode.value}")
            else:
                st.caption(f"Unaligned · {s.mode.value}")
        with head_cols[1]:
            st.caption(_format_elapsed(elapsed))

        if is_countdown and s.due is not None:
            remaining = s.due - now
            if remaining.total_seconds() > 0:
                st.progress(
                    min(1.0, elapsed.total_seconds() / (s.due - s.start).total_seconds())
                )
                st.caption(
                    f"{_format_elapsed(remaining)} remaining "
                    f"of {_format_elapsed(s.due - s.start)}"
                )
            else:
                over = -remaining
                st.progress(1.0)
                st.caption(f"Time's up · over by {_format_elapsed(over)}")
        else:
            st.caption(f"Running for {_format_elapsed(elapsed)}")

        btn_cols = st.columns(2)
        with btn_cols[0]:
            if st.button("Stop", key=f"focus_stop_{s.uid}", type="primary"):
                actual_due = now if is_countdown else now
                caldav.stop_time_log_entry(
                    s.uid, actual_due=actual_due, completed_at=now,
                )
                st.toast(f"Stopped: {s.label}")
                st.rerun(scope="app")
        with btn_cols[1]:
            if st.button("Discard", key=f"focus_discard_{s.uid}"):
                caldav.delete_time_log_entry(s.uid)
                st.toast(f"Discarded: {s.label}")
                st.rerun(scope="app")


# -----------------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------------


st.title("Focus")
st.caption("Start a focus session and watch it tick across all your devices.")

_render_start_form()
st.divider()
st.subheader("Active sessions")
_render_active_sessions()
