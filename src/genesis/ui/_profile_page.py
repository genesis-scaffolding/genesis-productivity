"""Shared renderer for the Profile and Life Story pages.

Both pages target the same per-user markdown store (``profile`` /
``life_story``) with the same shape, so they share this renderer.
The two ``pages/*.py`` files are thin wrappers that pass in the
target name, page title, caption, and empty-state hint.

UI states:

- ``view`` — file exists, render markdown, show Edit + History buttons.
- ``edit`` — show textarea + Save/Cancel; auto-snapshot on Save.
- ``empty`` — file does not exist, render an empty-state hint + Start writing.

History lives inside a collapsed expander so the main view stays
clean. Restore uses the standard inline checkbox + button pattern
from the Settings page so the user has to confirm before any
destructive write.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from genesis.profile import ProfileFacade, ProfileTarget
from genesis.profile.facade import ProfileRead, ProfileWrite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _facade() -> ProfileFacade:
    """Return the per-user :class:`ProfileFacade` from the cached Facade."""
    from genesis.ui._state import get_facade

    return get_facade().profile


def _local_time(ts: str) -> str:
    """Render a snapshot ts (UTC ISO filename form) in local time.

    The filename form ``YYYY-MM-DDTHHMMSSfffZ`` is not a real ISO
    timestamp (no colons), so we reconstruct the datetime, drop
    millis, and let pandas format it in the browser's locale.
    """
    body = ts.rstrip("Z")
    base, _millis = body[:-3], body[-3:]
    dt = datetime.strptime(base, "%Y-%m-%dT%H%M%S").replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _snapshot_label(ts: str, kind: str, size: int, note: str | None) -> str:
    """Build the human-readable option string for the restore picker."""
    when = _local_time(ts)
    bits = [when, kind]
    if note:
        bits.append(note[:40])
    bits.append(f"{size} B")
    return " — ".join(bits)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def _render_history(target: ProfileTarget, key_prefix: str) -> None:
    """Render the History expander for ``target``.

    Collapsed by default. Inside: a compact dataframe of snapshots
    + a Restore picker (selectbox + confirm checkbox + Restore
    button). The picker is disabled when no snapshots exist.
    """
    history = _facade().history(target)
    if not history.ok:
        st.error(history.error)
        return
    n = history.total
    with st.expander(f"History ({n} snapshot{'s' if n != 1 else ''}) — newest first", expanded=False):
        if not history.snapshots:
            st.caption("No snapshots yet. Edit the file to create one.")
            return

        df = pd.DataFrame(
            {
                "when": [_local_time(s.ts) for s in history.snapshots],
                "size": [f"{s.size} B" for s in history.snapshots],
                "kind": [s.meta.command if s.meta is not None else "?" for s in history.snapshots],
                "note": [s.meta.note or "" if s.meta is not None else "" for s in history.snapshots],
            }
        )
        st.dataframe(df, hide_index=True, use_container_width=True)

        st.write("")
        st.markdown("**Restore from snapshot**")

        options = [
            (
                s.ts,
                _snapshot_label(
                    s.ts,
                    s.meta.command if s.meta is not None else "?",
                    s.size,
                    s.meta.note if s.meta is not None else None,
                ),
            )
            for s in history.snapshots
        ]
        labels = [o[1] for o in options]
        selected_label = st.selectbox(
            "Snapshot",
            options=labels,
            index=0,
            key=f"{key_prefix}_restore_pick",
            label_visibility="collapsed",
        )
        # Map the chosen label back to its ts (the API takes the ts).
        ts_by_label = dict(zip(labels, [o[0] for o in options], strict=True))
        selected_ts = ts_by_label[selected_label]

        confirmed = st.checkbox(
            "I understand this will overwrite my current content.",
            key=f"{key_prefix}_restore_confirm",
        )
        restore_clicked = st.button(
            "Restore",
            key=f"{key_prefix}_restore_button",
            type="primary",
            disabled=not confirmed,
        )
        if restore_clicked:
            rr = _facade().restore(target, selected_ts, note="restored from UI")
            if rr.ok:
                st.success(
                    f"Restored. Previous content saved as `{rr.pre_restore_snapshot.filename}`."
                    if rr.pre_restore_snapshot is not None
                    else "Restored."
                )
                # Force a fresh render so view mode reflects the new content.
                st.session_state[f"{key_prefix}_editing"] = False
                st.rerun()
            else:
                st.error(rr.error)


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------


def render(
    *,
    target: ProfileTarget,
    title: str,
    caption: str,
    empty_hint: str,
    key_prefix: str,
) -> None:
    """Render the full page.

    ``key_prefix`` namespaces every Streamlit widget key so the
    profile and life_story pages don't collide in session_state
    (they're served in the same Streamlit process).
    """
    facade = _facade()
    reading: ProfileRead = facade.read(target)
    editing = st.session_state.get(f"{key_prefix}_editing", False)

    st.title(title)
    st.caption(caption)

    # ---- Empty state -------------------------------------------------
    if not reading.ok:
        # Resolve the path so the user knows where the new file
        # will land before they write a single character.
        target_path = next(
            (row.path for row in facade.list().rows if row.target == target),
            "",
        )
        if target_path:
            st.caption(f"`{target_path}`")

        if editing:
            # User clicked "Start writing" on the previous render.
            # Drop straight into the edit form with an empty draft.
            _render_edit(target, key_prefix, initial_content="")
        else:
            st.info(empty_hint)
            if st.button("Start writing", key=f"{key_prefix}_start", type="primary"):
                st.session_state[f"{key_prefix}_editing"] = True
                st.session_state[f"{key_prefix}_draft"] = ""
                st.rerun()
        _render_history(target, key_prefix)
        return

    # ---- File exists -------------------------------------------------
    # Show the on-disk path so the user knows where the file lives.
    st.caption(f"`{reading.path}`")

    if editing:
        _render_edit(target, key_prefix, initial_content=reading.content)
    else:
        _render_view(target, reading, key_prefix)

    _render_history(target, key_prefix)


def _render_view(
    target: ProfileTarget,
    reading: ProfileRead,
    key_prefix: str,
) -> None:
    """Render the read-only view: markdown + Edit button."""
    st.markdown(reading.content)
    st.write("")
    if st.button("Edit", key=f"{key_prefix}_edit_btn", type="primary"):
        st.session_state[f"{key_prefix}_editing"] = True
        st.session_state[f"{key_prefix}_draft"] = reading.content
        st.rerun()


def _render_edit(
    target: ProfileTarget,
    key_prefix: str,
    *,
    initial_content: str,
) -> None:
    """Render the edit mode: textarea + Save/Cancel.

    ``initial_content`` is only used the first time the user enters
    edit mode (when no draft exists in session_state). Subsequent
    reruns keep whatever the user typed so the buffer survives a
    Streamlit rerun triggered by, say, an info banner.
    """
    draft_key = f"{key_prefix}_draft"
    if draft_key not in st.session_state:
        st.session_state[draft_key] = initial_content

    draft = st.text_area(
        "Content",
        value=st.session_state[draft_key],
        height=400,
        key=f"{key_prefix}_textarea",
    )
    # Keep the draft in sync with what the user typed so a Save
    # without rerun reads the latest value.
    st.session_state[draft_key] = draft

    note = st.text_input(
        "Note (optional, recorded in the snapshot)",
        key=f"{key_prefix}_note",
        help="A short description of what changed. Shown in the history list.",
    )

    cols = st.columns(2)
    save_clicked = cols[0].button(
        "Save",
        key=f"{key_prefix}_save",
        type="primary",
    )
    cancel_clicked = cols[1].button(
        "Cancel",
        key=f"{key_prefix}_cancel",
    )

    if cancel_clicked:
        st.session_state[f"{key_prefix}_editing"] = False
        st.session_state.pop(draft_key, None)
        st.rerun()

    if save_clicked:
        wr: ProfileWrite = _facade().write(target, draft or "", note=note or None)
        if wr.ok:
            st.session_state[f"{key_prefix}_editing"] = False
            st.session_state.pop(draft_key, None)
            if wr.snapshot is not None and wr.snapshot.ok:
                st.success(f"Saved. Snapshot: `{wr.snapshot.filename}`")
            else:
                st.success("Saved (no snapshot — content unchanged from the previous version).")
            st.rerun()
        else:
            st.error(wr.error)


__all__ = ["render"]
