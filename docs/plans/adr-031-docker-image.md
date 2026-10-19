# Plan for adr-031

## Steps

1. Add `Dockerfile` (python:3.12-slim + uv + make + tini; `uv sync --frozen`;
   XDG roots redirected to /genesis-data; `make serve`; EXPOSE 3000 8765;
   healthcheck on :8765/health).
2. Add `.dockerignore` (git, venv, caches, tests, docs).
3. Add `.github/workflows/docker-image.yml` (per-arch build + GHCR push +
   multi-arch manifest; tags per ADR).
4. Add `docs/adr/adr-031-docker-image.md`.
5. Build the image locally and smoke-test it against a throwaway data
   dir (API /health 200, UI 200, XDG roots created under /genesis-data).
6. (rev 2, non-root) Replace root runtime with a drop-privilege entrypoint:
   `docker-entrypoint.sh` chowns the four mounted XDG dirs (and the
   editable egg-info) to PUID/PGID, then `exec setpriv` drops to that
   user; image bakes `UV_NO_SYNC=1` so `uv run` never re-syncs the
   root-owned venv. HOME=/home/genesis. Verified: app processes run as
   uid 1000, API/UI 200.
