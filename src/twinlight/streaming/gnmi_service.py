"""gNMI servicer for the T-API performance-measurement stream (TR-548 §6.3).

This is the twin's only gNMI interface. It serves one stream: the
measurement-details records the ``MeasurementStream`` logs, each a
``SubscribeResponse`` whose update path is an oam-job and whose value is a
serialized ``tapi.MeasurementDetails`` in ``proto_bytes``.

Subscribe accepts only the subset TR-548 §6.3.4 defines and answers anything
else with INVALID_ARGUMENT naming the offending field. A history-extension
``range.start`` resumes from the log per ST-6.4. Get and Set are not
implemented (gRPC UNIMPLEMENTED).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import grpc

from twinlight.streaming.measurement_stream import MeasurementStream
from twinlight.streaming.proto import gnmi_pb2, gnmi_pb2_grpc

logger = logging.getLogger(__name__)

GNMI_VERSION: str = gnmi_pb2.DESCRIPTOR.GetOptions().Extensions[gnmi_pb2.gnmi_service]
INT64_MAX = 2**63 - 1

# The models whose data the stream carries.
SUPPORTED_MODELS = [
    gnmi_pb2.ModelData(name=name, organization="Linux Foundation ONMI", version="2.6.0")
    for name in ("tapi-streaming", "tapi-gnmi-streaming", "tapi-oam")
]


class SubscriptionError(ValueError):
    """A SubscribeRequest outside the TR-548 §6.3.4 subset."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason} (TR-548 §6.3.4)")
        self.field = field


def _path_is_empty(path: gnmi_pb2.Path) -> bool:
    return not (path.elem or path.element or path.origin or path.target)


def validate_subscribe_request(request: gnmi_pb2.SubscribeRequest) -> int | None:
    """Check ``request`` against TR-548 §6.3.4; return the resume time, if any.

    Fields the RIA says are not used and "should be ignored" when present
    (``qos``, ``allow_aggregation``) are ignored. ``encoding`` is not listed;
    PROTO and the proto3 default (JSON, i.e. omitted) are accepted, since the
    records are protobuf whatever a client asks for.

    Returns the history extension's ``range.start`` in ns, or None.
    """
    if request.WhichOneof("request") != "subscribe":
        raise SubscriptionError("request", "the first message must be a subscribe")
    sl = request.subscribe
    if sl.mode != gnmi_pb2.SubscriptionList.STREAM:
        raise SubscriptionError("subscribe.mode", "must be STREAM")
    prefix = sl.prefix
    if prefix.origin:
        raise SubscriptionError("subscribe.prefix.origin", "must be omitted")
    if prefix.elem or prefix.element:
        raise SubscriptionError("subscribe.prefix.elem", "must be omitted")
    if prefix.target not in ("", "tapi"):
        raise SubscriptionError("subscribe.prefix.target", "must be 'tapi' or omitted")
    if len(sl.subscription) != 1:
        raise SubscriptionError("subscribe.subscription", "must have exactly one entry")
    sub = sl.subscription[0]
    field = "subscribe.subscription[0]"
    if not _path_is_empty(sub.path):
        raise SubscriptionError(
            f"{field}.path", "must be empty ({}): the stream is not filtered"
        )
    # §6.3.4 lists TARGET_DEFINED; its own example subscribes with SAMPLE and
    # sample_interval 0 ("provider choice"). Both mean the same here.
    if sub.mode not in (
        gnmi_pb2.SubscriptionMode.TARGET_DEFINED,
        gnmi_pb2.SubscriptionMode.SAMPLE,
    ):
        raise SubscriptionError(f"{field}.mode", "must be TARGET_DEFINED")
    if sub.sample_interval:
        raise SubscriptionError(f"{field}.sample_interval", "must be omitted (provider choice)")
    if sub.suppress_redundant:
        raise SubscriptionError(f"{field}.suppress_redundant", "must be omitted")
    if sub.heartbeat_interval:
        raise SubscriptionError(f"{field}.heartbeat_interval", "must be omitted")
    if not sl.updates_only:
        raise SubscriptionError("subscribe.updates_only", "must be true")
    if sl.use_models:
        raise SubscriptionError("subscribe.use_models", "must be omitted")
    if sl.encoding not in (gnmi_pb2.Encoding.JSON, gnmi_pb2.Encoding.PROTO):
        raise SubscriptionError("subscribe.encoding", "must be PROTO or omitted")

    start: int | None = None
    for ext in request.extension:
        kind = ext.WhichOneof("ext")
        if kind != "history":
            raise SubscriptionError(f"extension.{kind}", "only the history extension is used")
        if start is not None:
            raise SubscriptionError("extension.history", "at most one history extension")
        history = ext.history
        if history.WhichOneof("request") != "range":
            raise SubscriptionError("extension.history.snapshot_time", "not used; send range")
        if history.range.end not in (0, INT64_MAX):
            raise SubscriptionError("extension.history.range.end", "must be omitted")
        if history.range.start < 0:
            raise SubscriptionError("extension.history.range.start", "must not be negative")
        start = history.range.start
    return start


