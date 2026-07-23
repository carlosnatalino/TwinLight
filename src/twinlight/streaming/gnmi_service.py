"""gNMI Subscribe servicer — stateless frontend to FastAPI."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

import grpc
import httpx

from twinlight.streaming.proto import gnmi_pb2, gnmi_pb2_grpc

logger = logging.getLogger(__name__)

# Mapping of gNMI path elements to REST API paths.
# OPM paths carry a per-service UUID, so they are resolved dynamically in
# _gnmi_path_to_rest() rather than via this static map.
_PATH_MAP: dict[str, str] = {
    "tapi-common:context": "/data/tapi-common:context",
    "tapi-topology:topology-context": (
        "/data/tapi-common:context/tapi-topology:topology-context"
    ),
}


def _gnmi_path_to_rest(path: gnmi_pb2.Path) -> str:
    """Convert a gNMI Path to a REST API URL path.

    Supports paths like:
      /tapi-common:context
      /tapi-common:context/tapi-topology:topology-context
      /tapi-common:context/tapi-topology:topology-context/topology[uuid=X]
      /tapi-connectivity:connectivity-context/connectivity-service[uuid=X]/opm
        → live, transient-aware OPM measurements for service X
      /opm  → OPM measurements for every service
    """
    parts: list[str] = []
    service_uuid: str | None = None
    for elem in path.elem:
        name = elem.name
        if elem.key:
            # e.g. topology[uuid=X] → topology=X
            key_val = next(iter(elem.key.values()), "")
            if name == "connectivity-service":
                service_uuid = key_val
            parts.append(f"{name}={key_val}")
        else:
            parts.append(name)

    # Live OPM — signal quality WITH transient fluctuations. The internal
    # OPM endpoint composes the four transient models (EDFA reservoir,
    # polarization, phase noise, environmental) on top of the GNPy QoT
    # baseline, so each gNMI sample reflects the instantaneous signal
    # quality rather than a static baseline.
    if parts and parts[-1] == "opm":
        return f"/internal/opm/{service_uuid}" if service_uuid else "/internal/opm"

    joined = "/".join(parts)

    # Try the static map first
    if joined in _PATH_MAP:
        return _PATH_MAP[joined]

    # Build hierarchical path
    return "/data/" + joined


def _build_notification(
    path: gnmi_pb2.Path, json_bytes: bytes
) -> gnmi_pb2.Notification:
    """Build a gNMI Notification with a single JSON_IETF update."""
    update = gnmi_pb2.Update(
        path=path,
        val=gnmi_pb2.TypedValue(json_ietf_val=json_bytes),
    )
    return gnmi_pb2.Notification(
        timestamp=int(time.time() * 1e9),
        update=[update],
    )


class GnmiServicer(gnmi_pb2_grpc.gNMIServicer):
    """gNMI server that proxies Subscribe requests to the FastAPI app."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    async def Capabilities(
        self,
        request: gnmi_pb2.CapabilityRequest,
        context: grpc.aio.ServicerContext,
    ) -> gnmi_pb2.CapabilityResponse:
        return gnmi_pb2.CapabilityResponse(
            supported_models=[
                gnmi_pb2.ModelData(
                    name="tapi-common",
                    organization="ONF",
                    version="2.4.0",
                ),
                gnmi_pb2.ModelData(
                    name="tapi-topology",
                    organization="ONF",
                    version="2.4.0",
                ),
            ],
            supported_encodings=[gnmi_pb2.Encoding.JSON_IETF],
            gNMI_version="0.10.0",
        )

    async def Subscribe(
        self,
        request_iterator: AsyncIterator[gnmi_pb2.SubscribeRequest],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[gnmi_pb2.SubscribeResponse]:
        """Handle Subscribe RPC (STREAM/ONCE/POLL modes)."""
        try:
            async for request in request_iterator:
                if request.HasField("subscribe"):
                    sub_list = request.subscribe
                    mode = sub_list.mode

                    if mode == gnmi_pb2.SubscriptionList.ONCE:
                        async for resp in self._handle_once(sub_list):
                            yield resp
                        return

                    elif mode == gnmi_pb2.SubscriptionList.STREAM:
                        async for resp in self._handle_stream(
                            sub_list, context
                        ):
                            yield resp

                    elif mode == gnmi_pb2.SubscriptionList.POLL:
                        # For POLL, first subscribe sets up paths, then each
                        # poll message triggers a new fetch
                        async for resp in self._handle_once(sub_list):
                            yield resp

                elif request.HasField("poll"):
                    # Re-fetch using the last subscription's paths
                    # (simplified: respond with full context)
                    path = gnmi_pb2.Path(
                        elem=[gnmi_pb2.PathElem(name="tapi-common:context")]
                    )
                    data = await self._fetch_rest(
                        "/data/tapi-common:context"
                    )
                    if data is not None:
                        notification = _build_notification(path, data)
                        yield gnmi_pb2.SubscribeResponse(update=notification)
                    yield gnmi_pb2.SubscribeResponse(sync_response=True)

        except asyncio.CancelledError:
            logger.debug("Subscribe stream cancelled by client")

    async def _handle_once(
        self, sub_list: gnmi_pb2.SubscriptionList
    ) -> AsyncIterator[gnmi_pb2.SubscribeResponse]:
        """Fetch data once for all subscribed paths, then send sync."""
        for sub in sub_list.subscription:
            rest_path = _gnmi_path_to_rest(sub.path)
            data = await self._fetch_rest(rest_path)
            if data is not None:
                notification = _build_notification(sub.path, data)
                yield gnmi_pb2.SubscribeResponse(update=notification)
        yield gnmi_pb2.SubscribeResponse(sync_response=True)

    async def _handle_stream(
        self,
        sub_list: gnmi_pb2.SubscriptionList,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[gnmi_pb2.SubscribeResponse]:
        """SAMPLE-mode streaming: periodically poll REST API."""
        # Initial sync
        async for resp in self._handle_once(sub_list):
            yield resp

        # Determine sample interval (default 10s)
        interval_ns = 10_000_000_000  # 10s in nanoseconds
        if sub_list.subscription:
            si = sub_list.subscription[0].sample_interval
            if si > 0:
                interval_ns = si
        interval_s = interval_ns / 1e9

        # Periodic updates
        while not context.cancelled():
            await asyncio.sleep(interval_s)
            if context.cancelled():
                break
            for sub in sub_list.subscription:
                rest_path = _gnmi_path_to_rest(sub.path)
                data = await self._fetch_rest(rest_path)
                if data is not None:
                    notification = _build_notification(sub.path, data)
                    yield gnmi_pb2.SubscribeResponse(update=notification)

    async def _fetch_rest(self, path: str) -> bytes | None:
        """Fetch JSON from the FastAPI app via in-process HTTP."""
        try:
            resp = await self._http.get(path)
            if resp.status_code == 200:
                return resp.content
            logger.warning("REST %s returned %d", path, resp.status_code)
            return None
        except Exception:
            logger.exception("Error fetching %s", path)
            return None
