"""Unit tests for ``genesis.core.facade.Facade``.

The Facade owns the lifecycle of the Radicale server. These tests
verify the bits that matter for multi-user auth: the Facade must
make sure the configured user exists in the Radicale htpasswd even
when the server was already running before the Facade was
constructed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis.core.facade import Facade


class _FakeServer:
    """Drop-in for ``ServerManager`` that records calls but does no I/O."""

    def __init__(self) -> None:
        self.started = False
        self.ensure_user_calls: list[str] = []

    def start(self) -> None:
        # Simulate the deployed-server case: the server is already
        # up, so start() is a no-op (matches ``ServerManager.start``).
        self.started = True

    def ensure_user(self, username: str) -> None:
        self.ensure_user_calls.append(username)

    def stop(self) -> None:
        pass


def test_facade_calls_ensure_user_even_when_server_already_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for the post-migration PropfindError: when the
    server is already up, ``ServerManager.start`` is a no-op and the
    ``ensure_user`` it would otherwise run is skipped. The Facade
    must call ``ensure_user`` itself.

    Before this fix, a user who signed up while Radicale was already
    running would 500 on every CalDAV request because their
    htpasswd entry never got created.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    fake = _FakeServer()
    from genesis.core import facade as facade_module

    monkeypatch.setattr(facade_module, "ServerManager", lambda *a, **kw: fake)
    # Disable the focus scheduler too; otherwise the Facade's
    # ``ensure_server=True`` path would start a daemon thread
    # for ``alice`` and leak it across tests. ADR-029.
    monkeypatch.setattr(
        "genesis.focus_scheduler.runtime.ensure_scheduler_running",
        lambda user: None,
    )

    configs_path = tmp_path / "config" / "genesis" / "alice" / "configs.yaml"
    configs_path.parent.mkdir(parents=True, exist_ok=True)
    configs_path.write_text("user: alice\n")

    Facade(configs_path, ensure_server=True)

    assert fake.started is True
    assert fake.ensure_user_calls == ["alice"], (
        "Facade must call ensure_user() for the configured user even when the server start is a no-op"
    )


def test_facade_restart_server_stops_and_starts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``restart_server`` must stop a running Radicale and bring up a
    fresh one. This is the recovery path for the post-migration 500:
    the previously-running Radicale has stale in-memory config
    pointing at the pre-migration paths.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    fake = _FakeServer()
    fake.is_running = lambda: True  # type: ignore[method-assign]
    fake.stopped = False  # type: ignore[attr-defined]
    original_stop = fake.stop

    def tracking_stop() -> None:
        fake.stopped = True  # type: ignore[attr-defined]
        original_stop()

    fake.stop = tracking_stop  # type: ignore[method-assign]

    from genesis.core import facade as facade_module

    monkeypatch.setattr(facade_module, "ServerManager", lambda *a, **kw: fake)
    # Disable the focus scheduler; this test does not exercise
    # it and we do not want a daemon thread leaked. ADR-029.
    monkeypatch.setattr(
        "genesis.focus_scheduler.runtime.ensure_scheduler_running",
        lambda user: None,
    )

    configs_path = tmp_path / "config" / "genesis" / "alice" / "configs.yaml"
    configs_path.parent.mkdir(parents=True, exist_ok=True)
    configs_path.write_text("user: alice\n")

    facade = Facade(configs_path, ensure_server=False)
    facade._server = fake  # type: ignore[assignment]  # noqa: SLF001 - simulate "already started"
    facade.restart_server()

    assert fake.stopped is True  # type: ignore[attr-defined]
    assert fake.started is True  # start was called after stop
    assert fake.ensure_user_calls == ["alice"]
