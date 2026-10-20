"""Deployment entry point: the API plus the built React app, served from one origin.

    uvicorn app.api.serve_spa:create_render_app --factory

The client calls relative /api/... only (frontend/vite.config.ts), so one service that serves both needs no CORS
and keeps the session cookie first-party (SameSite=Lax, Secure over HTTPS). The API routes are registered by
create_app() first; the static files are mounted last at "/", so they never shadow an /api route.

FRONTEND_DIST_DIR (the `vite build` output) turns the static part on; without it this is the plain API.
The client-side routes (/employee, /rep) fall back to index.html so a refresh or a shared link works.
No business logic here: create_app() is the composition root and is not changed.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine
from starlette.exceptions import HTTPException   # what StaticFiles raises (FastAPI's subclass would not match)

from app.modules.conversation.public import LLMChain
from app.shared.settings import Settings

from .main import create_app


class SPAStaticFiles(StaticFiles):
    """StaticFiles that answers an unknown path with index.html (client-side routing), except /api: an unknown
    API path stays a 404, never an HTML page."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        if path.replace("\\", "/").split("/", 1)[0] == "api":       # (StaticFiles uses OS separators)
            raise HTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


def mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    if not (dist_dir / "index.html").is_file():
        raise RuntimeError(f"FRONTEND_DIST_DIR has no index.html: {dist_dir}")
    app.mount("/", SPAStaticFiles(directory=dist_dir, html=True), name="frontend")


def create_render_app(settings: Optional[Settings] = None, *, engine: Optional[Engine] = None,
                      llm: Optional[LLMChain] = None, dist_dir: Optional[Path] = None) -> FastAPI:
    app = create_app(settings, engine=engine, llm=llm)
    dist = dist_dir or (Path(os.environ["FRONTEND_DIST_DIR"]) if os.environ.get("FRONTEND_DIST_DIR") else None)
    if dist is not None:
        mount_frontend(app, dist)
    return app
