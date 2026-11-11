"""Configuration tests for the recurrence horizon (ADR-033).

The per-user global default lives in ``configs.yaml`` under the
``recurrence:`` key and can be overridden with
``GENESIS__RECURRENCE__DEFAULT_MATERIALIZED_FUTURE_COUNT``. The
default is 1 and the accepted range is 1 through 100.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from genesis.configs import Configs, RecurrenceConfig


def _write_config(tmp_path: Path, body: str) -> Path:
    """Write a configs.yaml under the per-user layout and return its path."""
    user_dir = tmp_path / "genesis" / "tester"
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / "configs.yaml"
    path.write_text(body)
    return path


def test_default_is_one(tmp_path: Path) -> None:
    """Without YAML or environment override the default is 1."""
    path = _write_config(tmp_path, "")
    configs = Configs(yaml_path=path)
    assert configs.recurrence.default_materialized_future_count == 1


def test_yaml_sets_the_default(tmp_path: Path) -> None:
    """The YAML block sets the default."""
    path = _write_config(tmp_path, "recurrence:\n  default_materialized_future_count: 4\n")
    configs = Configs(yaml_path=path)
    assert configs.recurrence.default_materialized_future_count == 4


def test_environment_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment variable wins over the YAML value."""
    path = _write_config(tmp_path, "recurrence:\n  default_materialized_future_count: 4\n")
    monkeypatch.setenv("GENESIS__RECURRENCE__DEFAULT_MATERIALIZED_FUTURE_COUNT", "7")
    configs = Configs(yaml_path=path)
    assert configs.recurrence.default_materialized_future_count == 7


def test_environment_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment variable alone sets the default."""
    path = _write_config(tmp_path, "")
    monkeypatch.setenv("GENESIS__RECURRENCE__DEFAULT_MATERIALIZED_FUTURE_COUNT", "3")
    configs = Configs(yaml_path=path)
    assert configs.recurrence.default_materialized_future_count == 3


@pytest.mark.parametrize("value", [0, -1, 101, 1000])
def test_out_of_range_values_are_rejected(value: int) -> None:
    """Values below 1 and above 100 are rejected."""
    with pytest.raises(ValidationError):
        RecurrenceConfig(default_materialized_future_count=value)


@pytest.mark.parametrize("value", [0, -1, 101])
def test_out_of_range_yaml_is_rejected(tmp_path: Path, value: int) -> None:
    """An out-of-range YAML value fails the config load."""
    path = _write_config(tmp_path, f"recurrence:\n  default_materialized_future_count: {value}\n")
    with pytest.raises(ValidationError):
        Configs(yaml_path=path)


def test_boundary_values_are_accepted() -> None:
    """1 and 100 are both valid."""
    assert RecurrenceConfig(default_materialized_future_count=1).default_materialized_future_count == 1
    assert RecurrenceConfig(default_materialized_future_count=100).default_materialized_future_count == 100


def test_global_default_applies_to_new_series_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing the global default does not resize an existing series."""
    from genesis.core.facade import Facade
    from genesis.entities import TaskCreate
    from tests.mock_caldav import MockCalDAVFacade

    path = _write_config(tmp_path, "recurrence:\n  default_materialized_future_count: 1\n")
    facade = Facade(yaml_path=path, ensure_server=False)
    facade._caldav = MockCalDAVFacade()  # type: ignore[assignment]

    from datetime import date

    first = facade.create_recurrent_task(
        TaskCreate(title="Pay rent", assigned_date=date(2026, 1, 31)),
        frequency="monthly",
        anchor="assigned_date",
    )
    assert first.series.materialized_future_count == 1

    facade.configs.recurrence.default_materialized_future_count = 4
    second = facade.create_recurrent_task(
        TaskCreate(title="Health insurance", assigned_date=date(2026, 1, 31)),
        frequency="monthly",
        anchor="assigned_date",
    )
    assert second.series.materialized_future_count == 4

    stored = facade.caldav.get_series(first.series.uid)
    assert stored is not None
    assert stored.materialized_future_count == 1
