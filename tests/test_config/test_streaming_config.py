"""The ``streaming`` config section: defaults and validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from twinlight.app import create_app
from twinlight.auth import parse_tokens
from twinlight.config import StreamingConfig, TwinConfig


def test_defaults() -> None:
    cfg = StreamingConfig()
    assert cfg.sample_interval_s == 1.0
    assert cfg.record_retention_min == 5
    assert cfg.advertised_address == ""


@pytest.mark.parametrize(
    "address", ["grpc://twin.example.org:50051", "grpc://10.0.0.1:1", "grpc://[::1]:50051"]
)
def test_advertised_address_accepts_grpc_host_port(address: str) -> None:
    assert StreamingConfig(advertised_address=address).advertised_address == address


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


def test_tokens_never_reach_the_transparent_controller(
    twin_config: TwinConfig,
) -> None:
    """The controller exposes every config leaf, but not the API tokens."""
    token = "s3cret-token-0123456789"
    twin_config.api_tokens = parse_tokens(token)
    client = TestClient(create_app(twin_config))
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/transparent/data/Config/streaming", headers=headers)
    assert resp.status_code == 200
    assert "auth_token" not in resp.json()["twinlight-transparent:data"]["Config"]["streaming"]
    for path in ("/transparent/data", "/transparent/schema"):
        body = client.get(path, headers=headers)
        assert body.status_code == 200
        assert token not in body.text
        assert "api_tokens" not in body.text
