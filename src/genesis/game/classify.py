"""Keyword-based stat inference (ADR-026).

Tier-2 of the inference flow: when tier-1 (session category mapping
or caller-provided explicit value) doesn't pin a stat, run a
keyword scan over the source text and pick the stat with the most
hits.

Implementation: NLTK Porter stemmer. The keyword list and the
incoming text are both stemmed; matching is on stems, so
"meeting" and "meet" both hit a keyword "meet". This avoids
maintaining exhaustive inflection lists in the keyword map.

Multi-word keywords ("difficult conversation") are stored as a set
of stemmed tokens; matching requires ALL of them to be present in
the text's token set. This gives phrase-aware matching without
separate phrase logic.

Porter is pure-Python, ships in ``nltk.stem``, no model download.

Tie-break: first stat id in ``STAT_IDS`` order wins. Defaults to
"discipline" when no stat scores any hits (for tasks; sessions on
PERSONAL/OTHER should be raising before reaching here).
"""

from __future__ import annotations

import re

from nltk.stem import PorterStemmer

from ..configs import GameConfig

# The five design-note stats. The order here is the canonical
# tie-break order when a caller doesn't pass a config.
DEFAULT_STAT_ORDER: tuple[str, ...] = ("courage", "discipline", "charisma", "knowledge", "vitality")

_STAT_KEYWORDS: dict[str, list[str]] = {
    "courage": [
        "difficult conversation",
        "hard conversation",
        "hard email",
        "cold call",
        "cold email",
        "scary",
        "confront",
        "push back",
        "decline",
        "reject",
        "apologize",
        "admit",
        "first time",
        "before ready",
        "ask out",
        "brave",
    ],
    "discipline": [
        "routine",
        "habit",
        "deadline",
        "follow up",
        "ship",
        "finish",
        "weekly",
        "monthly",
        "plan",
        "admin",
        "paperwork",
        "report",
        "schedule",
        "calendar",
        "review",
        "audit",
        "tidy",
        "organize",
        "check in",
        "follow through",
    ],
    "charisma": [
        "call",
        "meet",
        "conversation",
        "family",
        "social",
        "date",
        "dinner",
        "lunch",
        "coffee",
        "chat",
        "talk",
        "present",
        "attend",
        "host",
        "friend",
        "partner",
        "mom",
        "dad",
        "birthday",
        "anniversary",
        "catch up",
        "deep talk",
        "quality time",
    ],
    "knowledge": [
        "read",
        "study",
        "learn",
        "research",
        "course",
        "book",
        "paper",
        "write",
        "draft",
        "chapter",
        "blog",
        "notes",
        "tutorial",
        "practice",
        "spec",
        "design",
        "analysis",
    ],
    "vitality": [
        "gym",
        "workout",
        "lift",
        "run",
        "walk",
        "sleep",
        "nap",
        "eat",
        "meal",
        "cook",
        "food",
        "nature",
        "hike",
        "swim",
        "cycle",
        "yoga",
        "meditate",
        "doctor",
        "appointment",
        "recovery",
        "rest",
        "sauna",
        "stretch",
        "water",
        "push day",
        "pull day",
    ],
}

_TOKEN_RE = re.compile(r"\b[a-z][a-z']*\b")

_stemmer = PorterStemmer()

# Precompute each keyword as a set of stemmed tokens. Multi-word
# phrases ("["difficult conversation"]") become a set of stems that
# must ALL be present in the text's token set to count as a hit;
# single-word keywords become a singleton set. Counting how many
# of a stat's keyword sets are subsets of the text's token set
# gives phrase-aware matching without separate phrase logic.
_STEMMED_KEYWORDS: dict[str, list[set[str]]] = {
    stat_id: [{_stemmer.stem(w) for w in k.split()} for k in keywords] for stat_id, keywords in _STAT_KEYWORDS.items()
}


def tokenize(text: str) -> list[str]:
    """Lowercase, alpha-tokenise ``text``. Returns stemmed tokens.

    Apostrophes are kept inside tokens ("don't" stays one token)
    so common contractions don't fragment.
    """
    return [_stemmer.stem(t) for t in _TOKEN_RE.findall(text.lower())]


def infer_stat_from_text(
    text: str,
    *,
    config: GameConfig | None = None,
    default_stat_id: str | None = None,
    fail_on_no_match: bool = False,
) -> str | None:
    """Return the most likely stat id for ``text``.

    Counts phrase-aware keyword matches per stat. Each keyword (or
    multi-word phrase) contributes one hit when all its stemmed
    tokens are present in the text. Highest count wins; ties go
    to the first stat id in the config's stats order (or
    ``DEFAULT_STAT_ORDER`` when no config is passed).

    On no match:
    `` - If ``fail_on_no_match`` is True, returns ``None``.
    `` - Else returns ``default_stat_id`` if provided, else the
        first stat in the order.

    Use ``default_stat_id="discipline"`` for tasks (per ADR-026:
    tasks default to discipline on miss). Use
    ``fail_on_no_match=True`` for sessions on PERSONAL/OTHER,
    where guessing is worse than failing.
    """
    stat_order = _stat_order(config)
    if not stat_order:
        raise ValueError("no stats configured")

    tokens = tokenize(text)
    if not tokens:
        if fail_on_no_match:
            return None
        return default_stat_id if default_stat_id is not None else stat_order[0]

    token_set = set(tokens)
    scores: dict[str, int] = dict.fromkeys(stat_order, 0)
    for stat_id in stat_order:
        for kw_set in _STEMMED_KEYWORDS.get(stat_id, ()):
            if kw_set <= token_set:
                scores[stat_id] += 1

    best_score = max(scores.values())
    if best_score == 0:
        if fail_on_no_match:
            return None
        return default_stat_id if default_stat_id is not None else stat_order[0]

    # Iterate stat_order to break ties by config order.
    for stat_id in stat_order:
        if scores[stat_id] == best_score:
            return stat_id

    return stat_order[0]


def _stat_order(config: GameConfig | None) -> tuple[str, ...]:
    if config is not None and config.stats:
        return tuple(s.id for s in config.stats)
    return DEFAULT_STAT_ORDER


__all__ = ["DEFAULT_STAT_ORDER", "infer_stat_from_text", "tokenize"]
