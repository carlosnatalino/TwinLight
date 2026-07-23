"""FastAPI application factory.

Creates the FastAPI app, attaches TapiContext to app.state,
and includes all API routers.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from twinlight.api import (
    admin,
    common,
    config as config_api,
    connectivity,
    diagrams,
    equipment,
    internal,
    metrics as metrics_api,
    path_computation,
    photonic_media,
    topology,
)
from twinlight.api.middleware import (
    RESTCONF_MEDIA_TYPE,
    PathDecodeMiddleware,
    RestconfContentTypeMiddleware,
)
from twinlight.config import TwinConfig
from twinlight.state.context import TapiContext


def _restconf_error_response(
    status_code: int,
    error_tag: str,
    error_message: str,
    error_path: str | None = None,
) -> JSONResponse:
    """Build a JSONResponse with ietf-restconf:errors format (RFC 8040)."""
    err: dict = {
        "error-type": "application",
        "error-tag": error_tag,
        "error-message": error_message,
    }
    if error_path:
        err["error-path"] = error_path
    body = {"ietf-restconf:errors": {"error": [err]}}
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=RESTCONF_MEDIA_TYPE,
    )


def _http_status_to_error_tag(status_code: int) -> str:
    """Map HTTP status to RESTCONF error-tag (RFC 8040)."""
    if status_code == 404:
        return "data-missing"
    if status_code == 409:
        return "resource-denied"
    if status_code == 400:
        return "bad-element"
    if status_code == 422:
        return "invalid-value"
    return "operation-failed"


def create_app(config: TwinConfig) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="TwinLight",
        version="0.1.0",
        description="Optical network digital twin with T-API interfaces",
    )

    # Middleware (order matters: last added = first executed)
    # PathDecodeMiddleware is added first so it runs innermost (just before
    # the router), decoding %3A-encoded colons in RESTCONF paths.
    app.add_middleware(PathDecodeMiddleware)
    app.add_middleware(RestconfContentTypeMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # State — TapiContext loads topology and builds TAPI models
    app.state.context = TapiContext(config)
    app.state.config = config

    # Routers
    app.include_router(common.router)
    app.include_router(topology.router)
    app.include_router(connectivity.router)
    app.include_router(path_computation.router)
    app.include_router(equipment.router)
    app.include_router(photonic_media.router)
    app.include_router(internal.router)
    app.include_router(diagrams.router)
    app.include_router(admin.router)
    app.include_router(config_api.router)
    app.include_router(metrics_api.router)

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok"}

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        tag = _http_status_to_error_tag(exc.status_code)
        return _restconf_error_response(exc.status_code, tag, msg)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        msg = "; ".join(
            f"{e['loc']}: {e['msg']}" for e in exc.errors()
        ) if exc.errors() else str(exc)
        return _restconf_error_response(422, "invalid-value", msg)

    return app
