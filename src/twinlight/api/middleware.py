"""HTTP middleware: RESTCONF header fields and request checks, path decoding,
token authentication, Allow, HEAD and OPTIONS.

TAPI uses the RESTCONF media type, ``application/yang-data+json``.
``RestconfHeadersMiddleware`` stamps it (and ``Cache-Control``) on every
RESTCONF response; ``RestconfRequestMiddleware`` refuses the query
parameters, ``Accept`` and request media types RFC 8040 has a server refuse.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qsl, unquote

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


class RestconfHeadersMiddleware:
    """Stamp the RESTCONF header fields on every RESTCONF response.

    - ``Content-Type: application/yang-data+json`` on a JSON body: RFC 8040
      §3.3 gives the API resource that media type and §3.2 every RESTCONF
      representation, so the root and ``yang-library-version`` get it as
      well as the data resources (``application/json`` there would be a
      conformance gap in exactly the surface RFC 8040 governs).
    - ``Cache-Control: no-cache``: §5.5, "The server MUST include a
      'Cache-Control' header field in every response that specifies whether
      the response should be cached", and "responses ... SHOULD NOT be
      cached" since the datastore changes at unpredictable times.

    A pure ASGI middleware just inside ``CORSMiddleware``, so a 401 from the
    token check and the answers of ``HeadMiddleware`` and
    ``OptionsMiddleware`` get the same fields. ``/.well-known/host-meta``
    gets the ``Cache-Control`` too: it is how a client finds the root.
    """

    def __init__(self, app: ASGIApp, restconf_root: str = "") -> None:
        self.app = app
        self._root = restconf_root

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        restconf = is_restconf_path(path, self._root) or is_restconf_path(
            unquote(path), self._root
        )
        if not restconf and path != "/.well-known/host-meta":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", [])
                    if not (restconf and k.lower() == b"content-type" and b"json" in v)
                ]
                if restconf and len(headers) != len(message.get("headers", [])):
                    headers.append((b"content-type", RESTCONF_MEDIA_TYPE.encode()))
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


#: The media types a RESTCONF request body may carry. RFC 8040 §5.2: "If the
#: server does not support the requested input encoding for a request, then
#: it MUST return an error response with a '415 Unsupported Media Type'
#: status-line." ``application/yang-data+json`` is the RESTCONF encoding;
#: ``application/json`` names the same JSON and is what generic HTTP clients
#: send (docs/TAPI_COMPLIANCE.md records the leniency). XML, YANG Patch
#: (``application/yang-patch+json``) and anything else are refused.
ACCEPTED_INPUT = frozenset({RESTCONF_MEDIA_TYPE, "application/json"})

#: Media ranges an ``Accept`` may name for the twin's one output encoding.
_ACCEPTABLE_OUTPUT = frozenset({
    RESTCONF_MEDIA_TYPE, "application/yang-data", "application/json",
    "application/*", "*/*",
})

#: Query parameters a RESTCONF resource accepts: only ``content``, which RFC
#: 8040 §4.8.1 makes mandatory ("This query parameter MUST be supported by
#: the server"), and only for GET and HEAD. Every optional one is
#: unsupported (none is advertised in restconf-state/capabilities), so
#: §4.8 applies: "A server MUST return an error with a '400 Bad Request'
#: status-line if a query parameter is unexpected."
_CONTENT_METHODS = frozenset({"GET", "HEAD"})


def _restconf_error(status: int, tag: str, message: str) -> Response:
    """An RFC 8040 §7 error from the middleware, before routing."""
    body = {"ietf-restconf:errors": {"error": [{
        "error-type": "protocol", "error-tag": tag, "error-message": message,
    }]}}
    return Response(content=json.dumps(body), status_code=status, media_type=RESTCONF_MEDIA_TYPE)


def _media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _acceptable(accept: str) -> bool:
    """Whether an ``Accept`` header admits the twin's JSON (RFC 9110 §12.5.1;
    a range with ``q=0`` is "not acceptable")."""
    for item in accept.split(","):
        media, *params = (part.strip() for part in item.split(";"))
        if not media:
            continue
        q = next((p.split("=", 1)[1] for p in params if p.lower().startswith("q=")), "1")
        try:
            if float(q) == 0:
                continue
        except ValueError:
            continue
        if media.lower() in _ACCEPTABLE_OUTPUT:
            return True
    return False


def check_restconf_request(scope: Scope) -> Response | None:
    """The protocol error a RESTCONF request earns before routing, or None.

    In order: the query (RFC 8040 §4.8: an unexpected parameter, or one
    given twice, "MUST" be a 400 ``invalid-value``; §4.8.1's ``content``
    only on GET and HEAD, and only ``all``, the default — ``config`` and
    ``nonconfig`` need the YANG config/state split the twin does not have,
    so they are a 501), then ``Accept`` (§5.2: "The server MUST support the
    'Accept' header field and the '406 Not Acceptable' status-line"; the
    tag RFC 8040 §7 pairs with 406 is ``invalid-value``), then the body's
    ``Content-Type`` (§5.2, 415; a body without one is refused too, RFC
    9110 §8.3 letting the server treat it as ``application/octet-stream``).
    """
    method = scope["method"]
    pairs = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
    names = [name for name, _ in pairs]
    for name in names:
        if names.count(name) > 1:
            return _restconf_error(
                400, "invalid-value", f"query parameter {name!r} is given more than once",
            )
    for name, value in pairs:
        if name != "content":
            return _restconf_error(
                400, "invalid-value",
                f"query parameter {name!r} is not supported by this server",
            )
        if method not in _CONTENT_METHODS:
            return _restconf_error(
                400, "invalid-value", f"query parameter 'content' is not allowed on {method}",
            )
        if value in ("config", "nonconfig"):
            return _restconf_error(
                501, "operation-not-supported",
                f"content={value} is not supported by this server; content=all is",
            )
        if value != "all":
            return _restconf_error(
                400, "invalid-value",
                f"content={value!r} is not one of 'config', 'nonconfig', 'all'",
            )
    accept = _header(scope, b"accept")
    if accept is not None and accept.strip() and not _acceptable(accept):
        return _restconf_error(
            406, "invalid-value",
            f"cannot answer in {accept!r}: this server encodes {RESTCONF_MEDIA_TYPE} only",
        )
    has_body = _header(scope, b"transfer-encoding") is not None or (
        (_header(scope, b"content-length") or "0").strip() not in ("", "0")
    )
    if has_body:
        content_type = _header(scope, b"content-type")
        if content_type is None or _media_type(content_type) not in ACCEPTED_INPUT:
            return _restconf_error(
                415, "invalid-value",
                f"a request body must be {RESTCONF_MEDIA_TYPE}"
                + (f", not {content_type!r}" if content_type else "; it has no Content-Type"),
            )
    return None


class RestconfRequestMiddleware:
    """Refuse a RESTCONF request the protocol refuses, before routing.

    See ``check_restconf_request``. Inside token authentication, so an
    unauthenticated client learns nothing from these checks, and inside
    ``PathDecodeMiddleware``, so it sees the decoded path. The proprietary
    endpoints are not checked: their query parameters and media types are
    their own.
    """

    def __init__(self, app: ASGIApp, restconf_root: str = "") -> None:
        self.app = app
        self._root = restconf_root

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and is_restconf_path(scope["path"], self._root):
            refused = check_restconf_request(scope)
            if refused is not None:
                await refused(scope, receive, send)
                return
        await self.app(scope, receive, send)


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
