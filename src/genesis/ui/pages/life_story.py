"""Life Story page: your narrative history.

Thin wrapper around :func:`genesis.ui._profile_page.render`.
Target = ``life_story``; the page title, caption, and empty-state
hint are the only thing this file owns.
"""

from __future__ import annotations

from genesis.profile import ProfileTarget
from genesis.ui._profile_page import render

render(
    target=ProfileTarget.LIFE_STORY,
    title="Life Story",
    caption=(
        "Share your history. Helps your assistant respond with appropriate care when the conversation turns personal."
    ),
    empty_hint=(
        "No life story yet. If you'd like, share what shaped you — "
        "significant experiences, relationships, anything that helps your "
        "assistant understand where you're coming from. Completely optional, "
        "and you can edit or delete this any time."
    ),
    key_prefix="life_story",
)
