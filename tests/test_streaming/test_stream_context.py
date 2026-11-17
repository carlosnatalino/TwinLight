"""tapi-streaming discovery: the stream-context a client reads before subscribing."""

from __future__ import annotations

import asyncio
import uuid as uuid_lib

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.config import TwinConfig
from twinlight.streaming.measurement_stream import (
    AVAILABLE_STREAM_UUID,
    SUPPORTED_STREAM_TYPE_UUID,
)

_SC = "/data/tapi-common:context/tapi-streaming:stream-context"

# Leaves of tapi-streaming:stream-context in the v2.6.0 YANG
# (TAPI v2.6.0 YANG/tapi-streaming.tree). Anything outside these sets would
# be a non-standard key in a /data/ response.
STREAM_CONTEXT_KEYS = {"available-stream", "supported-stream-type"}
SUPPORTED_STREAM_TYPE_KEYS = {
    "stream-type-name", "record-retention", "segment-size", "stream-type-content",
    "log-storage-strategy", "log-record-strategy", "record-trigger", "uuid", "name",
    "compacted-log-details", "connection-protocol-details", "information-record-strategy",
}
CONNECTION_PROTOCOL_DETAILS_KEYS = {"allowed-connection-protocols", "encoding-format"}
AVAILABLE_STREAM_KEYS = {
    "connection-address", "stream-state", "supported-stream-type", "stream-id",
    "connection-protocol", "uuid", "name",
}
SST_REF_KEYS = {"supported-stream-type-uuid"}


def _context(app: TestClient) -> dict:
    return app.get(_SC).json()["tapi-streaming:stream-context"]


def test_keys_are_yang_leaves(app: TestClient) -> None:
    ctx = _context(app)
    assert set(ctx) == STREAM_CONTEXT_KEYS
    [sst] = ctx["supported-stream-type"]
    assert set(sst) <= SUPPORTED_STREAM_TYPE_KEYS
    assert set(sst["connection-protocol-details"]) == CONNECTION_PROTOCOL_DETAILS_KEYS
    [stream] = ctx["available-stream"]
    assert set(stream) <= AVAILABLE_STREAM_KEYS
    assert set(stream["supported-stream-type"]) == SST_REF_KEYS


def test_supported_stream_type_is_the_tr548_mandatory_one(app: TestClient) -> None:
    """TR-548 §6.2.2: the supported-stream-type a provider MUST support."""
    [sst] = _context(app)["supported-stream-type"]
    assert sst["stream-type-content"] == [
        "tapi-gnmi-streaming:GNMI_STREAMING_OBJECT_TYPE_MEASUREMENT_DETAILS"
    ]
    assert sst["log-record-strategy"] == "tapi-streaming:LOG_RECORD_STRATEGY_WHOLE_ENTITY"
    assert sst["record-trigger"] == "tapi-streaming:RECORD_TRIGGER_PERIODIC"
    assert sst["log-storage-strategy"] == "tapi-streaming:LOG_STORAGE_STRATEGY_TRUNCATED"
    assert sst["record-retention"] == "5"
    assert sst["connection-protocol-details"] == {
        "allowed-connection-protocols": ["tapi-streaming:CONNECTION_PROTOCOL_GNMI"],
        "encoding-format": "tapi-streaming:ENCODING_FORMAT_PROTOBUF",
    }
    assert "information-record-strategy" not in sst
    assert "compacted-log-details" not in sst


def test_available_stream_references_the_type(app: TestClient) -> None:
    ctx = _context(app)
    [stream] = ctx["available-stream"]
    assert stream["supported-stream-type"]["supported-stream-type-uuid"] == (
        ctx["supported-stream-type"][0]["uuid"]
    )
    assert stream["connection-protocol"] == "tapi-streaming:CONNECTION_PROTOCOL_GNMI"


def test_uuids_are_name_based_and_stable(twin_config: TwinConfig) -> None:
    first = _context(TestClient(create_app(twin_config)))
    second = _context(TestClient(create_app(twin_config)))
    assert first == second
    assert first["supported-stream-type"][0]["uuid"] == SUPPORTED_STREAM_TYPE_UUID
    assert first["available-stream"][0]["uuid"] == AVAILABLE_STREAM_UUID
    for value in (SUPPORTED_STREAM_TYPE_UUID, AVAILABLE_STREAM_UUID):
        assert uuid_lib.UUID(value).version == 5


def test_connection_address_and_retention_follow_config(twin_config: TwinConfig) -> None:
    twin_config.server.grpc_port = 50099
    twin_config.streaming.advertised_address = "grpc://twin.example.org:6000"
    twin_config.streaming.record_retention_min = 60
    ctx = _context(TestClient(create_app(twin_config)))
    assert ctx["available-stream"][0]["connection-address"] == [
        "grpc://twin.example.org:6000"
    ]
    assert ctx["supported-stream-type"][0]["record-retention"] == "60"


def test_aligning_until_the_first_tick_then_active(app: TestClient) -> None:
    """TR-548 ST-1.1 steps 3 and 5."""
    state = lambda: _context(app)["available-stream"][0]["stream-state"]
    assert state() == "tapi-streaming:STREAM_STATE_ALIGNING"
    asyncio.run(app.app.state.measurement_stream.tick())
    assert state() == "tapi-streaming:STREAM_STATE_ACTIVE"


@pytest.mark.parametrize(
    "suffix, key",
    [
        ("/supported-stream-type", "tapi-streaming:supported-stream-type"),
        (f"/supported-stream-type={SUPPORTED_STREAM_TYPE_UUID}",
         "tapi-streaming:supported-stream-type"),
        ("/available-stream", "tapi-streaming:available-stream"),
        (f"/available-stream={AVAILABLE_STREAM_UUID}", "tapi-streaming:available-stream"),
    ],
)
def test_list_and_entry_resources(app: TestClient, suffix: str, key: str) -> None:
    body = app.get(_SC + suffix).json()
    assert list(body) == [key]
    assert len(body[key]) == 1


def test_unknown_entries_are_404(app: TestClient) -> None:
    assert app.get(f"{_SC}/available-stream=nope").status_code == 404
    assert app.get(f"{_SC}/supported-stream-type=nope").status_code == 404


def test_in_full_context_and_under_restconf_root(app: TestClient) -> None:
    full = app.get("/data/tapi-common:context").json()["tapi-common:context"]
    assert full["tapi-streaming:stream-context"] == _context(app)
    rooted = app.get(f"/restconf{_SC}").json()
    assert rooted["tapi-streaming:stream-context"] == _context(app)
