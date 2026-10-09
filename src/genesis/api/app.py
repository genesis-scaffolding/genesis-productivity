"""FastAPI application for the agent-facing productivity API.

Wires up CORS, the auth router, and the productivity router under a
single ``app`` instance. ``genesis.api.cli`` runs this with
``uvicorn``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import router as auth_router
from .routers.contacts import router as contacts_router
from .routers.game import router as game_router
from .routers.productivity import router as productivity_router
from .routers.profile import router as profile_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Genesis Productivity API",
        description=(
            "REST API for the genesis productivity system. Authentication: OAuth2 password grant with bearer tokens."
        ),
        version="0.1.0",
    )

    # Loopback-only CORS in v1 — the Streamlit frontend talks to
    # Facade directly, so this only matters for the agent extension.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost",
            "http://127.0.0.1",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8765",
            "http://127.0.0.1:8765",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "online"}

    app.include_router(auth_router)
    app.include_router(productivity_router)
    app.include_router(profile_router)
    app.include_router(game_router)
    app.include_router(contacts_router)

    return app


app = create_app()
