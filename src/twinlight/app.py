"""FastAPI application factory.

Creates the FastAPI app, attaches TapiContext to app.state,
and includes all API routers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from xml.sax.saxutils import escape

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from twinlight.api import (
    admin,
    auth,
    common,
    connectivity,
    diagrams,
    equipment,
    internal,
    oam,
    parameters,
    path_computation,
    restconf_resources,
    streaming,
    topology,
    transparent,
)
from twinlight.api import (
    config as config_api,
)
from twinlight.api import (
    metrics as metrics_api,
)
from twinlight.api.middleware import (
    RESTCONF_MEDIA_TYPE,
    AllowHeaderMiddleware,
    HeadMiddleware,
    OptionsMiddleware,
    PathDecodeMiddleware,
    RestconfHeadersMiddleware,
    RestconfRequestMiddleware,
    TokenAuthMiddleware,
)
from twinlight.api.restconf import (
    RestconfError,
    error_type,
    validation_error_tag,
)
from twinlight.api.restconf_resources import YANG_LIBRARY_REVISION
from twinlight.auth import TokenAuthenticator
from twinlight.config import TwinConfig
from twinlight.state.context import TapiContext
from twinlight.streaming.measurement_stream import MeasurementStream


def _restconf_error_response(
    status_code: int,
    error_tag: str,
    error_message: str,
    error_path: str | None = None,
    headers: Mapping[str, str] | None = None,
    error_app_tag: str | None = None,
) -> JSONResponse:
    """Build a JSONResponse with ietf-restconf:errors format (RFC 8040)."""
    err: dict = {
        "error-type": error_type(error_tag),
        "error-tag": error_tag,
        "error-message": error_message,
    }
    if error_app_tag:
        err["error-app-tag"] = error_app_tag
    if error_path:
        err["error-path"] = error_path
    body = {"ietf-restconf:errors": {"error": [err]}}
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=RESTCONF_MEDIA_TYPE,
        headers=headers,
    )


def _http_status_to_error_tag(status_code: int) -> str:
    """Map an HTTP status to the RFC 8040 §7 error-tag of an error that names none.

    A 404 is ``invalid-value``: RFC 8040 §4.3, "If a retrieval request for
    a data resource represents an instance that does not exist, then an
    error response containing a '404 Not Found' status-line MUST be
    returned by the server. The error-tag value 'invalid-value' is used in
    this case"; §4.5, §4.6 and §4.7 let PUT, PATCH and DELETE answer the
    same 404 ("A server MAY return a '404 Not Found' status-line ... The
    error-tag value 'invalid-value' is used in this case"), which keeps one
    answer for a missing resource whatever the method. ``data-missing`` is
    paired with 409 only by the §7 table, and the IETF rejected re-pairing
    it with 404 (erratum 5565). The proprietary endpoints (``/internal``,
    ``/admin``, ``/config``, ``/transparent``) follow the same convention,
    so a client handles every error of the twin one way.
    """
    if status_code in (401, 403):
        return "access-denied"
    if status_code == 404:
        return "invalid-value"
    if status_code == 405:
        return "operation-not-supported"
    if status_code == 409:
        return "resource-denied"
    if status_code == 400:
        return "bad-element"
    if status_code == 501:
        return "operation-not-supported"
    return "operation-failed"


# RFC 8040 §3.3 requires the root resource to expose the YANG library
# version, which is the revision date of ietf-yang-library (RFC 8525), the
# revision restconf_resources serves.
_YANG_LIBRARY_VERSION = YANG_LIBRARY_REVISION


def restconf_href(root: str, request: Request) -> str:
    """The ``href`` of host-meta's ``restconf`` link for ``root``.

    A root is a path-absolute reference, ``/restconf`` (RFC 8040 §3.1's own
    example). An empty root cannot be written that way: XRD 1.0 §1.5.2 says
    every URI in an XRD "MUST consist of at least one non-whitespace
    character", and the candidates that would satisfy it as a path go
    wrong for a client that follows §3.1 ("use this value as the initial
    part of the path in the request URI"): ``/`` makes ``{+restconf}/data``
    the network-path reference ``//data`` (RFC 3986 §4.2), an authority
    called ``data``. So an empty root is advertised as the absolute URI the
    request reached, ``http://host:port``, whose path is empty (RFC 3986
    §3.3): resolved against host-meta (RFC 3986 §5.2) it is itself, and
    ``{+restconf}/data`` expands to ``http://host:port/data``.
    """
    return root or str(request.base_url).rstrip("/")


def _add_restconf_root_resources(app: FastAPI, root: str) -> None:
    """Serve the RFC 8040 root resource and its discovery document.

    RFC 8040 §3.1 has a client discover the API root from
    ``/.well-known/host-meta`` rather than assuming a path, so a client
    that knows only the host can find the twin. The conventional root is
    ``/restconf``, which is what ONOS and most RESTCONF clients hard-code.
    An empty root is as discoverable: host-meta names it (see
    :func:`restconf_href`), and the API resource ``{+restconf}`` is ``/``,
    since an empty path is ``/`` (RFC 9110 §4.2.3).
    """

    @app.get("/.well-known/host-meta", include_in_schema=False)
    async def host_meta(request: Request) -> Response:
        # XRD, per RFC 6415 — deliberately XML: RFC 8040 §3.1 specifies
        # this document's format, and a JSON variant would not be found
        # by a conforming client.
        href = escape(restconf_href(root, request), {"'": "&apos;"})
        xrd = (
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<XRD xmlns='http://docs.oasis-open.org/ns/xri/xrd-1.0'>\n"
            f"    <Link rel='restconf' href='{href}'/>\n"
            "</XRD>\n"
        )
        return Response(content=xrd, media_type="application/xrd+xml")

    @app.get(root or "/", tags=["restconf"])
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


_BEARER_SCHEME = "bearerAuth"


#: Paths served without a token when authentication is enabled. Only the
#: liveness probe: container healthchecks and load balancers call it without
#: credentials, and it reveals nothing but that the process is up. Everything
#: else — the RESTCONF root and host-meta, /metrics, the OpenAPI schema and
#: its viewers — needs a token. Exact matches only: see TokenAuthMiddleware.
PUBLIC_PATHS: frozenset[str] = frozenset({"/health"})


def _document_bearer_auth(app: FastAPI, public_paths: frozenset[str]) -> None:
    """Declare the bearer scheme in the OpenAPI schema.

    Enforcement is the middleware's job; this only makes the schema say what
    the middleware does, so generated clients and Swagger UI (loaded with a
    token, since the schema itself is protected) know to send one. Public
    paths are marked ``security: []`` so they are not shown locked.
    """
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = generate()  # also caches itself in app.openapi_schema
            components = schema.setdefault("components", {})
            components.setdefault("securitySchemes", {})[_BEARER_SCHEME] = {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "A token from TWINLIGHT_API_TOKENS. The same token "
                    "authenticates the gNMI interface."
                ),
            }
            schema["security"] = [{_BEARER_SCHEME: []}]
            for path in public_paths:
                for operation in schema.get("paths", {}).get(path, {}).values():
                    operation["security"] = []
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app(config: TwinConfig) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="TwinLight",
        version="0.2.0",
        description="Optical network digital twin with T-API interfaces",
    )

    # One authenticator for every interface: the gRPC server reads it from
    # app.state, so REST and gNMI accept exactly the same tokens. No tokens
    # configured means authentication is off: neither the HTTP middleware
    # nor the gRPC interceptor is installed, so no auth code runs per call.
    authenticator = TokenAuthenticator(config.api_tokens)

    # Middleware (order matters: last added = first executed)
    # PathDecodeMiddleware runs just before the router (only
    # AllowHeaderMiddleware is inside it), decoding %3A-encoded colons in
    # RESTCONF paths.
    # TokenAuthMiddleware, when installed, sits just inside CORS, so
    # preflights are answered without credentials and a 401 still carries
    # CORS headers. It is outside everything else, so a rejected request
    # never reaches routing or the other middleware.
    # AllowHeaderMiddleware is innermost of all: it rewrites the Allow header
    # of every 405 from the router's matching (RFC 9110 §15.5.6), so it must
    # see the decoded path and the exception handler's response.
    # OptionsMiddleware, just outside it and inside PathDecodeMiddleware and
    # the token check, answers OPTIONS on every route from the same method
    # set (RFC 8040 §4.1). HeadMiddleware, just inside CORS and outside the
    # token check, runs a HEAD as its GET and drops the body (RFC 8040
    # §4.2), so it is authenticated as the GET would be.
    # RestconfRequestMiddleware, inside the token check and path decoding,
    # refuses the query parameters, Accept and body media types RFC 8040
    # refuses (400, 406, 415); RestconfHeadersMiddleware, just inside CORS,
    # stamps Content-Type and Cache-Control on every RESTCONF response, a
    # 401 included.
    refusals = (transparent.refused_methods,)
    app.add_middleware(AllowHeaderMiddleware, refusals=refusals)
    app.add_middleware(
        OptionsMiddleware,
        restconf_root=config.server.restconf_root,
        refusals=refusals,
    )
    app.add_middleware(
        RestconfRequestMiddleware, restconf_root=config.server.restconf_root
    )
    app.add_middleware(PathDecodeMiddleware)
    if authenticator.enabled:
        app.add_middleware(
            TokenAuthMiddleware,
            authenticator=authenticator,
            public_paths=PUBLIC_PATHS,
        )
    app.add_middleware(HeadMiddleware)
    app.add_middleware(
        RestconfHeadersMiddleware, restconf_root=config.server.restconf_root
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
    app.state.authenticator = authenticator
    # The T-API measurement stream (TR-548 §6). Built here so REST discovery
    # can report its state; its sampler is started by main.py, beside the
    # gRPC server that delivers it.
    app.state.measurement_stream = MeasurementStream(app.state.context, config)

    # Routers
    # The T-API modules are mounted once, under the RESTCONF root (RFC 8040
    # §3.1), at {root}/data/…: one path per resource. Clients find the root
    # at /.well-known/host-meta. With an empty root the data resource is
    # /data/…. Every augment of the context (topology, connectivity, path
    # computation, equipment, OAM, streaming) is under tapi-common:context
    # (RFC 8040 §3.5.3).
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
        oam.router,
        streaming.router,
    ):
        app.include_router(tapi_router, prefix=root)
    # The RESTCONF protocol's own data resources: the datastore, the YANG
    # library and restconf-state (RFC 8040 §3.3.1, §9, §10).
    app.include_router(restconf_resources.router, prefix=root)

    app.include_router(internal.router)
    app.include_router(diagrams.router)
    app.include_router(admin.router)
    app.include_router(config_api.router)
    app.include_router(parameters.router)
    app.include_router(transparent.router)
    app.include_router(metrics_api.router)
    app.include_router(auth.router)

    _add_restconf_root_resources(app, root)
    app.include_router(restconf_resources.operations_router, prefix=root)

    if authenticator.enabled:
        _document_bearer_auth(app, PUBLIC_PATHS)

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok"}

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        if isinstance(exc, RestconfError):
            # Carry the exception's headers through: a 401 without its
            # WWW-Authenticate challenge, or a 405 without Allow, is malformed.
            return _restconf_error_response(
                exc.status_code, exc.error_tag, msg, error_path=exc.error_path,
                headers=exc.headers, error_app_tag=exc.error_app_tag,
            )
        status_code = exc.status_code
        if status_code == 422:
            # RFC 8040 does not use 422 (see below); a handler that still
            # raises it means a value the twin does not accept.
            status_code = 400
            tag = "invalid-value"
        else:
            tag = _http_status_to_error_tag(status_code)
        return _restconf_error_response(
            status_code, tag, msg, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        msg = "; ".join(
            f"{e['loc']}: {e['msg']}" for e in exc.errors()
        ) if exc.errors() else str(exc)
        # RFC 8040 does not use 422: §7 pairs every tag that describes a
        # request the server cannot accept as sent with 400. One convention
        # for the whole twin, the proprietary endpoints included.
        return _restconf_error_response(400, validation_error_tag(exc.errors()), msg)

    return app
