"""Character page: stat progress, XP-to-next bars, recent XP events (ADR-026).

Read-only surface for the life-gamification layer. Shows current
stats, XP-to-next-level progress bars, and the most recent XP
events. No edit affordances this round; ``game.configs.yaml`` is
the source of truth for stat changes.

Gated by ``configs.game.enabled``: when False the page shows an
empty-state info explaining how to enable the layer. The page
itself is removed from the User nav when disabled, so this is a
fallback for direct URL navigation.
"""

from __future__ import annotations

import streamlit as st

from genesis.game import xp as game_xp
from genesis.ui._state import get_facade

facade = get_facade()

st.title("Character")
st.caption("Your life-gamification state.")

if not facade.configs.game.enabled:
    st.info("Game layer is off. Set `game.enabled: true` in your `configs.yaml` to enable.")
    st.stop()

character = facade.game.get_character()

# Stat cards: 5 across, level + label + XP-to-next bar.

# Stat cards: 5 across, level + label + XP-to-next bar.
cols = st.columns(len(character.progress), gap="medium")
for col, progress in zip(cols, character.progress, strict=True):
    with col:
        in_deficit = progress.xp < 0
        with st.container(border=True):
            # Warning icon next to the stat name when in deficit;
            # subtle but visible cue that this stat needs attention.
            if in_deficit:
                st.caption(f":material/warning: {progress.name}")
            else:
                st.caption(progress.name)
            # The level/label line is rendered red when in deficit
            # so the worst-case persona label (Cowardly, Chaotic,
            # Fragile, etc.) reads as a warning rather than a
            # neutral state.
            if in_deficit:
                st.markdown(f"## :red[L{progress.level} · {progress.label}]")
            else:
                st.markdown(f"## L{progress.level} · {progress.label}")
            st.caption(f"{progress.xp} XP total")
            if progress.xp_to_next is None:
                st.progress(1.0)
                st.caption("Max level")
            else:
                # Bar shows progress within the current level band,
                # not raw total XP. Compute the lower and upper
                # threshold for the current level and render
                # ``(xp - lower) / (upper - lower)``.
                lower = game_xp.XP_CURVE[progress.level - 1] if progress.level >= 1 else 0
                upper = game_xp.XP_CURVE[progress.level] if progress.level < game_xp.MAX_LEVEL else game_xp.XP_CURVE[-1]
                band = max(1, upper - lower)
                into_band = max(0, progress.xp - lower)
                ratio = min(1.0, into_band / band)
                st.progress(ratio)
                st.caption(f"{progress.xp_to_next} XP to next level")

st.divider()

# Recent events table.
st.subheader(f"Recent XP events ({len(character.recent_events)})")
if not character.recent_events:
    st.info("No XP events yet. Complete a task or stop a focus session to start.")
else:
    rows = []
    for event in character.recent_events:
        rows.append(
            {
                "When": event.occurred_at.strftime("%Y-%m-%d %H:%M"),
                "Stat": event.stat_id,
                "Delta": event.delta,
                "Kind": event.kind.value,
                "Reason": event.reason,
                "Source": (
                    f"task:{event.source_task_id}"
                    if event.source_task_id
                    else f"session:{event.source_session_id}"
                    if event.source_session_id
                    else "ad-hoc"
                ),
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)
