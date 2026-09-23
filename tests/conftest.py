"""Shared test fixtures for the agent API.

Two flavors of ``api_server`` fixture:

- ``api_server`` (default) — backed by the in-memory
  ``MockCalDAVFacade`` and ``InMemoryJournalFacade``. Fast
  (~10 ms setup per test) and isolated. Use for filter,
  pagination, sort, auth, error, and Swagger UI contract tests.

- ``api_server_real`` — backed by a real Radicale subprocess and
  a temp journal directory. ~1 s setup per test but exercises the
  real iCal + markdown serialization paths. Use only for the
  handful of tests that prove round-trip persistence (datetime
  format, journal footer persistence, COMPLETED side effect).

Tests that need the real fixture are marked
``@pytest.mark.integration`` so the fast ``make test`` run
excludes them.
"""

from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genesis.api.app import app
from genesis.api.routers import productivity as _productivity_router
from genesis.users.store import UserStore

from ._mock_journal import InMemoryJournalFacade
from .mock_caldav import MockCalDAVFacade

# Pin the local timezone to UTC so tests that exercise
# ``genesis.tz.to_utc`` (which treats naive datetimes as local time)
# are deterministic regardless of the developer's machine. ``time.tzset``
# reads ``$TZ``; it must be called explicitly on CPython.
os.environ.setdefault("TZ", "UTC")
if hasattr(time, "tzset"):
    time.tzset()


def _free_port() -> int:
    """Bind port 0 to let the kernel pick a free port, then release."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _substitute_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the API router's ``_facade()`` to return a fresh mock facade.

    The router signature is ``_facade(subject)``; the mock ignores
    the subject and returns the same shared facade for the whole
    test, so per-request state is preserved within a test and
    isolated between tests.
    """
    state: dict[str, object] = {"mock": _build_mock_facade()}

    def fake_facade(subject: str) -> object:
        return state["mock"]

    monkeypatch.setattr(_productivity_router, "_facade", fake_facade)


def _build_mock_facade() -> object:
    class _MockFacade:
        def __init__(self) -> None:
            self.caldav = MockCalDAVFacade()
            self.journal = InMemoryJournalFacade()

        def shutdown(self) -> None:
            pass

        # ADR-026: API handlers call these on the facade after
        # task / session writes. The mock backend doesn't enforce
        # the game layer (tests that care wire up a real Facade
        # via ``api_server_with_mock`` and use ``facade.caldav``
        # directly). These no-ops keep the existing test surface
        # intact.
        def _fire_xp_safely(self, fn, **kwargs):
            try:
                fn(**kwargs)
            except Exception:
                pass

        def record_xp_event(self, **kwargs):
            return None

        def complete_task(self, uid):
            from genesis.entities import TaskStatus, TaskUpdate

            return self.caldav.update_task(uid, TaskUpdate(status=TaskStatus.COMPLETED))

        def stop_session(self, uid, *, actual_due, completed_at, category=None):
            return self.caldav.stop_time_log_entry(
                uid, actual_due=actual_due, completed_at=completed_at, category=category
            )

    return _MockFacade()


# ---------------------------------------------------------------------------
# Mock-backed fixture (fast)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[TestClient, None, None]:
    """Fast fixture: ``MockCalDAVFacade`` + ``InMemoryJournalFacade``.

    No subprocess, no temp dir, no port allocation. Each test gets a
    fresh mock so isolation is automatic. Seeds a ``default`` user
    in a temp users file.
    """
    store_path = tmp_path / "users.yaml"
    _seed_default_user(store_path)
    monkeypatch.setattr("genesis.users.store._default_store", UserStore(store_path))
    _substitute_facade(monkeypatch)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def api_server_with_mock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[tuple[TestClient, MockCalDAVFacade], None, None]:
    """Like :func:`api_server` but also yields the underlying mock facade.

    Use when a test needs to seed time log entries (no POST endpoint
    exists for them) or otherwise mutate backend state directly.
    """
    store_path = tmp_path / "users.yaml"
    _seed_default_user(store_path)
    monkeypatch.setattr("genesis.users.store._default_store", UserStore(store_path))
    mock = _build_mock_facade()
    state: dict[str, object] = {"mock": mock}

    def fake_facade(subject: str) -> object:
        return state["mock"]

    monkeypatch.setattr(_productivity_router, "_facade", fake_facade)
    with TestClient(app) as client:
        yield client, mock.caldav  # type: ignore[1]


