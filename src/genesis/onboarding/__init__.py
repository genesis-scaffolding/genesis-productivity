"""Durable bootstrap state for the onboarding wizard.

A new account goes from empty to populated through a seven-step
wizard. This package owns the wizard's own state: where the user
is, whether the bootstrap is finished, and the choices made along
the way. The state lives in one JSON file per user.

The module is a leaf: standard library plus pydantic. It knows
nothing about Streamlit, about the configs, or about the data the
wizard creates.

See ADR-032.
"""

from .facade import OnboardingFacade
from .state import (
    STATE_FILENAME,
    STEP_ORDER,
    OnboardingState,
    OnboardingStatus,
    OnboardingStep,
    read_state,
    write_state,
)

__all__ = [
    "STATE_FILENAME",
    "STEP_ORDER",
    "OnboardingFacade",
    "OnboardingState",
    "OnboardingStatus",
    "OnboardingStep",
    "read_state",
    "write_state",
]
