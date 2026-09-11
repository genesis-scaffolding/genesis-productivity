"""Unit tests for ``genesis.ui._state`` shutdown wiring.

The Streamlit worker process installs SIGTERM/SIGINT handlers that
tear down cached Facades so the Radicale subprocess does not
become orphaned when the shell sends ``Ctrl+C`` to ``make serve``.
"""

from __future__ import annotations

import signal

import pytest

import genesis.ui._state as state


class _FakeFacade:
    def __init__(self) -> None:
        self.shutdown_called = 0

    def shutdown(self) -> None:
        self.shutdown_called += 1


def test_sigterm_handler_shuts_down_cached_facades(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SIGTERM during a Streamlit session must terminate Radicale."""
    # Install the handler on SIGTERM (and leave SIGINT alone to
    # avoid interfering with pytest's own handler).
    monkeypatch.setattr(signal, "SIG_DFL", signal.SIG_DFL)
    monkeypatch.setattr(signal, "SIGTERM", signal.SIGTERM)

    # Re-install the handler under the test's signal setup.
    state._install_signal_handlers()

    fake = _FakeFacade()
    state._facade_cache["alice"] = fake  # type: ignore[assignment]

    # Simulate the signal: invoke the handler directly. The handler
    # calls os.kill which would terminate the test process, so
    # monkeypatch that out.
    import genesis.ui._state as mod

    monkeypatch.setattr(mod.os, "kill", lambda *a, **kw: None)

    handler = signal.getsignal(signal.SIGTERM)
    assert handler is not None
    handler(signal.SIGTERM, None)

    assert fake.shutdown_called == 1


def test_atexit_handler_shuts_down_cached_facades() -> None:
    """atexit must also clean up cached Facades for normal exits."""
    fake = _FakeFacade()
    state._facade_cache["alice"] = fake  # type: ignore[assignment]

    state._shutdown_all()

    assert fake.shutdown_called == 1
    assert "alice" not in state._facade_cache


def test_shutdown_all_is_best_effort() -> None:
    """A misbehaving Facade.shutdown must not stop the others."""

    class _Boom:
        def shutdown(self) -> None:
            raise RuntimeError("boom")

    good = _FakeFacade()
    state._facade_cache["bad"] = _Boom()  # type: ignore[assignment]
    state._facade_cache["good"] = good  # type: ignore[assignment]

    state._shutdown_all()

    assert good.shutdown_called == 1
    assert state._facade_cache == {}


def test_clear_cached_facade_swallows_shutdown_errors() -> None:
    class _Boom:
        def shutdown(self) -> None:
            raise RuntimeError("boom")

    state._facade_cache["bad"] = _Boom()  # type: ignore[assignment]
    state.clear_cached_facade("bad")  # must not raise
    assert "bad" not in state._facade_cache


def test_logout_does_not_evict_cached_facade() -> None:
    """Logout must not shut down the shared Radicale subprocess.

    Multiple browser sessions share the same ``_facade_cache`` and
    the same Radicale subprocess. If Alice's logout called
    ``clear_cached_facade`` it would tear down Radicale for Bob's
    in-flight session. The contract: logout pops the session key
    only, the cache entry stays.
    """
    import inspect

    from genesis.ui import _auth

    src = inspect.getsource(_auth.logout)
    assert "clear_cached_facade" not in src, (
        "logout() must not call clear_cached_facade; doing so would shut down Radicale for every other logged-in user"
    )
