"""gRPC server interceptor enforcing the twin's bearer-token authentication.

The gNMI counterpart of ``api/middleware.py:TokenAuthMiddleware``, checking
the same :class:`~twinlight.auth.TokenAuthenticator`, so a token works on
both interfaces or on neither. gRPC carries HTTP/2 headers as metadata with
lower-cased keys, so the credential is the ``authorization`` entry, holding
``Bearer <token>`` exactly as on REST (TR-548 ST-0.1 has the client hold a
token before it connects).

``build_grpc_server`` installs it only when tokens are configured, so a twin
without tokens runs no interceptor at all. A rejected call ends with
``UNAUTHENTICATED`` before the servicer runs, so a Subscribe without a valid
token never opens a stream.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import grpc

from twinlight.auth import TokenAuthenticator, bearer_token

logger = logging.getLogger(__name__)

#: Metadata key carrying the credential (gRPC lower-cases header names).
AUTHORIZATION_METADATA_KEY = "authorization"

_MISSING = "authentication required: send 'authorization: Bearer <token>' metadata"
_INVALID = "the bearer token is not valid for this twin"


def authorization_from_metadata(metadata: Any) -> str | None:
    """The ``authorization`` value from gRPC invocation metadata, if any."""
    for key, value in metadata or ():
        if key == AUTHORIZATION_METADATA_KEY:
            return value if isinstance(value, str) else None
    return None


class TokenAuthInterceptor(grpc.aio.ServerInterceptor):
    """Reject every RPC that lacks a valid bearer token."""

    def __init__(self, authenticator: TokenAuthenticator) -> None:
        if not authenticator.enabled:
            raise ValueError("no tokens configured: do not install the interceptor")
        self._auth = authenticator

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        if handler is None:  # unknown method: grpc answers UNIMPLEMENTED
            return handler
        token = bearer_token(
            authorization_from_metadata(handler_call_details.invocation_metadata)
        )
        if self._auth.authenticate(token) is not None:
            return handler
        logger.warning(
            "Rejected gRPC %s: %s bearer token",
            handler_call_details.method, "invalid" if token else "no",
        )
        return _denying(handler, _INVALID if token else _MISSING)


def _denying(handler: grpc.RpcMethodHandler, details: str) -> grpc.RpcMethodHandler:
    """A handler of the same shape as ``handler`` that aborts UNAUTHENTICATED.

    The replacement has to match the original's streaming flags and
    (de)serialisers, or grpc would mis-frame the call before the abort is
    ever sent.
    """

    async def deny_unary(request: Any, context: grpc.aio.ServicerContext) -> Any:
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, details)

    async def deny_stream(request: Any, context: grpc.aio.ServicerContext) -> Any:
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, details)
        yield  # pragma: no cover — abort raises

    codec = {
        "request_deserializer": handler.request_deserializer,
        "response_serializer": handler.response_serializer,
    }
    if handler.request_streaming and handler.response_streaming:
        return grpc.stream_stream_rpc_method_handler(deny_stream, **codec)
    if handler.request_streaming:
        return grpc.stream_unary_rpc_method_handler(deny_unary, **codec)
    if handler.response_streaming:
        return grpc.unary_stream_rpc_method_handler(deny_stream, **codec)
    return grpc.unary_unary_rpc_method_handler(deny_unary, **codec)
