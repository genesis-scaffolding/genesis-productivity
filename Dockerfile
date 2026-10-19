# Container image for the genesis productivity system.
#
# Runs exactly what `make serve` runs on the host: the Streamlit UI on
# :3000 and the FastAPI server on :8765, supervised by tini so SIGTERM
# reaches both children. The container starts as root only long enough
# for the entrypoint to normalize ownership of the mounted XDG
# directories to the host user (PUID/PGID, framework convention -- see
# the linuxserver images comfyui uses the same way), then drops
# privileges via setpriv and runs the app as that user. Ports 3000 and
# 8765 are above 1024, so no root privileges are needed to bind them.
#
# The user's home is /home/genesis; the declarative service mounts the
# worker's per-service pre-prepared directories onto the user's XDG
# roots, so the app's default path resolution finds them without any
# XDG redirection.

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends make tini curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app

# Lockfile + project metadata first for layer caching; the source second.
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY Makefile ./
COPY scripts ./scripts
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Default runtime identity (PUID/PGID default to this); the entrypoint
# re-chowns and re-drops to whatever PUID/PGID the service passes.
RUN useradd -u 1000 -m -d /home/genesis genesis

# The venv is frozen at build time; UV_NO_SYNC keeps `uv run` from
# attempting a re-sync at startup, which would fail as non-root on the
# root-owned /app/.venv (and add latency for no benefit).
ENV UV_NO_SYNC=1 \
    HOME=/home/genesis \
    GENESIS_UI_HOST=0.0.0.0 \
    GENESIS_UI_PORT=3000 \
    GENESIS_API_HOST=0.0.0.0 \
    GENESIS_API_PORT=8765

EXPOSE 3000 8765

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -fsS http://127.0.0.1:8765/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]
CMD ["make", "serve"]
