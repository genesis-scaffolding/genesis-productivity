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
from .mock_carddav import MockCardDAVFacade

# Pin the local timezone to UTC so tests that exercise
# ``genesis.tz.to_utc`` (which treats naive datetimes as local time)
# are deterministic regardless of the developer's machine. ``time.tzset``
# reads ``$TZ``; it must be called explicitly on CPython.
os.environ.setdefault("TZ", "UTC")
if hasattr(time, "tzset"):
    time.tzset()


@pytest.fixture(autouse=True)
def _clear_facade_cache():
    """Reset the per-subject API facade cache between tests.

    The production API caches one :class:`Facade` per subject (to avoid
    re-running CalDAV principal discovery on every request). Without this
    reset, a facade built by an earlier test — pointing at that test's
    ephemeral Radicale / config — would leak into the next test, so real-
    Radicale round-trip tests would hit a dead server. Clearing before and
    after each test keeps them isolated.
    """
    from genesis.api.routers.productivity import _facade_cache_clear

    _facade_cache_clear()
    yield
    _facade_cache_clear()


def _free_port() -> int:
    """Bind port 0 to let the kernel pick a free port, then release."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _substitute_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch every router's ``_facade()`` to return a fresh mock facade.

    The productivity, profile, and game routers each define their
    own ``_facade(subject)`` helper that builds a real
    ``Facade``. The mock ignores the subject and returns the same
    shared facade for the whole test, so per-request state is
    preserved within a test and isolated between tests.
    """
    from genesis.api.routers import contacts as _contacts_router
    from genesis.api.routers import game as _game_router
    from genesis.api.routers import profile as _profile_router

    state: dict[str, object] = {"mock": _build_mock_facade()}

    def fake_facade(subject: str) -> object:
        return state["mock"]

    monkeypatch.setattr(_productivity_router, "_facade", fake_facade)
    monkeypatch.setattr(_game_router, "_facade", fake_facade)
    monkeypatch.setattr(_profile_router, "_facade", fake_facade)
    monkeypatch.setattr(_contacts_router, "_facade", fake_facade)


