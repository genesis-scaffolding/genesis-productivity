"""Focus page: start a focus session and watch active sessions tick.

Active focus sessions live in the time_log collection as VTODOs with
STATUS != COMPLETED. Completed sessions are filtered out; a future
"Logs" page will review them. See ADR-014.

The page renders:

- A button that opens the shared ``focus_create_dialog`` for
  starting a new session. The same dialog is reachable from the
  Tasks page's per-row "Focus" action; here it opens with no
  pre-selected task.
- A live fragment that shows all active sessions, ticking every
  second, with Stop and Discard buttons. Countdown sessions
  auto-stop in-fragment when their target elapses.

When a session with a linked task ends (either via the auto-stop
path or via manual Stop), the page sets
``st.session_state["focus_completion_pending"]`` and triggers an
app-level rerun. The next render reads the flag at the top of this
script and pops the completion dialog.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from genesis.caldav.ical import now_utc
from genesis.category.enums import TimeLogCategory
from genesis.category.facade import CategoryFacade
from genesis.entities import TimeLogMode, TimeLogStatus
from genesis.ui._render import (
    FOCUS_COMPLETION_PENDING_KEY,
    focus_completion_dialog,
    focus_create_dialog,
)
from genesis.ui._state import get_facade

facade = get_facade()
caldav = facade.caldav
category_facade = CategoryFacade(caldav)

CATEGORY_OPTIONS = ["Auto", *list(TimeLogCategory)]


def _category_label(opt) -> str:
    """Render a category option for the selectbox. ``Auto`` becomes
    ``"Auto (inferred)"``; enum members are capitalised.

    Used with ``format_func`` on ``st.selectbox`` so the underlying
    option (the enum member or the literal string ``"Auto"``) is
    what lands in ``st.session_state``, not the displayed label.
    """
    if opt == "Auto":
        return "Auto (inferred)"
    return opt.value.capitalize()


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


def _set_completion_pending(session) -> None:
    """If ``session`` was aligned to a task, queue the completion dialog.

    The dialog reads ``task.title`` for display, not the session
    label, so we re-fetch the task rather than reusing ``s.label``.
    A task that disappeared between session start and stop is a
    legitimate race (deleted on another device); in that case the
    completion prompt is silently skipped.
    """
    if not session.task_id:
        return
    task = caldav.get_task(session.task_id)
    if task is None:
        return
    st.session_state[FOCUS_COMPLETION_PENDING_KEY] = {
        "task_uid": task.uid,
        "task_title": task.title,
    }


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
            category = _resolve_category(s)
            facade.stop_session(
                s.uid,
                actual_due=s.due,
                completed_at=now,
                category=category,
            )
            _set_completion_pending(s)
            # Rerun at app scope so the parent script picks up the
            # completion-pending flag and pops the dialog. We lose
            # the previous "Auto-stopped" toast on the rerun; the
            # completion dialog is the more useful signal here.
            st.rerun(scope="app")
            return

    if not sessions:
        st.info("No active sessions. Start one above.")
        return

    for s in sessions:
        _render_session_card(s, now)


def _resolve_category(s) -> TimeLogCategory:
    """Resolve the category the active session will be saved with.

    Returns the explicit pick when the user chose one, otherwise the
    inference result for the session's label + task alignment. Falls
    back to ``PERSONAL`` when inference has no confident signal.
    """
    pick_key = f"focus_cat_pick_{s.uid}"
    pick = st.session_state.get(pick_key, "Auto")
    if pick != "Auto":
        return TimeLogCategory(pick)
    inferred_key = f"focus_cat_inferred_{s.uid}"
    inferred = st.session_state.get(inferred_key)
    if inferred is None:
        inferred = category_facade.infer(s.label, s.task_id)
        st.session_state[inferred_key] = inferred
    return inferred or TimeLogCategory.PERSONAL


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
                st.progress(min(1.0, elapsed.total_seconds() / (s.due - s.start).total_seconds()))
                st.caption(f"{_format_elapsed(remaining)} remaining of {_format_elapsed(s.due - s.start)}")
            else:
                over = -remaining
                st.progress(1.0)
                st.caption(f"Time's up · over by {_format_elapsed(over)}")
        else:
            st.caption(f"Running for {_format_elapsed(elapsed)}")

        pick_key = f"focus_cat_pick_{s.uid}"
        current_pick = st.session_state.get(pick_key, "Auto")
        pick_index = CATEGORY_OPTIONS.index(current_pick)
        st.selectbox(
            "Category",
            options=CATEGORY_OPTIONS,
            format_func=_category_label,
            index=pick_index,
            key=pick_key,
            label_visibility="collapsed",
            help=(
                "Auto resolves from inference (history of this label, "
                "then keyword rules). Pick anything else to override."
            ),
        )
        st.caption(f"Will save as: {_resolve_category(s).value}")

        btn_cols = st.columns(2)
        with btn_cols[0]:
            if st.button("Stop", key=f"focus_stop_{s.uid}", type="primary"):
                category = _resolve_category(s)
                facade.stop_session(
                    s.uid,
                    actual_due=now,
                    completed_at=now,
                    category=category,
                )
                _set_completion_pending(s)
                st.rerun(scope="app")
        with btn_cols[1]:
            if st.button("Discard", key=f"focus_discard_{s.uid}"):
                caldav.delete_time_log_entry(s.uid)
                st.rerun(scope="app")


# -----------------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------------


st.title("Focus")
st.caption("Start a focus session and watch it tick across all your devices.")

# If a session with a linked task just ended (auto-stop or manual
# stop), the active-sessions fragment set this flag and triggered an
# app-level rerun. Pop the flag at the moment we open the dialog so
# any subsequent on_dismiss rerun does not re-open it. The Yes/No
# handlers in the dialog also pop the flag as belt-and-suspenders.
pending = st.session_state.pop(FOCUS_COMPLETION_PENDING_KEY, None)
if pending is not None:
    focus_completion_dialog(
        task_uid=pending["task_uid"],
        task_title=pending["task_title"],
    )

if st.button("Start focus session", type="primary", key="focus_open_btn"):
    focus_create_dialog()

st.divider()
st.subheader("Active sessions")
_render_active_sessions()
