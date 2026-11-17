"""The available-stream's connection-address: derived once, when the twin starts.

The default is ``grpc://<FQDN>:<grpc_port>``, and finding the FQDN is a
blocking resolver call. The discovery handlers run on the event loop the
sampler and the gRPC server share, so the lookup must not happen per request.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.config import TwinConfig
from twinlight.main import log_measurement_stream
from twinlight.streaming.measurement_stream import (
    AVAILABLE_STREAM_UUID,
    SUPPORTED_STREAM_TYPE_UUID,
)

_SC = "/data/tapi-common:context/tapi-streaming:stream-context"
# Every route whose response is built from the available-stream, including
# the full context controllers poll.
_ROUTES = (
    _SC,
    f"{_SC}/supported-stream-type",
    f"{_SC}/supported-stream-type={SUPPORTED_STREAM_TYPE_UUID}",
    f"{_SC}/available-stream",
    f"{_SC}/available-stream={AVAILABLE_STREAM_UUID}",
    "/data/tapi-common:context",
)


class _Resolver:
    """Stands in for ``socket.getfqdn``: a fixed answer, and a call count."""

    def __init__(self, fqdn: str) -> None:
        self.fqdn = fqdn
        self.calls = 0

    def __call__(self, name: str = "") -> str:
        self.calls += 1
        return self.fqdn


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> _Resolver:
    fake = _Resolver("twin.resolver.test")
    monkeypatch.setattr(socket, "getfqdn", fake)
    return fake


def _addresses(body: Any) -> list[list[str]]:
    """Every connection-address leaf anywhere in a response body."""
    if isinstance(body, dict):
        found = [body["connection-address"]] if "connection-address" in body else []
        return found + [a for v in body.values() for a in _addresses(v)]
    if isinstance(body, list):
        return [a for v in body for a in _addresses(v)]
    return []


def _served(client: TestClient, rounds: int) -> list[list[str]]:
    served: list[list[str]] = []
    for _ in range(rounds):
        for route in _ROUTES:
            resp = client.get(route)
            assert resp.status_code == 200, route
            served += _addresses(resp.json())
    return served


def test_default_resolved_once_at_startup_never_per_request(
    twin_config: TwinConfig, resolver: _Resolver, caplog: pytest.LogCaptureFixture,
) -> None:
    twin_config.server.grpc_port = 50052
    app = create_app(twin_config)
    assert resolver.calls == 1
    expected = "grpc://twin.resolver.test:50052"

    # The resolver's answer changing later must not change what is served.
    resolver.fqdn = "renamed.resolver.test"
    client = TestClient(app)
    served = _served(client, rounds=10)
    assert resolver.calls == 1  # no lookup on any request
    # Per round, four responses carry it: the stream-context, both
    # available-stream routes and the full context.
    assert len(served) == 10 * 4
    assert all(address == [expected] for address in served)

    # The startup log line shows the value every response serves.
    with caplog.at_level(logging.INFO, logger="twinlight.main"):
        log_measurement_stream(twin_config, app.state.measurement_stream)
    assert f"connection-address={expected}" in caplog.text
    assert resolver.calls == 1


@pytest.mark.parametrize(
    "address", ["grpc://twin.example.org:50051", "grpc://10.0.0.1:1", "grpc://[::1]:50051"]
)
def test_advertised_address_is_served_without_any_lookup(
    twin_config: TwinConfig, resolver: _Resolver, address: str,
) -> None:
    twin_config.streaming.advertised_address = address
    client = TestClient(create_app(twin_config))
    served = _served(client, rounds=2)
    assert served and all(a == [address] for a in served)
    assert resolver.calls == 0


def test_each_twin_keeps_its_own_address(
    twin_config: TwinConfig, resolver: _Resolver,
) -> None:
    """Nothing process-wide: a second twin resolves for itself."""
    first = TestClient(create_app(twin_config))
    resolver.fqdn = "second.resolver.test"
    second = TestClient(create_app(twin_config))
    port = twin_config.server.grpc_port
    assert _addresses(first.get(_SC).json()) == [[f"grpc://twin.resolver.test:{port}"]]
    assert _addresses(second.get(_SC).json()) == [[f"grpc://second.resolver.test:{port}"]]
    assert resolver.calls == 2
