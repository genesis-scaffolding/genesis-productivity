"""Radicale server management.

Handles starting, stopping, and checking the status of the Radicale server.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genesis.configs import DAV


# How long to wait for server to become ready after starting
DEFAULT_STARTUP_TIMEOUT = 10  # seconds

# How often to poll for server readiness
POLL_INTERVAL = 0.25  # seconds


def is_server_running(host: str, port: int) -> bool:
    """Check if a server is reachable at the given host and port.

    Args:
        host: Server hostname or IP.
        port: Server port.

    Returns:
        True if server is reachable, False otherwise.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except (TimeoutError, OSError):
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def find_radicale_binary() -> Path:
    """Find the radicale executable.

    Returns:
        Path to radicale executable.

    Raises:
        FileNotFoundError: If radicale is not installed.
    """
    # Try using sys.executable to find radicale in the same environment
    radicale_path = Path(sys.executable).parent / "radicale"
    if radicale_path.exists():
        return radicale_path

    # Fall back to shutil.which
    found = shutil.which("radicale")
    if found:
        return Path(found)

    raise FileNotFoundError(
        "Radicale executable not found. Install with: pip install radicale"
    )


class ServerManager:
    """Manages the lifecycle of the Radicale server.

    Can start, stop, and check the status of the server.
    Server runs as a subprocess and persists until explicitly stopped.
    """

    def __init__(
        self,
        dav_config: DAV,
        data_path: Path,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        log_buffer_size: int = 5000,
    ) -> None:
        """Initialize the server manager.

        Args:
            dav_config: DAV configuration with host, port, username, password.
            data_path: Root path for Radicale data storage.
            startup_timeout: Maximum seconds to wait for server to be ready.
            log_buffer_size: Maximum number of recent log lines kept in memory for diagnostics.
        """
        self._config = dav_config
        self._data_path = Path(data_path)
        self._startup_timeout = startup_timeout
        self._process: subprocess.Popen[str] | None = None
        self._log_buffer: deque[str] = deque(maxlen=log_buffer_size)
        self._reader_thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        """Server host."""
        return self._config.host

    @property
    def port(self) -> int:
        """Server port."""
        return self._config.port

    def is_running(self) -> bool:
        """Check if the server is currently running.

        Returns:
            True if server is reachable, False otherwise.
        """
        return is_server_running(self._config.host, self._config.port)

    def _write_config(self) -> None:
        """Write Radicale config file with authentication settings."""
        config_path = self._data_path / "config"
        htpasswd_path = self._data_path / "users"
        collections_path = self._data_path / "collections"

        # Create initial htpasswd file with the configured user
        # ensure_user() will add more users later
        with open(htpasswd_path, "w") as f:
            f.write(f"{self._config.username}:{self._config.password}\n")

        # Write config file
        config_content = f"""[server]
hosts = {self._config.host}:{self._config.port}

[storage]
type = multifilesystem
filesystem_folder = {str(collections_path)}

[auth]
type = htpasswd
htpasswd_filename = {str(htpasswd_path)}
htpasswd_encryption = plain

[rights]
type = from_file
file = {str(self._data_path / "rights")}
"""

        # Write rights file allowing all access
        rights_content = """[base]
user = .+
collection = .*
permissions = RrWw
"""

        with open(config_path, "w") as f:
            f.write(config_content)

        with open(self._data_path / "rights", "w") as f:
            f.write(rights_content)

    def ensure_user(self, username: str) -> None:
        """Ensure user exists in Radicale htpasswd file.

        If the user already exists, does nothing.
        If the user does not exist, adds them with the default password.

        Args:
            username: The username to ensure exists.
        """
        htpasswd_path = self._data_path / "users"

        # Read existing users
        existing_users: set[str] = set()
        if htpasswd_path.exists():
            with open(htpasswd_path) as f:
                for line in f:
                    line = line.strip()
                    if ":" in line:
                        existing_users.add(line.split(":")[0])

        # Add user if not present
        if username not in existing_users:
            # Ensure parent directory exists
            htpasswd_path.parent.mkdir(parents=True, exist_ok=True)
            with open(htpasswd_path, "a") as f:
                f.write(f"{username}:{self._config.password}\n")

    def start(self) -> None:
        """Start the server if not already running.

        Blocks until the server is ready to accept connections.

        Raises:
            TimeoutError: If server does not become ready within timeout.
            RuntimeError: If radicale executable is not found, or it exited during startup.
        """
        if self.is_running():
            return  # Already running

        try:
            # Ensure data directory exists
            self._data_path.mkdir(parents=True, exist_ok=True)

            # Write config files (includes initial htpasswd with default user)
            self._write_config()

            # Find radicale executable
            radicale = find_radicale_binary()

            # Start radicale. stderr is merged into stdout so a single drain thread
            # handles both streams. text=True gives str lines; bufsize=1 line-buffers
            # so the drain thread receives complete lines as soon as they arrive.
            #
            # We deliberately avoid subprocess.PIPE-without-drain, which is the bug
            # that caused the previous deadlock: a pipe has a fixed 64KB kernel buffer,
            # and once it fills, every thread that tries to log via stderr blocks on
            # the logging lock. The drain thread below consumes continuously, so the
            # pipe never has a chance to back up.
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    str(radicale),
                    "-C", str(self._data_path / "config"),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # Background drain into an in-memory ring buffer (no disk, no deadlock).
            self._start_log_drain()

            # Wait for server to be ready
            self._wait_for_ready()

            # Ensure the configured user exists in htpasswd
            self.ensure_user(self._config.username)
        except BaseException:
            # Any failure path (TimeoutError, RuntimeError, KeyboardInterrupt, etc.)
            # must terminate the subprocess. Otherwise a half-started radicale leaks
            # holding port 5232, and the next start() call hits "Address already in use"
            # and bails out via is_running().
            self._terminate_and_cleanup()
            raise

    def _start_log_drain(self) -> None:
        """Spawn a daemon thread that drains the subprocess stdout into the log buffer.

        The thread reads one line at a time and appends to a bounded deque. Writes
        to a deque are O(1) and never block under normal conditions, so this fully
        replaces the diskless-but-blockable PIPE pattern.
        """
        proc = self._process
        if proc is None or proc.stdout is None:
            return

        def _drain() -> None:
            try:
                stream = proc.stdout
                assert stream is not None  # for type checkers
                for line in stream:
                    self._log_buffer.append(line)
            except (ValueError, OSError):
                # Stream closed because the subprocess exited; that's expected.
                pass
            except Exception:
                # Anything else is a bug, but the thread must not propagate it.
                pass

        self._reader_thread = threading.Thread(
            target=_drain,
            name="radicale-log-drain",
            daemon=True,
        )
        self._reader_thread.start()

    def recent_logs(self, n: int = 200) -> str:
        """Return the last n lines of captured subprocess output.

        Useful for surfacing diagnostics in the UI when something goes wrong.
        Returns an empty string if no output has been captured or n <= 0.

        Args:
            n: Maximum number of recent lines to return.

        Returns:
            Concatenated log lines, no trailing newline.
        """
        if n <= 0:
            return ""
        # Snapshot via list() so a concurrent append during iteration can't corrupt
        # the result. The snapshot may include a partial final line; that's fine for
        # debug display.
        snapshot = list(self._log_buffer)
        return "".join(snapshot[-n:])

    def _terminate_and_cleanup(self) -> None:
        """Terminate the subprocess, force-killing if needed, and clear state.

        Best-effort: never raises. Use from both stop() and the start() failure
        path so the subprocess is always reaped.
        """
        proc = self._process
        if proc is None:
            return
        self._process = None
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception:
            # Don't mask whatever called us.
            pass

    def _wait_for_ready(self) -> None:
        """Wait for server to become ready.

        Raises:
            TimeoutError: If server does not become ready within timeout (with recent
                log lines appended for diagnostics).
            RuntimeError: If radicale process exited during startup (with recent log
                lines appended for diagnostics).
        """
        start = time.monotonic()
        while time.monotonic() - start < self._startup_timeout:
            if self.is_running():
                return
            time.sleep(POLL_INTERVAL)

        # Build a tail of recent log output to embed in the error message.
        recent = self.recent_logs(50).strip()
        tail = f"\n\nRecent log output:\n{recent}" if recent else ""

        # If the process exited, surface its return code.
        if self._process is not None and self._process.poll() is not None:
            rc = self._process.returncode
            raise RuntimeError(
                f"Radicale failed to start (exit code {rc}).{tail}"
            )

        raise TimeoutError(
            f"Server did not become ready within {self._startup_timeout} seconds.{tail}"
        )

    def stop(self) -> None:
        """Stop the server if it was started by this manager.

        Does nothing if the server was not started by this instance.
        """
        if self._process is None:
            return  # Not started by us
        self._terminate_and_cleanup()

    def __enter__(self) -> ServerManager:
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()
