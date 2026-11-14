"""The ``streaming`` config section: defaults, validation, token masking."""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from twinlight.app import create_app
from twinlight.config import StreamingConfig, TwinConfig


def test_defaults() -> None:
    cfg = StreamingConfig()
    assert cfg.sample_interval_s == 1.0
    assert cfg.record_retention_min == 5
    assert cfg.auth_token.get_secret_value() == ""
    assert cfg.advertised_address == ""


def test_connection_address_derived_from_fqdn_and_port() -> None:
    assert StreamingConfig().connection_address(50052) == (
        f"grpc://{socket.getfqdn()}:50052"
    )


@pytest.mark.parametrize(
    "address", ["grpc://twin.example.org:50051", "grpc://10.0.0.1:1", "grpc://[::1]:50051"]
)
def test_advertised_address_overrides(address: str) -> None:
    assert StreamingConfig(advertised_address=address).connection_address(1) == address


@pytest.mark.parametrize(
    "address", ["twin:50051", "grpc://twin", "grpc://twin:50051/x", "http://twin:50051"]
)
def test_advertised_address_must_be_grpc_host_port(address: str) -> None:
    with pytest.raises(ValidationError, match="advertised_address"):
        StreamingConfig(advertised_address=address)


@pytest.mark.parametrize(
    "field, value",
    [("sample_interval_s", 0), ("record_retention_min", 0), ("unknown", 1)],
)
def test_rejects_bad_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        StreamingConfig(**{field: value})


def test_token_is_masked_by_the_transparent_controller(
    twin_config: TwinConfig,
) -> None:
    """The controller exposes every config leaf; the token only as its mask."""
    twin_config.streaming.auth_token = StreamingConfig(
        auth_token="s3cret"
    ).auth_token
    client = TestClient(create_app(twin_config))
    resp = client.get("/transparent/data/Config/streaming")
    assert resp.status_code == 200
    body = resp.json()["twinlight-transparent:data"]["Config"]["streaming"]
    assert body["auth_token"] == "**********"
    for path in ("/transparent/data", "/transparent/schema"):
        assert "s3cret" not in client.get(path).text
