"""HTTP middleware: RESTCONF content type, path decoding, token authentication, Allow.

TAPI uses RESTCONF convention: ``application/yang-data+json``.
``RestconfContentTypeMiddleware`` sets the correct Content-Type on /data/
responses while still accepting standard ``application/json`` for convenience.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import unquote

from starlette.authentication import AuthCredentials, SimpleUser
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from twinlight.auth import TokenAuthenticator, bearer_token

RESTCONF_MEDIA_TYPE = "application/yang-data+json"

logger = logging.getLogger(__name__)

#: The ``realm`` advertised in ``WWW-Authenticate`` challenges (RFC 6750 §3).
AUTH_REALM = "twinlight"


class TokenAuthMiddleware:
    """Require a bearer token on every request outside ``public_paths``.

    ``create_app`` installs it only when tokens are configured, so a twin
    without tokens never runs this code at all; there is no per-request
    "is authentication on?" test.

    Deny by default: a route added later is protected without anyone having
    to remember to protect it, and an unauthenticated probe of an unknown
    path gets 401 rather than a 404 that maps the API. ``public_paths`` are
    exact matches on the raw path, so ``/health`` is open but ``/health/x``
    and ``/%68ealth`` are not. WebSocket handshakes are held to the same
    rule (closed with 1008, policy violation) so that adding one later
    cannot open a hole; only the ``lifespan`` scope passes untouched.

    A pure ASGI middleware rather than ``BaseHTTPMiddleware``: it either
    answers 401 itself or hands the untouched request on, so there is no
    response body to buffer, and it reads the one header it needs straight
    from the scope instead of building a ``Request``. On success it sets
    ``scope["user"]`` and ``scope["auth"]`` the way Starlette's own
    ``AuthenticationMiddleware`` does, so ``request.user.display_name`` is
    the token's principal name.

    Must sit *inside* ``CORSMiddleware``: a CORS preflight carries no
    credentials and has to be answered before this runs, and a 401 needs
    CORS headers for a browser to be allowed to read it.
    """

    def __init__(
        self,
        app: ASGIApp,
        authenticator: TokenAuthenticator,
        public_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        self._auth = authenticator
        self._public = frozenset(public_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan" or scope["path"] in self._public:
            await self.app(scope, receive, send)
            return

        token = bearer_token(_header(scope, b"authorization"))
        principal = self._auth.authenticate(token)
        if principal is not None:
            scope["user"] = SimpleUser(principal)
            scope["auth"] = AuthCredentials(["authenticated"])
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        logger.warning(
            "Rejected %s %s from %s: %s bearer token",
            scope.get("method", "WEBSOCKET"), scope["path"],
            client[0] if client else "unknown",
            "invalid" if token else "no",
        )
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await _unauthorized(invalid=token is not None)(scope, receive, send)


def _header(scope: Scope, name: bytes) -> str | None:
    """The first value of header ``name`` (lower-case), from the raw scope."""
    for key, value in scope["headers"]:
        if key == name:
            return value.decode("latin-1")
    return None


def _unauthorized(*, invalid: bool) -> Response:
    """A 401 with an RFC 6750 challenge and an RFC 8040 error body.

    RFC 6750 §3.1: a request that offered no credentials gets a bare
    challenge; one that offered an unrecognised token gets
    ``error="invalid_token"``. RFC 8040 §7 maps 401 to ``access-denied``.
    """
    challenge = f'Bearer realm="{AUTH_REALM}"'
    if invalid:
        challenge += ', error="invalid_token"'
        message = "The bearer token is not valid for this twin."
    else:
        message = (
            "Authentication required: send 'Authorization: Bearer <token>'."
        )
    body = {
        "ietf-restconf:errors": {
            "error": [{
                "error-type": "protocol",
                "error-tag": "access-denied",
                "error-message": message,
            }]
        }
    }
    return Response(
        content=json.dumps(body),
        status_code=401,
        media_type=RESTCONF_MEDIA_TYPE,
        headers={"WWW-Authenticate": challenge},
    )


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

    The T-API modules are served under ``{restconf_root}/data/`` (``/data/``
    when the root is empty), so that is the prefix recognised — a response
    served from there with ``application/json`` would be a conformance gap
    in exactly the surface RFC 8040 governs.
    """

    def __init__(self, app, restconf_root: str = "") -> None:
        super().__init__(app)
        self._prefix = f"{restconf_root}/data/"

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if "json" in ct and request.url.path.startswith(self._prefix):
            response.headers["Content-Type"] = RESTCONF_MEDIA_TYPE
        return response


#: The methods a route can serve, in the order an ``Allow`` header lists them.
ALLOW_CANDIDATES = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE")

#: Maps a route path to the methods its handler answers with 405 although a
#: route serves them there (see ``AllowHeaderMiddleware``).
MethodRefusal = Callable[[str], Iterable[str]]


def routed_methods(app: Any, path: str, root_path: str = "") -> list[str]:
    """The methods some route of ``app`` fully matches at ``path``.

    Asks each top-level route, the way the router does, whether it would
    take a request with that method. Top-level routes include the routers
    this FastAPI version includes lazily (``app.routes`` does not list
    their routes), which answer for everything below them, so the result
    covers every route, whatever the prefix, without walking the table.
    """
    methods = []
    for method in ALLOW_CANDIDATES:
        probe: Scope = {
            "type": "http", "method": method, "path": path,
            "root_path": root_path, "headers": [], "query_string": b"",
        }
        if any(route.matches(dict(probe))[0] is Match.FULL for route in app.router.routes):
            methods.append(method)
    return methods


class AllowHeaderMiddleware:
    """Give every 405 an ``Allow`` header listing all the resource's methods.

    RFC 9110 §15.5.6: a 405 MUST carry ``Allow`` naming the methods the
    target resource supports. Starlette builds the header from the first
    route that matches the path, and FastAPI registers one route per method,
    so where several operations share a path the router named only one of
    them (``PATCH …/connectivity-service`` answered ``Allow: POST``,
    although GET is served there too). This rebuilds the header for every
    405, from the router's own matching (``routed_methods``), so a route
    added later is covered without anyone having to declare anything.

    Two kinds of method are taken out: the request's own (it got a 405, so
    whatever answered it does not support it), and those a ``refusals``
    function reports for the path — methods a route serves only to refuse
    for that resource, which routing cannot see (a write to a read-only
    datastore). HEAD and OPTIONS are listed only where a route serves them;
    CORS preflights are answered by ``CORSMiddleware`` before routing and do
    not make OPTIONS a method of the resource.

    Pure ASGI and innermost, so it sees the decoded path and the response
    of the exception handler, and costs nothing on any other status.
    """

    def __init__(self, app: ASGIApp, refusals: Iterable[MethodRefusal] = ()) -> None:
        self.app = app
        self._refusals = tuple(refusals)

    def allowed(self, app: Any, path: str, root_path: str, method: str) -> list[str]:
        route_path = path[len(root_path):] if root_path and path.startswith(root_path) else path
        refused = {method}
        for refusal in self._refusals:
            refused.update(refusal(route_path))
        return [m for m in routed_methods(app, path, root_path) if m not in refused]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Captured before routing, which may rewrite the scope.
        path, root_path, method = scope["path"], scope.get("root_path", ""), scope["method"]

        async def send_with_allow(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 405:
                allow = ", ".join(self.allowed(scope["app"], path, root_path, method))
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"allow"
                ]
                headers.append((b"allow", allow.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_allow)
