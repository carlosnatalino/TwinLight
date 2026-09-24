"""RESTCONF content-type handling middleware.

TAPI uses RESTCONF convention: ``application/yang-data+json``.
This middleware sets the correct Content-Type on /data/ responses
while still accepting standard ``application/json`` for convenience.
"""

from __future__ import annotations

from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

RESTCONF_MEDIA_TYPE = "application/yang-data+json"


class PathDecodeMiddleware(BaseHTTPMiddleware):
    """URL-decode percent-encoded characters in the request path before routing.

    Browsers (per the WHATWG URL Standard) sometimes encode colons as %3A in
    URL paths. TAPI RESTCONF paths contain literal colons (e.g.
    ``tapi-common:context``), so we decode the path before Starlette's router
    sees it, ensuring the route patterns match correctly.
    """

    async def dispatch(self, request: Request, call_next):
        if "%" in request.scope.get("path", ""):
            request.scope["path"] = unquote(request.scope["path"])
        return await call_next(request)


class RestconfContentTypeMiddleware(BaseHTTPMiddleware):
    """Stamp the RESTCONF media type on T-API data resources.

    The T-API modules are reachable at the bare ``/data/`` and under the
    configured RESTCONF root, so both prefixes have to be recognised — a
    response served from ``{root}/data/`` with ``application/json`` would
    be a conformance gap in exactly the surface RFC 8040 governs.
    """

    def __init__(self, app, restconf_root: str = "") -> None:
        super().__init__(app)
        prefixes = ["/data/"]
        if restconf_root:
            prefixes.append(f"{restconf_root}/data/")
        self._prefixes = tuple(prefixes)

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if "json" in ct and request.url.path.startswith(self._prefixes):
            response.headers["Content-Type"] = RESTCONF_MEDIA_TYPE
        return response