# ---------------------------------------------------------------------------
# Real-Radicale fixture (slow; integration only)
# ---------------------------------------------------------------------------


def _radicale_binary() -> str:
    candidate = Path(sys.executable).parent / "radicale"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("radicale")
    if found:
        return found
    raise RuntimeError("radicale executable not found")


def _spawn_radicale(host: str, port: int, data_path: Path) -> subprocess.Popen:
    """Spawn a Radicale child process with its own config dir.

    We bypass ``ServerManager`` because it caches ``is_running()``
    state across calls and briefly returns True against a TIME_WAIT
    socket, leading to silent hangs.
    """
    data_path.mkdir(parents=True, exist_ok=True)
    cfg = data_path / "config"
    cfg.write_text(
        f"[server]\nhosts = {host}:{port}\n\n"
        f"[storage]\ntype = multifilesystem\n"
        f"filesystem_folder = {data_path / 'collections'}\n\n"
        f"[auth]\ntype = htpasswd\n"
        f"htpasswd_filename = {data_path / 'users'}\n"
        f"htpasswd_encryption = plain\n\n"
        f"[rights]\ntype = from_file\n"
        f"file = {data_path / 'rights'}\n"
    )
    (data_path / "users").write_text("default:default\n")
    (data_path / "rights").write_text("[base]\nuser = .+\ncollection = .*\npermissions = RrWw\n")

    proc = subprocess.Popen(
        [sys.executable, _radicale_binary(), "-C", str(cfg)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    creds = base64.b64encode(b"default:default").decode()
    deadline = time.time() + 15
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://{host}:{port}/")
            req.add_header("Authorization", f"Basic {creds}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status in (200, 302, 207):
                    return proc
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError(f"Radicale did not come up on {host}:{port}: {last_err}")


def _wipe(data_path: Path, config_home: Path) -> None:
    col_root = data_path / "collections" / "collection-root" / "default"
    if col_root.exists():
        for child in col_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    journal_dir = config_home / "genesis" / "default" / "journal"
    if journal_dir.exists():
        shutil.rmtree(journal_dir)


def _seed_default_user(store_path: Path) -> None:
    """Write a fresh users file containing only ``default`` with the
    test password."""
    store = UserStore(store_path)
    store.add("default", "test-pw")


@pytest.fixture
def api_server_real(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Slow fixture: real Radicale + real journal files.

    ~1 s per test for the subprocess startup. Use only for the
    handful of round-trip tests in ``test_api.py`` that prove
    iCal/markdown persistence works.
    """
    host = "127.0.0.1"
    port = _free_port()

    tmp = tempfile.TemporaryDirectory(prefix="genesis-api-test-")
    try:
        root = Path(tmp.name)
        config_home = root / "config"
        data_home = root / "data"
        state_home = root / "state"
        cache_home = root / "cache"
        rad_data = root / "radicale"
        for d in (config_home, data_home, state_home, cache_home, rad_data):
            d.mkdir(parents=True, exist_ok=True)

        # Seed the default user in the test users file before
        # monkeypatching the module-level store.
        _seed_default_user(state_home / "genesis" / "users.yaml")
        monkeypatch.setattr(
            "genesis.users.store._default_store",
            UserStore(state_home / "genesis" / "users.yaml"),
        )

        proc = _spawn_radicale(host, port, rad_data)

        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        os.environ["XDG_DATA_HOME"] = str(data_home)
        os.environ["XDG_STATE_HOME"] = str(state_home)
        os.environ["XDG_CACHE_HOME"] = str(cache_home)
        os.environ["GENESIS__DAV__HOST"] = host
        os.environ["GENESIS__DAV__PORT"] = str(port)
        os.environ["GENESIS__DAV__USERNAME"] = "default"
        os.environ["GENESIS__DAV__PASSWORD"] = "default"

        _wipe(rad_data, config_home)

        with TestClient(app) as client:
            yield client

        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        tmp.cleanup()
        for key in (
            "GENESIS__DAV__HOST",
            "GENESIS__DAV__PORT",
            "GENESIS__DAV__USERNAME",
            "GENESIS__DAV__PASSWORD",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
        ):
            os.environ.pop(key, None)
