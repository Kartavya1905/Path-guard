"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from app.api import router
from app.config import get_settings
from app.db import database_ready

MAX_BODY_BYTES = 2_000_000


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PathGuard API", version="0.1.0")

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        """Reject oversized request bodies before they are read (2 MB is ample for a map)."""
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> JSONResponse:
        """Readiness: 200 only when the database answers."""
        db_ok = database_ready()
        body = {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "unavailable",
            "env": settings.env,
            "identity": settings.identity_mode,
            "visitor_retention_days": settings.visitor_retention_days
            if settings.visitor_identity_enabled else None,
            "external_processing": settings.external_processing_enabled,
        }
        return JSONResponse(body, status_code=200 if db_ok else 503)

    @app.exception_handler(IntegrityError)
    def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        """The transaction was rolled back; nothing from this request was saved."""
        return JSONResponse({"detail": {
            "code": "integrity_error",
            "message": "The change conflicts with existing data and was not saved.",
            "constraint": getattr(getattr(exc.orig, "diag", None), "constraint_name", None),
        }}, status_code=409)

    app.include_router(router)
    # Single-container hosting: serve the built web app after the API routes.
    if settings.static_dir and Path(settings.static_dir).is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
    return app


app = create_app()
