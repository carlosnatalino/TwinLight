"""Client for the twin's T-API performance-measurement stream (TR-548 §6).

The flow TR-548 prescribes:

1. ST-0.1 — have the bearer token, if the twin requires one.
2. ST-0.2 — discover the stream over RESTCONF: find the supported-stream-type
   that carries measurement-details over gNMI as protobuf, and the
   available-stream of it, whose ``connection-address`` says where to connect.
3. ST-6.2 — subscribe with the §6.3.4 subscription and decode each record's
   ``proto_bytes`` as a ``tapi.MeasurementDetails``.
4. ST-6.4 — after a lost connection, resubscribe with the history
   extension's ``range.start`` at the last processed record's timestamp.

The stream carries every OAM job; a client interested in one connectivity
service filters on that service's oam-job UUID.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import urlsplit

import grpc

from twinlight.streaming.proto import gnmi_ext_pb2, gnmi_pb2, gnmi_pb2_grpc
from twinlight.streaming.proto import tapi_streaming_performance_pb2 as tapi
from twinlight.streaming.records import GNMI_TARGET, decode_measurements, job_uuid_of
from twinlight_client.client import TapiClient

logger = logging.getLogger(__name__)

MEASUREMENT_DETAILS = "tapi-gnmi-streaming:GNMI_STREAMING_OBJECT_TYPE_MEASUREMENT_DETAILS"
CONNECTION_PROTOCOL_GNMI = "tapi-streaming:CONNECTION_PROTOCOL_GNMI"
ENCODING_FORMAT_PROTOBUF = "tapi-streaming:ENCODING_FORMAT_PROTOBUF"
STREAM_STATE_ACTIVE = "tapi-streaming:STREAM_STATE_ACTIVE"

# Status codes worth a resubscription once the stream has been reached;
# anything else (a rejected subscription, a wrong token) would fail the same
# way again.
_TRANSIENT = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.CANCELLED, grpc.StatusCode.UNKNOWN}


class StreamDiscoveryError(RuntimeError):
    """The twin offers no stream this client can consume."""


class StreamUnreachableError(RuntimeError):
    """The stream at ``target`` never answered a subscription: the address
    cannot be reached from here (``UNAVAILABLE`` before any response).

    Distinct from a connection lost after the server has answered, which
    :meth:`TapiStreamClient.follow` retries. ``rpc_error`` is the underlying
    ``grpc.aio.AioRpcError``.
    """

    def __init__(self, target: str, rpc_error: grpc.aio.AioRpcError) -> None:
        self.target = target
        self.rpc_error = rpc_error
        super().__init__(
            f"cannot reach the measurement stream at {target}: "
            f"{rpc_error.code().name}: {rpc_error.details()}"
        )

    @property
    def code(self) -> grpc.StatusCode:
        return self.rpc_error.code()

    @property
    def details(self) -> str:
        return self.rpc_error.details() or ""


def unreachable_message(
    error: StreamUnreachableError, connection_address: str | None = None
) -> str:
    """What to tell a user whose stream cannot be reached.

    ``connection_address`` is the address the twin advertised when that is
    what was tried, None when the user's ``--gnmi-target`` overrode it. Only
    the advertised case gets the remedies: an explicit target is the user's
    own to fix.
    """
    reason = f"{error.code.name}: {error.details}"
    if connection_address is None:
        return (
            f"Cannot reach the measurement stream at {error.target} "
            f"(from --gnmi-target): {reason}"
        )
    return (
        f"Cannot reach the measurement stream at {error.target}, the "
        f"connection-address the twin advertises ({connection_address}): {reason}\n"
        "Pass --gnmi-target HOST:PORT to connect elsewhere, or set "
        "streaming.advertised_address on the twin to an address clients can reach."
    )


@dataclass(frozen=True)
class StreamEndpoint:
    """An available-stream of the TR-548 measurement-details stream type."""

    uuid: str
    connection_address: str
    stream_state: str

    @property
    def target(self) -> str:
        """``host:port`` for a gRPC channel, from ``grpc://host:port``."""
        parts = urlsplit(self.connection_address)
        if parts.scheme != "grpc" or not parts.hostname or parts.port is None:
            raise StreamDiscoveryError(
                f"connection-address {self.connection_address!r} is not grpc://<host>:<port>"
            )
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        return f"{host}:{parts.port}"


