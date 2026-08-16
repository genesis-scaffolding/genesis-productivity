# 009. Radicale CalDAV Server for Task and Project Storage

## Context

ADR-008 chose direct .ics file storage. The implementation revealed friction with the icalendar library:
- Manual serialization/deserialization with type quirks
- Custom X-property handling inconsistencies
- File globbing for queries instead of proper CalDAV REPORT queries

We need a cleaner approach that preserves CalDAV compatibility.

## Decision

Use Radicale as a local CalDAV server with the caldav library as client.

### Architecture

**Separation of concerns:**
- Server lifecycle is managed separately from application lifecycle
- Multiple genesis processes (CLI, Streamlit) share a single Radicale instance
- Facade connects to server but does not own its lifecycle

```
┌─────────────────────────────────────────────────┐
│  Radicale Server (lifecycle managed separately) │
│  - Running as long-running process              │
│  - Or started/stopped via genesis CLI           │
└─────────────────────────────────────────────────┘
         ▲                           ▲
         │                           │
┌────────┴────────┐         ┌────────┴────────┐
│  genesis server │         │  Facade          │
│  (start/stop)   │         │  (connects)      │
└─────────────────┘         └──────────────────┘
```

### Server Lifecycle

Server can run independently of any genesis process.

**CLI commands:**
```bash
genesis server start   # Start server, block until ready
genesis server stop    # Stop server
genesis server status  # Check if running
genesis server restart # Stop and start
```

**ServerManager class** (`src/genesis/dav/server.py`):
```python
class ServerManager:
    def __init__(self, config: DAVConfig):
        self._config = config

    def start(self) -> None:
        """Start server if not running. Block until ready."""
        if self.is_running():
            return  # Already running
        self._start_radicale()
        self._wait_for_ready()  # Poll until ready

    def stop(self) -> None:
        """Stop server."""
        self._radicale.terminate()

    def is_running(self) -> bool:
        """Check if server is reachable."""
        return _is_server_running(self._config.port)

    def _wait_for_ready(self, timeout=10) -> None:
        """Poll until server accepts connections."""
        start = time.time()
        while time.time() - start < timeout:
            if _is_server_running(self._config.port):
                return
            time.sleep(0.5)
        raise TimeoutError("Server did not start in time")
```

**Server check:**
```python
import socket

def _is_server_running(host="127.0.0.1", port=5232) -> bool:
    try:
        sock = socket.socket()
        sock.settimeout(1)
        sock.connect((host, port))
        sock.close()
        return True
    except:
        return False
```

### Startup Behavior

When Facade initializes, it starts the server if not running:

```python
class Facade:
    def __init__(self, yaml_path=None):
        self._configs = Configs(yaml_path=yaml_path)
        self._server = ServerManager(self._configs.dav)
        self._server.start()  # Start if not running, wait until ready
        self._caldav = CalDAVFacade(...)
        self._journal = JournalFacade(...)

    def shutdown(self):
        """Stop server if we started it."""
        self._server.stop()
```

### Authentication

Shared htpasswd auth for local-only server.

**Default password:**
```python
DEFAULT_RADICALE_PASSWORD = "genesis-local"
```

**Config:**
```yaml
dav:
  host: "127.0.0.1"
  port: 5232
  username: "genesis"
  password: "genesis-local"  # Override via env var or config
```

**Setup (one-time):**
```bash
htpasswd -B ~/.config/radicale/users genesis
```

### Storage Layout

```
<data>/radicale/
  collections/
    <uuid>-tasks/
      .RadicaleCalendar
    <uuid>-projects/
      .RadicaleCalendar
```

### Client

Use the caldav library:

```python
from caldav import CalDAVClient

client = CalDAVClient(
    f"http://{host}:{port}",
    username=self._config.username,
    password=self._config.password,
)

# Get or create calendars
tasks_cal = client.calendar(name="tasks")
projects_cal = client.calendar(name="projects")
```

### Data Model (unchanged from ADR-008)

Task and Project entities remain the same. Only storage implementation changes.

### API (unchanged from ADR-008)

Facade operations remain the same:
- `get_task(uid)`, `list_tasks(...)`, `create_task(...)`, `update_task(...)`, `delete_task(uid)`
- `get_project(uid)`, `list_projects(...)`, `create_project(...)`, `update_project(...)`, `delete_project(uid)`

## Status

Proposed

## Consequences

- Server lifecycle separated from application lifecycle
- Server can run as long-running process
- Ready check ensures server accepts connections before use
- Shared server: multiple genesis processes can run concurrently
- ServerManager handles start/stop/status
- Same API and data model as ADR-008

## Relationship

Supersedes ADR-008. ADR-001, ADR-005, ADR-006, ADR-007 remain valid.
