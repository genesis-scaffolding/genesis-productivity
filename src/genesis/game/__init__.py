"""Life-gamification module (ADR-026).

Public surface:

- ``GameFacade`` — the orchestrator. Constructed by the main
  ``Facade`` as ``facade.game``.
- ``XpSource`` and its variants — the typed-union contract between
  the main facade and the game facade.
- ``GameDisabled`` / ``GameClassificationError`` — exceptions raised
  by ``GameFacade.record_xp_event``.
- ``CharacterState`` / ``StatProgress`` — read-only views returned
  by ``GameFacade.get_character``.
"""

from .facade import (
    CharacterState,
    GameClassificationError,
    GameDisabled,
    GameFacade,
    StatProgress,
)
from .source import ReasonSource, SessionSource, TaskSource, XpSource

__all__ = [
    "CharacterState",
    "GameClassificationError",
    "GameDisabled",
    "GameFacade",
    "ReasonSource",
    "SessionSource",
    "StatProgress",
    "TaskSource",
    "XpSource",
]
