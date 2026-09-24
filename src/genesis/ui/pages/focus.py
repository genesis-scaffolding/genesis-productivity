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

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

import streamlit as st

from genesis.caldav.ical import now_utc
from genesis.category.enums import TimeLogCategory
from genesis.category.facade import CategoryFacade
from genesis.entities import TimeLogMode, TimeLogStatus
from genesis.notify import messages
from genesis.ui._render import (
    FOCUS_COMPLETION_PENDING_KEY,
    focus_completion_dialog,
    focus_create_dialog,
)
from genesis.ui._state import get_facade

logger = logging.getLogger(__name__)


def _notify_safely(builder: Callable[[], tuple[str, str, list[str]]]) -> None:
    """Run a message builder and POST to ntfy. Never raises.

    A dead ntfy server (DNS failure, connection timeout, HTTP
    error) must not break the focus page's one-second tick or
    the focus create dialog's Start handler. We log a warning
    so the operator can see the failure but otherwise swallow
    the exception. ADR-027.

    The ``builder`` is invoked lazily so the focus page only
    builds the payload string when ntfy is actually reachable
    (or at least the HTTP attempt succeeds) — but building the
    tuple is cheap, so this is mostly a structural nicety.
    """
    try:
        title, message, tags = builder()
        facade.notify.send(title, message, tags=tags)
    except Exception as exc:
        logger.warning("ntfy notification failed: %s", exc)


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


def _task_title_for_session(session) -> str | None:
    """Return the title of the task aligned to ``session``, if any.

    Used by the notification builders so an aligned session's
    notification body disambiguates from other sessions. Returns
    ``None`` when the session is unaligned or the task was
    deleted (legitimate race). ADR-027.
    """
    if not session.task_id:
        return None
    task = caldav.get_task(session.task_id)
    return task.title if task is not None else None


# Milestone notifications fire on each 30-minute crossing. We
# track which crossings have already fired per session in
# ``st.session_state`` so a rerun after a manual interaction
# cannot double-fire. ADR-027.
_MILESTONE_MINUTES = 30


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
            # Notify the user the countdown finished. Manual Stop
            # on a still-running countdown does not get this
            # notification — the user is at the page and already
            # knows. ADR-027.
            finished_task_title = _task_title_for_session(s)
            _notify_safely(lambda s=s, t=finished_task_title: messages.session_finished(s, task_title=t))
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
        _maybe_fire_milestone(s, now)
        _render_session_card(s, now)


def _maybe_fire_milestone(s, now: datetime) -> None:
    """Fire a 30-minute milestone notification if a crossing just happened.

    We track which multiples of 30 minutes have already been
    notified per session in ``st.session_state`` so a rerun after
    a manual interaction cannot re-fire. On first render of a
    long-running session we may need to back-fire several
    milestones; we issue one POST per crossing, sequenced, so
    the network cost is predictable. ADR-027.
    """
    elapsed_seconds = (now - s.start).total_seconds()
    if elapsed_seconds < _MILESTONE_MINUTES * 60:
        return
    elapsed_minutes = int(elapsed_seconds // 60)
    milestone = elapsed_minutes // _MILESTONE_MINUTES
    if milestone <= 0:
        return
    fired_key = f"focus_notify_milestones_{s.uid}"
    fired = st.session_state.setdefault(fired_key, set())
    new_milestones = [m for m in range(1, milestone + 1) if m not in fired]
    if not new_milestones:
        return
    task_title = _task_title_for_session(s)
    for m in new_milestones:
        minutes_elapsed = m * _MILESTONE_MINUTES
        _notify_safely(lambda s=s, n=minutes_elapsed: messages.session_milestone(s, n, task_title=task_title))
        fired.add(m)


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
