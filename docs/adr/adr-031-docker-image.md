# 031. Docker image for the productivity system

## Context

Forces at play. The system currently runs by cloning the repo and
`make serve` (uv + streamlit on :3000 + genesis-api on :8765). That
manual install path is repeated on every new machine, and the
genesis-agent-backend worker needs a container image it can pull and
mount its managed state directory into. The data model (ADR-019 shared
Radicale root, per-user XDG paths) already resolves every root through
XDG environment variables, which makes a single-mount container
footprint possible without code changes.

## Decision

We will ship an official Docker image built by GitHub Actions. The
image is python:3.12-slim + uv + make + tini; it installs the project
from the lockfile and runs `make serve`, so the container runs exactly
what the host runs. All four XDG roots (config, data, state, cache)
are pointed at a single /genesis-data directory via XDG_*_HOME
environment variables, so the entire persistent footprint is one
mountable path. The Streamlit UI (:3000) and the API (:8765) are both
exposed; the API's /health endpoint is the container health probe.

CI builds on master push, on tag push, and on manual dispatch.
Master builds tag the image `latest` and `sha-<short>` (per commit, so
any master commit is individually downloadable); tag builds add a
versioned tag matching the tag name. Both amd64 and arm64 are built
on native runners and combined into multi-arch manifests. Images are
pushed to GHCR.

## Status

Accepted

## Consequences

- A machine installs the system with `docker pull` + bind mounts, no
  clone, no uv, no Python toolchain.
- The four per-service mounts map the worker's pre-prepared directory
  layout 1:1 onto the app's data model with no in-image XDG
  redirection, matching every other declarative service in the fleet.
- The image bakes in the `make serve` supervision model (tini + GNU
  Make -j 2). Replacing make would require reworking the image's
  entrypoint, but that is the app's existing runtime, not a new one.
- Builds depend on GHCR package permissions (GITHUB_TOKEN write
  access), which the repository's workflow permissions grant.
