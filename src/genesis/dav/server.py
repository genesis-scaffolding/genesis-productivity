"""Radicale server management.

Handles starting, stopping, and checking the status of the Radicale server.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
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
    ) -> None:
        """Initialize the server manager.

        Args:
            dav_config: DAV configuration with host, port, username, password.
            data_path: Root path for Radicale data storage.
            startup_timeout: Maximum seconds to wait for server to be ready.
        """
        self._config = dav_config
        self._data_path = Path(data_path)
        self._startup_timeout = startup_timeout
        self._process: subprocess.Popen[bytes] | None = None

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

        # Create htpasswd file with the configured user
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

    def start(self) -> None:
        """Start the server if not already running.

        Blocks until the server is ready to accept connections.

        Raises:
            TimeoutError: If server does not become ready within timeout.
            RuntimeError: If radicale executable is not found.
        """
        if self.is_running():
            return  # Already running

        # Ensure data directory exists
        self._data_path.mkdir(parents=True, exist_ok=True)

        # Write config files
        self._write_config()

        # Find radicale executable
        radicale = find_radicale_binary()

        # Start radicale as subprocess
        self._process = subprocess.Popen(  # type: ignore[assignment]
            [
                sys.executable,
                str(radicale),
                "-C", str(self._data_path / "config"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for server to be ready
        self._wait_for_ready()

    def _wait_for_ready(self) -> None:
        """Wait for server to become ready.

        Raises:
            TimeoutError: If server does not become ready within timeout.
        """
        start = time.monotonic()
        while time.monotonic() - start < self._startup_timeout:
            if self.is_running():
                return
            time.sleep(POLL_INTERVAL)

        # If we started the process, check if it died
        if self._process is not None and self._process.poll() is not None:
            stdout, stderr = self._process.communicate()
            raise RuntimeError(
                f"Radicale failed to start:\nstdout: {stdout.decode()}\nstderr: {stderr.decode()}"
            )

        raise TimeoutError(
            f"Server did not become ready within {self._startup_timeout} seconds"
        )

    def stop(self) -> None:
        """Stop the server if it was started by this manager.

        Does nothing if the server was not started by this instance.
        """
        if self._process is None:
            return  # Not started by us

        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None

    def __enter__(self) -> ServerManager:
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()
