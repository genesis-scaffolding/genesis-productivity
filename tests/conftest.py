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

from ._mock_journal import InMemoryJournalFacade
from .mock_caldav import MockCalDAVFacade


def _free_port() -> int:
    """Bind port 0 to let the kernel pick a free port, then release."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _substitute_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the API router's ``_facade()`` to return a fresh mock facade.

    Returns an object with the same ``.caldav`` and ``.journal``
    attributes plus a no-op ``shutdown()`` so the router's
    ``try/finally facade.shutdown()`` does not error.
    """
    state: dict[str, object] = {"mock": _build_mock_facade()}

    def fake_facade() -> object:
        # The mock keeps state across requests within one test, but
        # a new mock is built for each test (see fixture body).
        return state["mock"]

    monkeypatch.setattr(_productivity_router, "_facade", fake_facade)


def _build_mock_facade() -> object:
    class _MockFacade:
        def __init__(self) -> None:
            self.caldav = MockCalDAVFacade()
            self.journal = InMemoryJournalFacade()

        def shutdown(self) -> None:
            pass

    return _MockFacade()


# ---------------------------------------------------------------------------
# Mock-backed fixture (fast)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_server(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Fast fixture: ``MockCalDAVFacade`` + ``InMemoryJournalFacade``.

    No subprocess, no temp dir, no port allocation. Each test gets a
    fresh mock so isolation is automatic.
    """
    os.environ["GENESIS_API_PASSWORD"] = "test-pw"
    _substitute_facade(monkeypatch)
    with TestClient(app) as client:
        yield client


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
    (data_path / "rights").write_text(
        "[base]\nuser = .+\ncollection = .*\npermissions = RrWw\n"
    )

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


@pytest.fixture
def api_server_real() -> Generator[TestClient, None, None]:
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
        rad_data = root / "radicale"
        for d in (config_home, data_home, rad_data):
            d.mkdir(parents=True, exist_ok=True)

        proc = _spawn_radicale(host, port, rad_data)

        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        os.environ["XDG_DATA_HOME"] = str(data_home)
        os.environ["XDG_STATE_HOME"] = str(root / "state")
        os.environ["XDG_CACHE_HOME"] = str(root / "cache")
        os.environ["GENESIS_API_PASSWORD"] = "test-pw"
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
