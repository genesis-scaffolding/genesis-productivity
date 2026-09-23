"""Helpers for loading the per-user game config (ADR-026).

The config lives in ``genesis.configs.Configs.game``. This module
just provides a thin pass-through so callers can import it from
the game namespace without circular dependencies.
"""

from __future__ import annotations

from ..configs import GameConfig


def load_game_config(configs_game: GameConfig) -> GameConfig:
    """Return the resolved game config.

    Currently a thin pass-through; the helper exists so the game
    facade does not import from ``genesis.configs`` directly when
    the config wiring moves (e.g. when we add an HTTP edit
    surface).
    """
    return configs_game


__all__ = ["load_game_config"]
