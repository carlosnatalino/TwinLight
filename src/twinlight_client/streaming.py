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

# Status codes worth a resubscription; anything else (a rejected subscription,
# a wrong token) would fail the same way again.
_TRANSIENT = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.CANCELLED, grpc.StatusCode.UNKNOWN}


class StreamDiscoveryError(RuntimeError):
    """The twin offers no stream this client can consume."""


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

    async def records(
        self, *, start_ns: int | None = None, job_uuid: str | None = None
    ) -> AsyncIterator[MeasurementRecord]:
        """One subscription's records, optionally of one oam-job only."""
        hold = asyncio.Event()

        async def requests() -> AsyncIterator[gnmi_pb2.SubscribeRequest]:
            yield subscribe_request(start_ns)
            await hold.wait()  # keep the send side open while subscribed

        call = self._require_stub().Subscribe(requests(), metadata=self._metadata)
        try:
            async for response in call:
                for record in decode_record(response):
                    if job_uuid is None or record.job_uuid == job_uuid:
                        yield record
        finally:
            hold.set()
            call.cancel()

    async def follow(
        self,
        *,
        job_uuid: str | None = None,
        retry_delay_s: float = 1.0,
        max_retries: int | None = None,
    ) -> AsyncIterator[MeasurementRecord]:
        """Records across reconnections, resuming without loss (ST-6.4).

        After a transient failure it resubscribes from the timestamp of the
        last record it yielded. The twin replays from that time inclusive,
        so records already seen at that timestamp are dropped here.
        """
        last_ns: int | None = None
        seen_at_last: set[str] = set()
        failures = 0
        while True:
            try:
                async for record in self.records(start_ns=last_ns, job_uuid=job_uuid):
                    failures = 0
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
            except grpc.aio.AioRpcError as exc:
                if exc.code() not in _TRANSIENT:
                    raise
                failures += 1
                if max_retries is not None and failures > max_retries:
                    raise
                logger.warning(
                    "stream interrupted (%s); resubscribing in %.1f s from %s",
                    exc.code().name, retry_delay_s, last_ns,
                )
                await asyncio.sleep(retry_delay_s)
