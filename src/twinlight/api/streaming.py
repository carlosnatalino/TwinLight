"""TAPI Streaming discovery (tapi-streaming v2.6.0) — TR-548 §3.7, ST-0.2.

GET .../tapi-streaming:stream-context
GET .../tapi-streaming:stream-context/supported-stream-type
GET .../tapi-streaming:stream-context/supported-stream-type={uuid}
GET .../tapi-streaming:stream-context/available-stream
GET .../tapi-streaming:stream-context/available-stream={uuid}

T-API only: every key is a leaf of ``tapi-streaming:stream-context`` in the
v2.6.0 YANG, and every identity value is fully qualified. The twin offers
exactly the supported-stream-type TR-548 §6.2.2 makes mandatory for
performance data, and one available-stream of it on the gRPC port.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from twinlight.streaming.measurement_stream import (
    AVAILABLE_STREAM_UUID,
    SUPPORTED_STREAM_TYPE_UUID,
    MeasurementStream,
)

PREFIX = "/data/tapi-common:context/tapi-streaming:stream-context"

router = APIRouter(prefix=PREFIX, tags=["tapi-streaming"])

CONNECTION_PROTOCOL_GNMI = "tapi-streaming:CONNECTION_PROTOCOL_GNMI"


def supported_stream_type(stream: MeasurementStream, config: Any) -> dict:
    """TR-548 §6.2.2: the supported-stream-type a conforming provider MUST offer.

    log-record-strategy is stated although WHOLE_ENTITY is its default, so a
    client can match all six §6.2.2 properties without knowing YANG defaults.
    information-record-strategy is omitted ("not required to be used").
    """
    return {
        "uuid": SUPPORTED_STREAM_TYPE_UUID,
        "stream-type-name": "measurement-details",
        # "Time in minutes" (tapi-streaming.yang); a string leaf.
        "record-retention": str(config.streaming.record_retention_min),
        "stream-type-content": [
            "tapi-gnmi-streaming:GNMI_STREAMING_OBJECT_TYPE_MEASUREMENT_DETAILS"
        ],
        "log-storage-strategy": "tapi-streaming:LOG_STORAGE_STRATEGY_TRUNCATED",
        "log-record-strategy": "tapi-streaming:LOG_RECORD_STRATEGY_WHOLE_ENTITY",
        "record-trigger": "tapi-streaming:RECORD_TRIGGER_PERIODIC",
        "connection-protocol-details": {
            "allowed-connection-protocols": [CONNECTION_PROTOCOL_GNMI],
            "encoding-format": "tapi-streaming:ENCODING_FORMAT_PROTOBUF",
        },
    }


def available_stream(stream: MeasurementStream) -> dict:
    """The one running stream: where to connect and whether it is streaming."""
    return {
        "uuid": AVAILABLE_STREAM_UUID,
        "connection-address": [stream.connection_address],
        "stream-state": stream.state.value,
        "supported-stream-type": {
            "supported-stream-type-uuid": SUPPORTED_STREAM_TYPE_UUID,
        },
        "connection-protocol": CONNECTION_PROTOCOL_GNMI,
    }


def stream_context_payload(request: Request) -> dict:
    stream = request.app.state.measurement_stream
    return {
        "supported-stream-type": [supported_stream_type(stream, request.app.state.config)],
        "available-stream": [available_stream(stream)],
    }


@router.get("")
async def get_stream_context(request: Request) -> dict:
    return {"tapi-streaming:stream-context": stream_context_payload(request)}


@router.get("/supported-stream-type")
async def get_supported_stream_types(request: Request) -> dict:
    return {
        "tapi-streaming:supported-stream-type":
            stream_context_payload(request)["supported-stream-type"]
    }


@router.get("/supported-stream-type={uuid}")
async def get_supported_stream_type(uuid: str, request: Request) -> dict:
    if uuid != SUPPORTED_STREAM_TYPE_UUID:
        raise HTTPException(status_code=404, detail=f"Supported stream type {uuid} not found")
    return await get_supported_stream_types(request)


@router.get("/available-stream")
async def get_available_streams(request: Request) -> dict:
    return {
        "tapi-streaming:available-stream":
            stream_context_payload(request)["available-stream"]
    }


@router.get("/available-stream={uuid}")
async def get_available_stream(uuid: str, request: Request) -> dict:
    if uuid != AVAILABLE_STREAM_UUID:
        raise HTTPException(status_code=404, detail=f"Available stream {uuid} not found")
    return await get_available_streams(request)