def _is_measurement_stream_type(sst: dict[str, Any]) -> bool:
    """TR-548 §6.2.2: measurement-details, TRUNCATED, PERIODIC, GNMI, PROTOBUF."""
    details = sst.get("connection-protocol-details") or {}
    return (
        MEASUREMENT_DETAILS in sst.get("stream-type-content", [])
        and sst.get("log-storage-strategy") == "tapi-streaming:LOG_STORAGE_STRATEGY_TRUNCATED"
        and sst.get("record-trigger") == "tapi-streaming:RECORD_TRIGGER_PERIODIC"
        and CONNECTION_PROTOCOL_GNMI in details.get("allowed-connection-protocols", [])
        and details.get("encoding-format") == ENCODING_FORMAT_PROTOBUF
    )


def select_stream(stream_context: dict[str, Any]) -> StreamEndpoint:
    """ST-0.2: pick the gNMI available-stream of the measurement stream type."""
    type_uuids = {
        sst["uuid"]
        for sst in stream_context.get("supported-stream-type", [])
        if _is_measurement_stream_type(sst)
    }
    if not type_uuids:
        raise StreamDiscoveryError("no supported-stream-type for measurement-details over gNMI")
    for stream in stream_context.get("available-stream", []):
        ref = (stream.get("supported-stream-type") or {}).get("supported-stream-type-uuid")
        addresses = stream.get("connection-address") or []
        if (
            ref in type_uuids
            and stream.get("connection-protocol") == CONNECTION_PROTOCOL_GNMI
            and addresses
        ):
            return StreamEndpoint(stream["uuid"], addresses[0], stream.get("stream-state", ""))
    raise StreamDiscoveryError("no available-stream of the measurement-details stream type")


async def discover_stream(rest: TapiClient) -> StreamEndpoint:
    """Read the stream-context over RESTCONF and select the stream."""
    return select_stream(await rest.get_stream_context())


async def oam_job_for_service(rest: TapiClient, service_uuid: str) -> str:
    """The UUID of the oam-job whose records measure ``service_uuid``."""
    for job in await rest.get_oam_jobs():
        ref = job.get("connectivity-service-end-point") or {}
        if ref.get("connectivity-service-uuid") == service_uuid:
            return str(job["uuid"])
    raise StreamDiscoveryError(f"no oam-job references connectivity service {service_uuid}")


def subscribe_request(start_ns: int | None = None) -> gnmi_pb2.SubscribeRequest:
    """The TR-548 §6.3.4 subscription; ``start_ns`` resumes per ST-6.4."""
    request = gnmi_pb2.SubscribeRequest(
        subscribe=gnmi_pb2.SubscriptionList(
            prefix=gnmi_pb2.Path(target=GNMI_TARGET),
            subscription=[
                gnmi_pb2.Subscription(
                    path=gnmi_pb2.Path(),
                    mode=gnmi_pb2.SubscriptionMode.TARGET_DEFINED,
                )
            ],
            mode=gnmi_pb2.SubscriptionList.STREAM,
            updates_only=True,
        )
    )
    if start_ns is not None:
        request.extension.append(
            gnmi_ext_pb2.Extension(
                history=gnmi_ext_pb2.History(range=gnmi_ext_pb2.TimeRange(start=start_ns))
            )
        )
    return request


@dataclass(frozen=True)
class MeasurementRecord:
    """One decoded stream record."""

    job_uuid: str
    logged_ns: int
    """Notification timestamp: when the twin logged the record (resume key)."""
    sampled_ms: int
    """time-measurement-was-sampled, ms since the epoch."""
    measurements: dict[str, float | None]
    """By OPM key (``gsnr-db``, ``pre-fec-ber``, ...); None where MISSING."""
    details: tapi.MeasurementDetails = field(repr=False)


def decode_record(response: gnmi_pb2.SubscribeResponse) -> list[MeasurementRecord]:
    """The records in one SubscribeResponse (none for a sync_response)."""
    if not response.HasField("update"):
        return []
    out = []
    for update in response.update.update:
        job = job_uuid_of(update.path)
        if job is None or update.val.WhichOneof("value") != "proto_bytes":
            continue
        details = tapi.MeasurementDetails.FromString(update.val.proto_bytes)
        out.append(MeasurementRecord(
            job_uuid=job,
            logged_ns=response.update.timestamp,
            sampled_ms=details.time_measurement_was_sampled,
            measurements=decode_measurements(details),
            details=details,
        ))
    return out


