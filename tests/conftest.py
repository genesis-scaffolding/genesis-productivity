"""Shared test fixtures for the agent API.

Spins up a fresh Radicale per test via a direct subprocess (not
``ServerManager``) so we avoid the TIME_WAIT race that hits when
the test port is reused across tests in one process. The cost is
~1 second of startup per test, which is acceptable for the suite
size.

Each test also gets a clean ``XDG_*_HOME`` so the Facade's
``resolve_config_path`` and ``paths.data`` point at fresh temp
dirs — not at the user's real ``~/.local/share/genesis``.

The default Radicale port (5232) is avoided: the test Radicale
binds to a kernel-assigned free port picked at session start.
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


def _free_port() -> int:
    """Bind port 0 to let the kernel pick a free port, then release."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _radicale_binary() -> str:
    """Locate the radicale executable in the project venv."""
    candidate = Path(sys.executable).parent / "radicale"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("radicale")
    if found:
        return found
    raise RuntimeError("radicale executable not found")


def _spawn_radicale(host: str, port: int, data_path: Path) -> subprocess.Popen:
    """Spawn a Radicale child process with its own config dir.

    We write ``config``, ``users``, ``rights``, ``collections``
    under ``data_path`` so Radicale's multifilesystem storage can
    read/write there directly. We do NOT use ``ServerManager``
    because it caches ``is_running()`` state across calls and
    briefly returns True against a TIME_WAIT socket, leading to
    silent hangs.
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

    # Probe until the server responds (default credentials).
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
    """Remove every collection under the user root. The directory
    structure stays so Radicale keeps responding."""
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
def api_server() -> Generator[TestClient, None, None]:
    """Per-test FastAPI client with a freshly-spawned Radicale.

    Each test gets its own Radicale on a kernel-assigned port.
    This is slower than a session-scoped server (~1 s startup
    per test) but eliminates cross-test pollution from Radicale
    state and TIME_WAIT races on the test port.
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

        # Point the API at this Radicale via env vars. The Facade
        # reads these on every request, so they take effect
        # immediately.
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
