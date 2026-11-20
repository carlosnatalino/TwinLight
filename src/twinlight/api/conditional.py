"""Entity-tags, Last-Modified, conditional requests and ``content`` on the
RESTCONF datastore and data resources.

RFC 8040 §3.4.1.2: "The server MUST maintain a unique opaque entity-tag for
the datastore resource and MUST return it in the 'ETag' ... header in the
response for a retrieval request"; §3.4.1.1 and §3.5.1–§3.5.2 add the
timestamp and, for a data resource without an entity-tag of its own, "the
resource entity-tag for the datastore MUST be used instead". Once a server
has validators, RFC 9110 §13 says how ``If-Match``, ``If-None-Match``,
``If-Modified-Since`` and ``If-Unmodified-Since`` are evaluated.

Which validator a representation carries (``state/revision.py``):

- ``content=config``: the configuration validator, RFC 8040's datastore
  entity-tag, which only a change of a ``config true`` node moves.
- ``content=all`` (the default) and ``content=nonconfig``: the full
  validator, which every change a T-API body can show moves. RFC 8040
  §3.4.1.2: "each representation ... requires a different entity-tag"; the
  configuration tag on a body carrying operational state would let a
  ``304`` hide a link failure.

A write's ``If-Match`` passes when it names the current tag of either
representation, and ``If-Unmodified-Since`` is compared with the
configuration's time: edit collision prevention concerns configuration
(§3.4.1). No validator is sent on a write's response: RFC 9110 §9.3.4 forbids
one on a PUT whose body the server transformed, which admission always does.

The middleware is route-agnostic, so a resource or write route added later
is covered without declaring anything:

- every unsafe request, on any path, holds ``ctx.write_gate`` shared; a
  RESTCONF write with a precondition holds it exclusively from the
  evaluation to the end of its response, so no other write can land in
  between;
- preconditions are ignored where the request without them would not have
  succeeded before its content was read (RFC 9110 §13.2.1): a method the
  resource does not serve (405), a target that does not exist, unless the
  method is PUT, which would create it (404). Existence is asked of the
  resource's own GET;
- on a GET (a HEAD arrives here as its GET, ``HeadMiddleware``), the
  validators are those of the state when the request started; a ``304``
  needs the state unchanged at the end as well, so a body that may mix two
  states is never confirmed. Validators go on 2xx responses only.

It sits inside token authentication (a 401 comes before any precondition)
and inside ``RestconfRequestMiddleware`` (a 400, 406 or 415 is detected
before the content is processed, so it takes precedence too).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl

from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from twinlight.api.middleware import RESTCONF_MEDIA_TYPE, routed_methods
from twinlight.api.restconf import is_data_path
from twinlight.api.restconf_content import filter_body, target_of

UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: An entity-tag in a list: optional ``W/`` and a quoted opaque-tag (RFC
#: 9110 §8.8.3; etagc excludes DQUOTE, so the tag runs to the next quote).
_ETAG = re.compile(r'\s*(W/)?"([^"]*)"\s*(?:,|$)')


def _header(scope: Scope, name: bytes) -> str | None:
    """A header's field value, several field lines joined with commas (RFC
    9110 §5.3); None when absent."""
    values = [v.decode("latin-1") for k, v in scope["headers"] if k == name]
    return ", ".join(values) if values else None


def _tags(value: str) -> tuple[bool, list[tuple[bool, str]]]:
    """``(any, [(weak, opaque-tag)])`` of an If-Match / If-None-Match value.

    ``*`` is "any". A member that is not an entity-tag matches nothing, so a
    malformed If-Match fails and a malformed If-None-Match passes.
    """
    if value.strip() == "*":
        return True, []
    return False, [(bool(m[1]), m[2]) for m in _ETAG.finditer(value)]


def _strong_match(tags: list[tuple[bool, str]], current: set[str]) -> bool:
    """RFC 9110 §8.8.3.2 strong comparison: neither weak, same opaque-tag."""
    return any(not weak and f'"{tag}"' in current for weak, tag in tags)


def _weak_match(tags: list[tuple[bool, str]], current: set[str]) -> bool:
    """Weak comparison: same opaque-tag, either or both weak."""
    return any(f'"{tag}"' in current for _weak, tag in tags)


def _date(scope: Scope, name: bytes) -> datetime | None:
    """A valid HTTP-date header (RFC 9110 §13.1.3, §13.1.4: ignored when
    invalid or with more than one member), not in the future."""
    value = _header(scope, name)
    if value is None or "," in value.split(" ", 1)[-1]:
        return None  # several members (a date has its one comma after the day)
    try:
        date = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return None if date > datetime.now(UTC) else date


def _http_date(when: datetime) -> str:
    return format_datetime(when.astimezone(UTC), usegmt=True)


def _precondition_failed(message: str, headers: dict[str, str]) -> Response:
    """412 with the ``operation-failed`` error RFC 8040 §7 pairs with it."""
    body = {"ietf-restconf:errors": {"error": [{
        "error-type": "protocol", "error-tag": "operation-failed", "error-message": message,
    }]}}
    return Response(
        content=json.dumps(body), status_code=412, media_type=RESTCONF_MEDIA_TYPE,
        headers=headers,
    )


def _not_found(message: str) -> Response:
    body = {"ietf-restconf:errors": {"error": [{
        "error-type": "application", "error-tag": "invalid-value", "error-message": message,
    }]}}
    return Response(content=json.dumps(body), status_code=404, media_type=RESTCONF_MEDIA_TYPE)


def _content(scope: Scope) -> str:
    for name, value in parse_qsl(scope.get("query_string", b"").decode("latin-1")):
        if name == "content":
            return value
    return "all"


def _raw_path(scope: Scope) -> str:
    raw = scope.get("raw_path")
    return raw.decode("latin-1") if isinstance(raw, bytes) else scope["path"]


class ConditionalRequestMiddleware:
    """See the module docstring."""

    def __init__(self, app: ASGIApp, restconf_root: str = "") -> None:
        self.app = app
        self._root = restconf_root

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        data = is_data_path(scope["path"], self._root)
        ctx = scope["app"].state.context
        if method in UNSAFE:
            guarded = data and any(
                _header(scope, name) is not None
                for name in (b"if-match", b"if-none-match", b"if-unmodified-since")
            )
            if guarded:
                async with ctx.write_gate.exclusive():
                    await self._write(scope, receive, send, ctx)
            else:
                async with ctx.write_gate.shared():
                    await self.app(scope, receive, send)
            return
        if method == "GET" and data:
            await self._read(scope, receive, send, ctx)
            return
        await self.app(scope, receive, send)

    # -- writes ---------------------------------------------------------------

    async def _exists(self, scope: Scope) -> bool:
        """Whether the target has a current representation: its GET is not 404."""
        dropped = {
            b"content-length", b"content-type", b"transfer-encoding", b"if-match",
            b"if-none-match", b"if-modified-since", b"if-unmodified-since",
        }
        probe = {
            **scope, "method": "GET", "query_string": b"",
            "headers": [(k, v) for k, v in scope["headers"] if k not in dropped],
        }
        status = 0

        async def no_body() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]

        await self.app(probe, no_body, capture)
        # A 405 means the resource serves no GET: existence unknown, so
        # the preconditions are evaluated.
        return status != 404

    async def _write(self, scope: Scope, receive: Receive, send: Send, ctx: Any) -> None:
        method = scope["method"]
        if method not in routed_methods(scope["app"], scope["path"], scope.get("root_path", "")):
            await self.app(scope, receive, send)  # 405 from the router
            return
        exists = await self._exists(scope)
        if not exists and method != "PUT":
            await self.app(scope, receive, send)  # 404 from the handler
            return
        failure = self._write_failure(scope, ctx.datastore_revision, exists)
        if failure is not None:
            rev = ctx.datastore_revision
            await _precondition_failed(failure, {
                "ETag": rev.etag("config"), "Last-Modified": _http_date(rev.config.modified),
            })(scope, receive, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _write_failure(scope: Scope, rev: Any, exists: bool) -> str | None:
        """Why a write's preconditions fail (RFC 9110 §13.2.2), or None."""
        current = {rev.etag("config"), rev.etag("full")}
        if_match = _header(scope, b"if-match")
        if if_match is not None:
            any_tag, tags = _tags(if_match)
            if not exists:
                return "If-Match: the target resource does not exist"
            if not (any_tag or _strong_match(tags, current)):
                return (
                    f"If-Match: the datastore changed; its entity-tag is now "
                    f"{rev.etag('config')} (configuration) or {rev.etag('full')}"
                )
        elif exists and (date := _date(scope, b"if-unmodified-since")) is not None:
            if rev.config.modified_after(date):
                return (
                    "If-Unmodified-Since: the configuration was modified at "
                    f"{_http_date(rev.config.modified)}"
                )
        if_none_match = _header(scope, b"if-none-match")
        if if_none_match is not None and exists:
            any_tag, tags = _tags(if_none_match)
            if any_tag:
                return "If-None-Match: *: the target resource exists"
            if _weak_match(tags, current):
                return "If-None-Match: the entity-tag matches the current one"
        return None

    # -- reads ----------------------------------------------------------------

    async def _read(self, scope: Scope, receive: Receive, send: Send, ctx: Any) -> None:
        mode = _content(scope)
        target = target_of(_raw_path(scope), f"{self._root}/data")
        if mode == "config" and (target is None or not target.node.config):
            await _not_found(
                "content=config: the target is config false, so it has no "
                "configuration data; use content=nonconfig or content=all",
            )(scope, receive, send)
            return
        rev = ctx.datastore_revision
        kind = "config" if mode == "config" else "full"
        validator = rev.validator(kind)
        start, etag, modified = validator.revision, rev.etag(kind), validator.modified
        filtering = mode != "all" and target is not None
        # "pass": send the handler's messages on; "drop": a 304 or 412 was
        # sent instead; "buffer": collect the body to filter it.
        route = "pass"
        held: Message = {}
        chunks: list[bytes] = []

        async def send_wrapper(message: Message) -> None:
            nonlocal route, held
            if message["type"] == "http.response.start":
                if not 200 <= message["status"] < 300:
                    route = "pass"
                    await send(message)
                    return
                outcome = self._read_outcome(scope, rev, kind, start)
                if outcome is not None:
                    route = "drop"
                    await outcome(scope, receive, send)
                    return
                headers = [
                    (k, v) for k, v in message.get("headers", [])
                    if k.lower() not in (b"etag", b"last-modified")
                ]
                headers.append((b"etag", etag.encode()))
                headers.append((b"last-modified", _http_date(modified).encode()))
                held = {**message, "headers": headers}
                route = "buffer" if filtering else "pass"
                if route == "pass":
                    await send(held)
                return
            if message["type"] != "http.response.body" or route == "pass":
                await send(message)
                return
            if route == "drop":
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            assert target is not None
            body = filter_body(target, json.loads(b"".join(chunks)), mode)
            content = json.dumps(body).encode()
            headers = [(k, v) for k, v in held["headers"] if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(content)).encode()))
            await send({**held, "headers": headers})
            await send({"type": "http.response.body", "body": content, "more_body": False})

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    def _read_outcome(scope: Scope, rev: Any, kind: str, start: int) -> Response | None:
        """A 412 or 304 instead of the 2xx a GET earned (RFC 9110 §13.2.2), or None."""
        validator = rev.validator(kind)
        etag = rev.etag(kind)
        unchanged = validator.revision == start
        if_match = _header(scope, b"if-match")
        if if_match is not None:
            any_tag, tags = _tags(if_match)
            if not (any_tag or (_strong_match(tags, {etag}) and unchanged)):
                return _precondition_failed(
                    f"If-Match: the representation changed; its entity-tag is now {etag}",
                    {"ETag": etag, "Last-Modified": _http_date(validator.modified)},
                )
        elif (date := _date(scope, b"if-unmodified-since")) is not None:
            if validator.modified_after(date) or not unchanged:
                return _precondition_failed(
                    f"If-Unmodified-Since: modified at {_http_date(validator.modified)}",
                    {"ETag": etag, "Last-Modified": _http_date(validator.modified)},
                )
        if_none_match = _header(scope, b"if-none-match")
        if if_none_match is not None:
            any_tag, tags = _tags(if_none_match)
            if unchanged and (any_tag or _weak_match(tags, {etag})):
                return Response(status_code=304, headers={"ETag": etag})
        elif (date := _date(scope, b"if-modified-since")) is not None:
            if unchanged and not validator.modified_after(date):
                return Response(status_code=304, headers={"ETag": etag})
        return None
