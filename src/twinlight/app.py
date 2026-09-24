"""FastAPI application factory.

Creates the FastAPI app, attaches TapiContext to app.state,
and includes all API routers.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from twinlight.api import (
    admin,
    common,
    connectivity,
    diagrams,
    equipment,
    internal,
    path_computation,
    topology,
)
from twinlight.api import (
    config as config_api,
)
from twinlight.api import (
    metrics as metrics_api,
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


# RFC 8040 §3.3 requires the root resource to expose the YANG library
# version, which is the revision date of ietf-yang-library (RFC 8525).
_YANG_LIBRARY_VERSION = "2019-01-04"


def _add_restconf_root_resources(app: FastAPI, root: str) -> None:
    """Serve the RFC 8040 root resource and its discovery document.

    RFC 8040 §3.1 has a client discover the API root from
    ``/.well-known/host-meta`` rather than assuming a path, so a client
    that knows only the host can find the twin. The conventional root is
    ``/restconf``, which is what ONOS and most RESTCONF clients hard-code.
    """

    @app.get("/.well-known/host-meta", include_in_schema=False)
    async def host_meta() -> Response:
        # XRD, per RFC 6415 — deliberately XML: RFC 8040 §3.1 specifies
        # this document's format, and a JSON variant would not be found
        # by a conforming client.
        xrd = (
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<XRD xmlns='http://docs.oasis-open.org/ns/xri/xrd-1.0'>\n"
            f"    <Link rel='restconf' href='{root}'/>\n"
            "</XRD>\n"
        )
        return Response(content=xrd, media_type="application/xrd+xml")

    @app.get(root, tags=["restconf"])
    async def restconf_root() -> dict:
        return {
            "ietf-restconf:restconf": {
                "data": {},
                "operations": {},
                "yang-library-version": _YANG_LIBRARY_VERSION,
            }
        }

    @app.get(f"{root}/yang-library-version", tags=["restconf"])
    async def yang_library_version() -> dict:
        return {"ietf-restconf:yang-library-version": _YANG_LIBRARY_VERSION}


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
    app.add_middleware(
        RestconfContentTypeMiddleware,
        restconf_root=config.server.restconf_root,
    )
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
    # The T-API modules are mounted twice: under the RESTCONF root
    # (RFC 8040 §3.1, the canonical location) and at the bare /data/ they
    # have always been served from. The bare mount is hidden from OpenAPI
    # so the documented surface shows one path per resource; it exists for
    # clients written against earlier releases, and docs/PENDING.md tracks
    # retiring it.
    #
    # There is no photonic-media router: the twin's photonic surface is the
    # augments on the SIP and the connectivity-service end-point, served by
    # common.router and connectivity.router. The grid parameters that used
    # to be published here as tapi-photonic-media:spectrum-context are not
    # T-API at all and now live at /internal/spectrum-context.
    root = config.server.restconf_root
    for tapi_router in (
        common.router,
        topology.router,
        connectivity.router,
        path_computation.router,
        equipment.router,
    ):
        if root:
            app.include_router(tapi_router, prefix=root)
        app.include_router(tapi_router, include_in_schema=not root)

    app.include_router(internal.router)
    app.include_router(diagrams.router)
    app.include_router(admin.router)
    app.include_router(config_api.router)
    app.include_router(metrics_api.router)

    if root:
        _add_restconf_root_resources(app, root)

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
