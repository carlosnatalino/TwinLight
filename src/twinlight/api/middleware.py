"""HTTP middleware: RESTCONF content type, path decoding, token authentication.

TAPI uses RESTCONF convention: ``application/yang-data+json``.
``RestconfContentTypeMiddleware`` sets the correct Content-Type on /data/
responses while still accepting standard ``application/json`` for convenience.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from urllib.parse import unquote

from starlette.authentication import AuthCredentials, SimpleUser
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

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
