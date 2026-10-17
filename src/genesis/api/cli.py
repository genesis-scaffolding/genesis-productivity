"""Console-script entry point for the agent API.

Usage:
    genesis-api          # bind 0.0.0.0:8765
    GENESIS_API_PORT=9000 genesis-api

Honors ``GENESIS_API_HOST`` and ``GENESIS_API_PORT``.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> int:
    host = os.environ.get("GENESIS_API_HOST", "0.0.0.0")
    port = int(os.environ.get("GENESIS_API_PORT", "8765"))
    # Two workers so a parallel fan-out of CLI queries (e.g. `genesis
    # overview` issuing ~6-8 task queries concurrently) doesn't serialize
    # behind a single worker. Each worker keeps its own per-subject
    # facade cache (see api.routers.productivity._facade_cache); the
    # CalDAV discovery cost is paid once per subject per worker.
    workers = int(os.environ.get("GENESIS_API_WORKERS", "2"))
    # With workers > 1, uvicorn spawns child processes that re-import the
    # app — so it must be an import string, not an app object.
    uvicorn.run(
        "genesis.api.app:app",
        host=host,
        port=port,
        log_level="info",
        workers=workers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