def initial_cursor(stream: MeasurementStream, start_ns: int | None, now_ns: int) -> int:
    """The logged time after which a new subscription's records begin.

    Without history: live records only (``updates_only``; each record is
    independent, so there is no initial state to send).

    With ``range.start`` (TR-548 ST-6.4): from the first record logged at or
    after ``start``, so the record(s) at ``start`` are sent again; from the
    oldest retained record when ``start`` has been truncated away; except
    that "where the oldest record in the log is at the time requested as
    start, that record must not be sent". A ``start`` not yet elapsed starts
    from the next record, one of the two options §6.3.4 allows.
    """
    if start_ns is None or start_ns > now_ns:
        return stream.last_logged_ns
    if start_ns == stream.log.oldest_ns():
        return start_ns
    return start_ns - 1


async def _next_request(requests: AsyncIterator[gnmi_pb2.SubscribeRequest]) -> Any:
    """The client's next message, or None once it half-closes."""
    try:
        return await anext(requests)
    except StopAsyncIteration:
        return None


class TapiGnmiServicer(gnmi_pb2_grpc.gNMIServicer):
    """Capabilities and the TR-548 Subscribe; Get and Set stay UNIMPLEMENTED."""

    def __init__(self, stream: MeasurementStream) -> None:
        self._stream = stream

    async def Capabilities(
        self,
        request: gnmi_pb2.CapabilityRequest,
        context: grpc.aio.ServicerContext,
    ) -> gnmi_pb2.CapabilityResponse:
        return gnmi_pb2.CapabilityResponse(
            supported_models=SUPPORTED_MODELS,
            supported_encodings=[gnmi_pb2.Encoding.PROTO],
            gNMI_version=GNMI_VERSION,
        )

    async def Get(
        self, request: gnmi_pb2.GetRequest, context: grpc.aio.ServicerContext
    ) -> gnmi_pb2.GetResponse:
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "Get is not implemented: read T-API state over RESTCONF",
        )
        raise AssertionError("unreachable")  # pragma: no cover

    async def Set(
        self, request: gnmi_pb2.SetRequest, context: grpc.aio.ServicerContext
    ) -> gnmi_pb2.SetResponse:
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "Set is not implemented: configure the twin over RESTCONF",
        )
        raise AssertionError("unreachable")  # pragma: no cover

    async def Subscribe(
        self,
        request_iterator: AsyncIterator[gnmi_pb2.SubscribeRequest],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[gnmi_pb2.SubscribeResponse]:
        requests = aiter(request_iterator)
        first = await _next_request(requests)
        if first is None:
            return
        try:
            start_ns = validate_subscribe_request(first)
        except SubscriptionError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            return

        stream = self._stream
        cursor = initial_cursor(stream, start_ns, stream.now_ns())
        # Replayed history first, then sync_response; with no history the
        # sync_response comes at once (updates_only: no initial state).
        if start_ns is not None:
            for record in stream.log.replay(cursor + 1):
                yield record.response
                cursor = record.logged_ns
        yield gnmi_pb2.SubscribeResponse(sync_response=True)

        # A STREAM subscription takes one request. Watch for more so a stray
        # poll is rejected rather than silently ignored; a half-close is fine.
        extra: asyncio.Task[Any] | None = asyncio.create_task(_next_request(requests))
        pending: asyncio.Task[Any] | None = None
        try:
            while True:
                pending = asyncio.create_task(stream.records_after(cursor))
                waiting: set[asyncio.Task[Any]] = {pending}
                if extra is not None:
                    waiting.add(extra)
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                if extra is not None and extra in done:
                    if extra.result() is not None:
                        await context.abort(
                            grpc.StatusCode.INVALID_ARGUMENT,
                            "request: a STREAM subscription accepts a single "
                            "SubscribeRequest (TR-548 §6.3.4)",
                        )
                        return
                    extra = None
                if pending not in done:
                    pending.cancel()
                    continue
                for record in pending.result():
                    yield record.response
                    cursor = record.logged_ns
        except asyncio.CancelledError:
            # The client went away or the server is stopping. Ending quietly
            # keeps grpc from logging a normal disconnect as an error.
            logger.debug("Subscribe stream closed")
        finally:
            for task in (pending, extra):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task


# -- Authentication ------------------------------------------------------------


def _bearer_matches(metadata: Any, token: str) -> bool:
    """``authorization: Bearer <token>``; the scheme is case-insensitive (RFC 6750)."""
    for key, value in metadata or ():
        if key == "authorization":
            scheme, _, credential = str(value).partition(" ")
            return scheme.lower() == "bearer" and hmac.compare_digest(
                credential.strip().encode(), token.encode()
            )
    return False


class BearerTokenInterceptor(grpc.aio.ServerInterceptor):
    """Reject every call without the configured bearer token (UNAUTHENTICATED).

    TR-548 ST-0.1 has the client obtain a token before connecting; the twin's
    token is pre-shared through ``streaming.auth_token``.
    """

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("an empty token disables authentication; do not install")
        self._token = token

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        if handler is None or _bearer_matches(
            handler_call_details.invocation_metadata, self._token
        ):
            return handler
        return _denying(handler)


_DENIED = "missing or invalid 'authorization: Bearer <token>' metadata"


def _denying(handler: grpc.RpcMethodHandler) -> grpc.RpcMethodHandler:
    """A handler of the same shape as ``handler`` that aborts UNAUTHENTICATED."""

    async def deny_unary(request: Any, context: grpc.aio.ServicerContext) -> Any:
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, _DENIED)

    async def deny_stream(request: Any, context: grpc.aio.ServicerContext) -> Any:
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, _DENIED)
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
