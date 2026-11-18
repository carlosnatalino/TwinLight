"""HTTP middleware: RESTCONF content type, path decoding, token authentication,
Allow, HEAD and OPTIONS.

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

from twinlight.api.restconf import is_restconf_path
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
    """The methods ``app`` serves at ``path``: routed ones, plus HEAD and OPTIONS.

    Asks each top-level route, the way the router does, whether it would
    take a request with that method. Top-level routes include the routers
    this FastAPI version includes lazily (``app.routes`` does not list
    their routes), which answer for everything below them, so the result
    covers every route, whatever the prefix, without walking the table.

    HEAD and OPTIONS are served by ``HeadMiddleware`` and
    ``OptionsMiddleware`` for every resource a route serves: HEAD wherever
    GET is, OPTIONS wherever any method is.
    """
    methods = []
    for method in ALLOW_CANDIDATES:
        probe: Scope = {
            "type": "http", "method": method, "path": path,
            "root_path": root_path, "headers": [], "query_string": b"",
        }
        if any(route.matches(dict(probe))[0] is Match.FULL for route in app.router.routes):
            methods.append(method)
    if not methods:
        return []
    served = set(methods) | {"OPTIONS"}
    if "GET" in served:
        served.add("HEAD")
    return [m for m in ALLOW_CANDIDATES if m in served]


def allowed_methods(
    app: Any, path: str, root_path: str,
    refusals: Iterable[MethodRefusal] = (), exclude: str | None = None,
) -> list[str]:
    """The methods the resource at ``path`` supports: what ``Allow`` lists.

    ``routed_methods`` less those a ``refusals`` function reports for the
    path (a route serves them only to refuse them for that resource, which
    routing cannot see) and less ``exclude``, the method of a request that
    got a 405 (whatever answered it does not support it). HEAD goes with
    GET: a resource that does not serve GET does not serve HEAD either.
    """
    route_path = path[len(root_path):] if root_path and path.startswith(root_path) else path
    refused = {exclude} if exclude else set()
    for refusal in refusals:
        refused.update(refusal(route_path))
    if "GET" in refused:
        refused.add("HEAD")
    return [m for m in routed_methods(app, path, root_path) if m not in refused]


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
    datastore). HEAD is listed wherever GET is and OPTIONS wherever any
    method is, since ``HeadMiddleware`` and ``OptionsMiddleware`` serve them
    for every route.

    Pure ASGI and innermost, so it sees the decoded path and the response
    of the exception handler, and costs nothing on any other status.
    """

    def __init__(self, app: ASGIApp, refusals: Iterable[MethodRefusal] = ()) -> None:
        self.app = app
        self._refusals = tuple(refusals)

    def allowed(self, app: Any, path: str, root_path: str, method: str) -> list[str]:
        return allowed_methods(app, path, root_path, self._refusals, exclude=method)

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


#: The patch media type a PATCH-capable RESTCONF resource accepts: the plain
#: patch of RFC 8040 §4.6.1, in the one encoding the twin speaks.
ACCEPT_PATCH = RESTCONF_MEDIA_TYPE


class HeadMiddleware:
    """Answer HEAD on every route that serves GET, without the body.

    RFC 8040 §4.2: "The RESTCONF server MUST support the HEAD method ... The
    server MUST respond the same as if the method was GET instead of HEAD,
    except that no response message-body is included", and RFC 9110 §9.3.2
    asks the same of any HTTP server. FastAPI registers GET routes without
    HEAD, so the request runs as the GET it mirrors and the body is dropped:
    a route added later answers HEAD without anyone declaring it. Header
    fields, ``Content-Length`` included, are the GET's (RFC 9110 §9.3.2).

    It sits just inside ``CORSMiddleware`` and outside token authentication,
    so a HEAD is authenticated exactly as its GET (§4.2: "The access control
    behavior is enforced as if the method was GET instead of HEAD") and a
    401 to a HEAD loses its body too. Where GET is not served, the GET the
    request became is refused with 405, and ``AllowHeaderMiddleware`` leaves
    HEAD out of ``Allow`` with it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message: Message) -> None:
            if message["type"] == "http.response.body":
                if message.get("more_body", False):
                    return
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


class OptionsMiddleware:
    """Answer OPTIONS with the methods the target resource supports.

    RFC 8040 §4.1: "The OPTIONS method is sent by the client to discover
    which methods are supported by the server for a specific resource ...
    The server MUST implement this method", and "The 'Accept-Patch' header
    field MUST be supported and returned in the response to the OPTIONS
    request". The answer is 200 with ``Allow`` computed as a 405's is
    (``allowed_methods``), so the two cannot disagree, and no content
    (``Content-Length: 0``, RFC 9110 §9.3.7). On a RESTCONF resource that
    takes PATCH it also carries ``Accept-Patch`` naming the plain patch:
    RFC 5789 §3.1 makes that field say PATCH is allowed, so it is not sent
    where ``Allow`` has no PATCH. A path no route matches is passed on, and
    the router answers 404.

    Every route gets it, the proprietary ones too: HTTP gives OPTIONS the
    same meaning everywhere, and one rule is simpler than a list of paths.
    CORS preflights (OPTIONS with ``Access-Control-Request-Method``) never
    get here: ``CORSMiddleware`` answers them first. Inside token
    authentication, so an OPTIONS needs a token like any other request, and
    inside ``PathDecodeMiddleware``, so it sees the decoded path.
    """

    def __init__(
        self, app: ASGIApp, restconf_root: str = "",
        refusals: Iterable[MethodRefusal] = (),
    ) -> None:
        self.app = app
        self._root = restconf_root
        self._refusals = tuple(refusals)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "OPTIONS":
            await self.app(scope, receive, send)
            return
        path, root_path = scope["path"], scope.get("root_path", "")
        methods = allowed_methods(scope["app"], path, root_path, self._refusals)
        if not methods:
            await self.app(scope, receive, send)
            return
        headers = {"Allow": ", ".join(methods)}
        if "PATCH" in methods and is_restconf_path(path, self._root):
            headers["Accept-Patch"] = ACCEPT_PATCH
        await Response(status_code=200, headers=headers)(scope, receive, send)
