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

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if "%" in request.scope.get("path", ""):
            request.scope["path"] = unquote(request.scope["path"])
        return await call_next(request)


class RestconfContentTypeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response: Response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if request.url.path.startswith("/data/") and "json" in ct:
            response.headers["Content-Type"] = RESTCONF_MEDIA_TYPE
        return response
