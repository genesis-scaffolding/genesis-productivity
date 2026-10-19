# Container image for the genesis productivity system.
#
# Runs exactly what `make serve` runs on the host: the Streamlit UI on
# :3000 and the FastAPI server on :8765, supervised by tini so SIGTERM
# reaches both children. The image runs as root, so the app's default XDG
# path resolution resolves to /root/.config, /root/.local/share,
# /root/.local/state and /root/.cache. The declarative service mounts the
# worker's per-service pre-prepared directories onto exactly those paths,
# so no XDG redirection happens here -- the app finds its roots the way it
# always has.

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

ENV GENESIS_UI_HOST=0.0.0.0 \
    GENESIS_UI_PORT=3000 \
    GENESIS_API_HOST=0.0.0.0 \
    GENESIS_API_PORT=8765

EXPOSE 3000 8765

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -fsS http://127.0.0.1:8765/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["make", "serve"]
