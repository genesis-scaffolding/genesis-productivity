"""Integration tests for the backfill_xp script against real Radicale.

Spawns a fresh Radicale subprocess per test (mirrors the pattern in
``conftest.py``'s ``api_server_real`` fixture, but adapted for an
out-of-process script invocation).

Marked ``@pytest.mark.integration`` so ``make test`` excludes it.
Run via ``make test-integration``.
"""

from __future__ import annotations

import base64
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scripts.backfill_xp import _resolve_task  # noqa: F401  (importable smoke)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn_radicale(host: str, port: int, data_path: Path) -> subprocess.Popen:
    """Spawn Radicale as a child process and wait for it to come up.

    Same shape as ``conftest.py``'s helper but exposed here so the
    test fixture owns the lifecycle end to end.
    """
    candidate = Path(sys.executable).parent / "radicale"
    if not candidate.exists():
        found = shutil.which("radicale")
        if not found:
            raise RuntimeError("radicale executable not found")
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
        [sys.executable, str(candidate), "-C", str(cfg)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    creds = base64.b64encode(b"default:default").decode()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://{host}:{port}/")
            req.add_header("Authorization", f"Basic {creds}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status in (200, 302, 207):
                    return proc
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError(f"Radicale did not come up on {host}:{port}")


@pytest.fixture
def radicale_env(monkeypatch: pytest.MonkeyPatch):
    """Spawn a fresh Radicale, write a configs.yaml pointing at it,
    yield the (data_path, config_path) pair, then tear down."""
    host = "127.0.0.1"
    port = _free_port()

    tmp = tempfile.TemporaryDirectory(prefix="genesis-backfill-test-")
    root = Path(tmp.name)
    rad_data = root / "radicale"

    config_root = root / "config"
    config_path = config_root / "default" / "configs.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(f"dav:\n  host: {host}\n  port: {port}\n  username: default\n  password: default\n")

    proc = _spawn_radicale(host, port, rad_data)
    try:
        yield {"host": host, "port": port, "config_path": config_path, "rad_data": rad_data}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        tmp.cleanup()


def _seed_via_caldav_api(config_path: Path):
    """Seed completed tasks + sessions using the real Facade
    against the running Radicale.

    Task ``completed_at`` is server-stamped at transition time, so
    we cannot set arbitrary historical values via the public API.
    We accept that the seeded tasks show ``completed_at = now`` and
    run the script with an unbounded window (no ``--from`` /
    ``--to``).
    """
    from genesis.category.enums import TimeLogCategory
    from genesis.core.facade import Facade
    from genesis.entities import (
        TaskCreate,
        TaskStatus,
        TaskUpdate,
        TimeLogEntryCreate,
        TimeLogMode,
        TimeLogStatus,
    )

    facade = Facade(config_path, ensure_server=False)
    t1 = facade.caldav.create_task(TaskCreate(title="finish paperwork"))
    facade.caldav.update_task(t1.uid, TaskUpdate(status=TaskStatus.COMPLETED))

    t2 = facade.caldav.create_task(TaskCreate(title="read atomic habits ch3"))
    facade.caldav.update_task(t2.uid, TaskUpdate(status=TaskStatus.COMPLETED))

    # Session can set completed_at directly via create.
    completed_at = datetime.now(UTC) - timedelta(minutes=50)
    start = completed_at - timedelta(minutes=50)
    facade.caldav.create_time_log_entry(
        TimeLogEntryCreate(
            label="deep work",
            mode=TimeLogMode.COUNTDOWN,
            start=start,
            due=completed_at,
            completed_at=completed_at,
            category=TimeLogCategory.WORK,
            status=TimeLogStatus.COMPLETED,
        )
    )
    return facade


@pytest.mark.integration
def test_backfill_against_real_radicale(radicale_env):
    """Run the full backfill against a real Radicale instance.

    Sequence:
    1. Seed 2 completed tasks + 1 completed session via the Facade.
    2. Dry-run the script. Verify no XP events written.
    3. Apply. Verify 3 XP events exist with correct stat/delta.
    4. Re-apply. Verify idempotent (still 3 events, all skipped).
    """

    from genesis.core.facade import Facade

    config_path = radicale_env["config_path"]
    seeded = _seed_via_caldav_api(config_path)

    # Pre-condition: no XP events yet.
    assert len(seeded.caldav.list_xp_events()) == 0
    seeded.shutdown()

    # Dry run.
    rc = subprocess.run(
        [sys.executable, "scripts/backfill_xp.py", "--config-path", str(config_path), "--dry-run"],
        check=False,
    ).returncode
    assert rc == 0
    facade = Facade(config_path, ensure_server=False)
    try:
        assert len(facade.caldav.list_xp_events()) == 0
    finally:
        facade.shutdown()

    # Apply.
    rc = subprocess.run(
        [sys.executable, "scripts/backfill_xp.py", "--config-path", str(config_path)],
        check=False,
    ).returncode
    assert rc == 0
    facade = Facade(config_path, ensure_server=False)
    try:
        events = facade.caldav.list_xp_events()
        assert len(events) == 3
        task_events = [e for e in events if e.source_task_id]
        session_events = [e for e in events if e.source_session_id]
        assert len(task_events) == 2
        assert len(session_events) == 1
        sess = session_events[0]
        assert sess.stat_id == "discipline"
        assert sess.delta == 2
    finally:
        facade.shutdown()

    # Idempotent re-run.
    rc = subprocess.run(
        [sys.executable, "scripts/backfill_xp.py", "--config-path", str(config_path)],
        check=False,
    ).returncode
    assert rc == 0
    facade = Facade(config_path, ensure_server=False)
    try:
        # Still 3 events — re-run skipped all candidates.
        assert len(facade.caldav.list_xp_events()) == 3
    finally:
        facade.shutdown()


from datetime import UTC, datetime, timedelta  # noqa: E402  (used in the function body above)
