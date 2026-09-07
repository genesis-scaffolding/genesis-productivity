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
    from .app import app

    host = os.environ.get("GENESIS_API_HOST", "0.0.0.0")
    port = int(os.environ.get("GENESIS_API_PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
