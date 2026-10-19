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