class TapiStreamClient:
    """Subscribes to the measurement stream at ``target`` (``host:port``)."""

    def __init__(self, target: str, token: str | None = None) -> None:
        self._target = target
        self._metadata = (("authorization", f"Bearer {token}"),) if token else None
        self._channel: grpc.aio.Channel | None = None
        self._stub: gnmi_pb2_grpc.gNMIStub | None = None

    async def connect(self) -> None:
        self._channel = grpc.aio.insecure_channel(self._target)
        self._stub = gnmi_pb2_grpc.gNMIStub(self._channel)

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._stub = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _require_stub(self) -> gnmi_pb2_grpc.gNMIStub:
        if self._stub is None:
            raise RuntimeError("call connect() first")
        return self._stub

    async def capabilities(self) -> dict[str, Any]:
        resp = await self._require_stub().Capabilities(
            gnmi_pb2.CapabilityRequest(), metadata=self._metadata
        )
        return {
            "supported_models": [
                {"name": m.name, "organization": m.organization, "version": m.version}
                for m in resp.supported_models
            ],
            "supported_encodings": [gnmi_pb2.Encoding.Name(e) for e in resp.supported_encodings],
            "gnmi_version": resp.gNMI_version,
        }

    async def _responses(
        self, start_ns: int | None
    ) -> AsyncIterator[gnmi_pb2.SubscribeResponse]:
        """One subscription's raw responses, ``sync_response`` included.

        ``UNAVAILABLE`` before the first response raises
        :class:`StreamUnreachableError`; every other failure is the
        ``grpc.aio.AioRpcError`` itself.
        """
        hold = asyncio.Event()

        async def requests() -> AsyncIterator[gnmi_pb2.SubscribeRequest]:
            yield subscribe_request(start_ns)
            await hold.wait()  # keep the send side open while subscribed

        call = self._require_stub().Subscribe(requests(), metadata=self._metadata)
        answered = False
        try:
            async for response in call:
                answered = True
                yield response
        except grpc.aio.AioRpcError as exc:
            if not answered and exc.code() == grpc.StatusCode.UNAVAILABLE:
                raise StreamUnreachableError(self._target, exc) from exc
            raise
        finally:
            hold.set()
            call.cancel()

    async def records(
        self, *, start_ns: int | None = None, job_uuid: str | None = None
    ) -> AsyncIterator[MeasurementRecord]:
        """One subscription's records, optionally of one oam-job only.

        Raises :class:`StreamUnreachableError` if the stream cannot be reached
        at all, ``grpc.aio.AioRpcError`` for any other failure; nothing is
        retried.
        """
        async for response in self._responses(start_ns):
            for record in decode_record(response):
                if job_uuid is None or record.job_uuid == job_uuid:
                    yield record

    async def follow(
        self,
        *,
        job_uuid: str | None = None,
        retry_delay_s: float = 1.0,
        max_retries: int | None = None,
    ) -> AsyncIterator[MeasurementRecord]:
        """Records across reconnections, resuming without loss (ST-6.4).

        The subscription counts as established once the server has answered
        it at all; the twin sends a ``sync_response`` right after a subscribe,
        so a stream with no records yet for ``job_uuid`` is established too.

        Not retried: a failure before the subscription is established. A
        stream that cannot be reached at the first connection raises
        :class:`StreamUnreachableError` at once (a wrong address is not a
        dropped connection); any other early failure raises its
        ``grpc.aio.AioRpcError``. A non-transient status (a rejected
        subscription, a wrong token) is never retried either.

        Retried: once established, a lost connection (``UNAVAILABLE``,
        ``CANCELLED``, ``UNKNOWN``, or a server that cannot be reached while
        it is down), every ``retry_delay_s``, up to ``max_retries`` failures in
        a row (None: without end; the count restarts whenever the server
        answers again). When retries run out, the last ``grpc.aio.AioRpcError``
        is raised. Each resubscription starts at the timestamp of the last
        record yielded; the twin replays from that time inclusive, so records
        already seen at that timestamp are dropped here.
        """
        last_ns: int | None = None
        seen_at_last: set[str] = set()
        established = False
        failures = 0
        while True:
            try:
                async for response in self._responses(last_ns):
                    established = True
                    failures = 0
                    for record in decode_record(response):
                        if job_uuid is not None and record.job_uuid != job_uuid:
                            continue
                        if last_ns is not None and (
                            record.logged_ns < last_ns
                            or (record.logged_ns == last_ns and record.job_uuid in seen_at_last)
                        ):
                            continue  # already yielded before the reconnection
                        if record.logged_ns != last_ns:
                            last_ns, seen_at_last = record.logged_ns, set()
                        seen_at_last.add(record.job_uuid)
                        yield record
                return
            except StreamUnreachableError as exc:
                if not established:
                    raise
                error = exc.rpc_error  # the server is down: a lost connection
            except grpc.aio.AioRpcError as exc:
                if not established:
                    raise
                error = exc
            if error.code() not in _TRANSIENT:
                raise error
            failures += 1
            if max_retries is not None and failures > max_retries:
                raise error
            logger.warning(
                "stream interrupted (%s); resubscribing in %.1f s %s",
                error.code().name, retry_delay_s,
                "for new records" if last_ns is None else f"from {last_ns}",
            )
            await asyncio.sleep(retry_delay_s)