def _build_mock_facade() -> object:
    class _MockConfigs:
        """Lightweight stand-in for ``genesis.configs.Configs`` so
        tests can flip ``game.enabled`` in place."""

        def __init__(self) -> None:
            from genesis.configs import GameConfig

            self.game = GameConfig(enabled=True)

    class _MockFacade:
        def __init__(self) -> None:
            self.caldav = MockCalDAVFacade()
            self.carddav = MockCardDAVFacade()
            self.journal = InMemoryJournalFacade()
            # Lazy wrapper so list/get_character routes don't blow up.
            # Not a full GameFacade — record_xp_event below writes
            # to the mock caldav directly for the same reason.
            self._game = None
            self._configs = None
            # Expose configs on the caldav too so tests that yield
            # ``mock.caldav`` can still toggle ``game.enabled``.
            self.caldav.__dict__["configs"] = self.configs

        @property
        def game(self):
            if self._game is None:
                from genesis.configs import GameConfig
                from genesis.game.facade import GameFacade

                self._game = GameFacade(self.caldav, GameConfig(enabled=True))  # type: ignore[arg-type]
            return self._game

        @property
        def configs(self):
            """Expose configs so tests can flip ``game.enabled``."""

            if self._configs is None:
                self._configs = _MockConfigs()
            return self._configs

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
            # Persist a minimal event into the mock so the API can
            # return 201 instead of "source not found". Mirrors the
            # validation ``Facade.record_xp_event`` does so the
            # router's 400-mapping fires correctly when callers omit
            # required fields.
            from datetime import UTC, datetime

            from genesis.entities import XpEventCreate, XpEventKind
            from genesis.game.facade import GameDisabled

            # Honour ``game.enabled`` like the real facade.
            if not self.configs.game.enabled:
                raise GameDisabled("game layer is disabled in configs.game.enabled")

            # Reason source requires an explicit idempotency_key;
            # the real ``Facade.record_xp_event`` raises ValueError
            # in that case. Replicate so the router maps to 400.
            if kwargs.get("reason") is not None and not kwargs.get("idempotency_key"):
                raise ValueError("reason source requires an explicit idempotency_key")

            stat_id = kwargs.get("stat_id") or "discipline"
            delta = int(kwargs.get("delta") or 5)
            session_id = kwargs.get("session_id")
            return self.caldav.create_xp_event(
                XpEventCreate(
                    stat_id=stat_id,
                    delta=delta,
                    kind=XpEventKind.GAIN if delta >= 0 else XpEventKind.LOSS,
                    occurred_at=kwargs.get("occurred_at") or datetime.now(UTC),
                    idempotency_key=kwargs.get("idempotency_key") or "",
                    reason=kwargs.get("reason") or "mock",
                    source_session_id=session_id,
                )
            )

        def complete_task(self, uid):
            from genesis.entities import TaskStatus, TaskUpdate

            return self.caldav.update_task(uid, TaskUpdate(status=TaskStatus.COMPLETED))

        def stop_session(self, uid, *, actual_due, completed_at, category=None):
            updated = self.caldav.stop_time_log_entry(
                uid, actual_due=actual_due, completed_at=completed_at, category=category
            )
            if updated is not None:
                self._fire_xp_safely(
                    self.record_xp_event,
                    session_id=uid,
                    occurred_at=completed_at,
                )
            return updated

        def start_session(self, *, label, mode, duration_minutes=None, category=None, task_id=None, description=None):
            from datetime import timedelta as _td

            from genesis.caldav.ical import now_utc
            from genesis.entities import TimeLogEntryCreate, TimeLogMode, TimeLogStatus

            now = now_utc()
            mode_enum = TimeLogMode(mode)
            due = None
            if mode_enum == TimeLogMode.COUNTDOWN:
                if duration_minutes is None:
                    raise ValueError("duration_minutes is required for countdown mode")
                due = now + _td(minutes=duration_minutes)
            from genesis.category.enums import TimeLogCategory

            return self.caldav.create_time_log_entry(
                TimeLogEntryCreate(
                    label=label,
                    start=now,
                    due=due,
                    mode=mode_enum,
                    status=TimeLogStatus.IN_PROGRESS,
                    task_id=task_id,
                    category=TimeLogCategory(category) if category is not None else TimeLogCategory.OTHER,
                    description=description,
                )
            )

        def lodge_completed_session(
            self, *, label, start, duration_minutes, category=None, task_id=None, description=None
        ):
            from datetime import timedelta as _td

            from genesis.category.enums import TimeLogCategory
            from genesis.entities import TimeLogEntryCreate, TimeLogMode, TimeLogStatus
            from genesis.tz import to_utc

            start_utc = to_utc(start)
            end_utc = start_utc + _td(minutes=duration_minutes)
            entry = self.caldav.create_time_log_entry(
                TimeLogEntryCreate(
                    label=label,
                    start=start_utc,
                    due=end_utc,
                    completed_at=end_utc,
                    mode=TimeLogMode.COUNTDOWN,
                    status=TimeLogStatus.COMPLETED,
                    task_id=task_id,
                    category=TimeLogCategory(category) if category is not None else TimeLogCategory.OTHER,
                    description=description,
                )
            )
            self._fire_xp_safely(self.record_xp_event, session_id=entry.uid, occurred_at=end_utc)
            return entry

        def discard_session(self, uid):
            return self.caldav.delete_time_log_entry(uid)

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
    from genesis.api.routers import contacts as _contacts_router
    from genesis.api.routers import game as _game_router
    from genesis.api.routers import profile as _profile_router

    store_path = tmp_path / "users.yaml"
    _seed_default_user(store_path)
    monkeypatch.setattr("genesis.users.store._default_store", UserStore(store_path))
    mock = _build_mock_facade()
    state: dict[str, object] = {"mock": mock}

    def fake_facade(subject: str) -> object:
        return state["mock"]

    monkeypatch.setattr(_productivity_router, "_facade", fake_facade)
    monkeypatch.setattr(_game_router, "_facade", fake_facade)
    monkeypatch.setattr(_profile_router, "_facade", fake_facade)
    monkeypatch.setattr(_contacts_router, "_facade", fake_facade)
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
