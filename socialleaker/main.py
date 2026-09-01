"""FastAPI application factory and entrypoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .database import init_db
from .routers import auth, integrations, pages, results, tasks

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Self-hosted social-media OSINT & collection panel with a "
                    "Claude Code ACP agent bridge.",
    )

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        from .services import queue_manager
        queue_manager.start_worker()

    # Static assets
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # API routers
    app.include_router(auth.router)
    app.include_router(tasks.router)
    app.include_router(integrations.router)
    app.include_router(results.router)

    # HTML pages (mounted last so /static and /api win)
    app.include_router(pages.router)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "app": settings.app_name, "version": __version__}

    return app


app = create_app()
