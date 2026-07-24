"""gNMI streaming client for the T-API Digital Twin."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Self

import grpc

from twinlight.streaming.proto import gnmi_pb2, gnmi_pb2_grpc

logger = logging.getLogger(__name__)


class GnmiConsumer:
    """Async gNMI Subscribe consumer that yields decoded JSON responses."""

    def __init__(self, target: str = "localhost:50051") -> None:
        self._target = target
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

    async def capabilities(self) -> dict[str, Any]:
        """Fetch gNMI Capabilities."""
        assert self._stub is not None, "Call connect() first"
        resp = await self._stub.Capabilities(gnmi_pb2.CapabilityRequest())
        return {
            "supported_models": [
                {"name": m.name, "organization": m.organization, "version": m.version}
                for m in resp.supported_models
            ],
            "supported_encodings": [
                gnmi_pb2.Encoding.Name(e) for e in resp.supported_encodings
            ],
            "gnmi_version": resp.gNMI_version,
        }

    async def subscribe_once(
        self, paths: list[str]
    ) -> list[dict[str, Any]]:
        """Send a ONCE subscription and return all updates."""
        assert self._stub is not None, "Call connect() first"

        sub_list = gnmi_pb2.SubscriptionList(
            mode=gnmi_pb2.SubscriptionList.ONCE,
            encoding=gnmi_pb2.Encoding.JSON_IETF,
            subscription=[
                gnmi_pb2.Subscription(
                    path=_parse_path(p),
                    mode=gnmi_pb2.SubscriptionMode.TARGET_DEFINED,
                )
                for p in paths
            ],
        )

        request = gnmi_pb2.SubscribeRequest(subscribe=sub_list)

        results: list[dict[str, Any]] = []
        async for resp in self._stub.Subscribe(iter([request])):
            if resp.HasField("update"):
                for update in resp.update.update:
                    if update.val.json_ietf_val:
                        data = json.loads(update.val.json_ietf_val)
                        results.append(data)
            elif resp.sync_response:
                break

        return results

    async def subscribe_stream(
        self,
        paths: list[str],
        sample_interval_s: float = 10.0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Open a STREAM subscription yielding decoded JSON updates."""
        assert self._stub is not None, "Call connect() first"

        interval_ns = int(sample_interval_s * 1e9)
        sub_list = gnmi_pb2.SubscriptionList(
            mode=gnmi_pb2.SubscriptionList.STREAM,
            encoding=gnmi_pb2.Encoding.JSON_IETF,
            subscription=[
                gnmi_pb2.Subscription(
                    path=_parse_path(p),
                    mode=gnmi_pb2.SubscriptionMode.SAMPLE,
                    sample_interval=interval_ns,
                )
                for p in paths
            ],
        )

        request = gnmi_pb2.SubscribeRequest(subscribe=sub_list)

        async for resp in self._stub.Subscribe(iter([request])):
            if resp.HasField("update"):
                for update in resp.update.update:
                    if update.val.json_ietf_val:
                        data = json.loads(update.val.json_ietf_val)
                        yield data


def _parse_path(path_str: str) -> gnmi_pb2.Path:
    """Parse a simple slash-separated path string into a gNMI Path.

    Example: "tapi-common:context/tapi-topology:topology-context"
    """
    elems = []
    for part in path_str.strip("/").split("/"):
        if "[" in part and "]" in part:
            name, rest = part.split("[", 1)
            key_str = rest.rstrip("]")
            key, val = key_str.split("=", 1)
            elems.append(gnmi_pb2.PathElem(name=name, key={key: val}))
        else:
            elems.append(gnmi_pb2.PathElem(name=part))
    return gnmi_pb2.Path(elem=elems)
