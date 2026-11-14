"""The T-API stream client: discovery, job filtering, decoding, resume."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import grpc
import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_streaming.test_gnmi_service import Twin, start_twin
from twinlight.config import StreamingConfig, TwinConfig
from twinlight.grpc_server import build_grpc_server
from twinlight.models.oam import oam_job_uuid
from twinlight_client.client import TapiClient
from twinlight_client.streaming import (
    MeasurementRecord,
    StreamDiscoveryError,
    StreamEndpoint,
    TapiStreamClient,
    discover_stream,
    oam_job_for_service,
    select_stream,
)

TIMEOUT = 5.0


def _rest(twin: Twin) -> TapiClient:
    rest = TapiClient("http://twin")
    rest._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=twin.app), base_url="http://twin"
    )
    return rest


@pytest.fixture
async def twin(twin_config: TwinConfig) -> AsyncIterator[Twin]:
    twin_config.streaming.advertised_address = "grpc://127.0.0.1:1"
    t = await start_twin(twin_config)
    yield t
    await t.close()


async def take(it: AsyncIterator[MeasurementRecord], n: int) -> list[MeasurementRecord]:
    out: list[MeasurementRecord] = []

    async def read() -> None:
        async for record in it:
            out.append(record)
            if len(out) == n:
                return

    await asyncio.wait_for(read(), TIMEOUT)
    return out


# -- Discovery --------------------------------------------------------------------


def test_select_stream_from_the_twins_stream_context(app: TestClient) -> None:
    ctx = app.get(
        "/data/tapi-common:context/tapi-streaming:stream-context"
    ).json()["tapi-streaming:stream-context"]
    endpoint = select_stream(ctx)
    assert endpoint.uuid == ctx["available-stream"][0]["uuid"]
    assert endpoint.connection_address.startswith("grpc://")


def test_select_stream_rejects_other_stream_types(app: TestClient) -> None:
    ctx = app.get(
        "/data/tapi-common:context/tapi-streaming:stream-context"
    ).json()["tapi-streaming:stream-context"]
    ctx["supported-stream-type"][0]["log-storage-strategy"] = (
        "tapi-streaming:LOG_STORAGE_STRATEGY_COMPACTED"
    )
    with pytest.raises(StreamDiscoveryError, match="supported-stream-type"):
        select_stream(ctx)


@pytest.mark.parametrize(
    "address, target",
    [("grpc://twin.example.org:50051", "twin.example.org:50051"),
     ("grpc://[::1]:50051", "[::1]:50051")],
)
def test_endpoint_target(address: str, target: str) -> None:
    assert StreamEndpoint("u", address, "").target == target


def test_endpoint_target_rejects_other_schemes() -> None:
    with pytest.raises(StreamDiscoveryError):
        _ = StreamEndpoint("u", "http://twin:8080", "").target


async def test_discover_and_find_the_job_over_restconf(twin: Twin) -> None:
    svc = await twin.add_service()
    async with _rest(twin) as rest:
        endpoint = await discover_stream(rest)
        assert endpoint.target == "127.0.0.1:1"
        assert await oam_job_for_service(rest, svc) == oam_job_uuid(svc)
        with pytest.raises(StreamDiscoveryError):
            await oam_job_for_service(rest, "no-such-service")


# -- Subscribing -------------------------------------------------------------------


async def test_records_are_decoded_and_filtered_by_job(twin: Twin) -> None:
    wanted = await twin.add_service()
    await twin.add_service()
    async with TapiStreamClient(twin.target) as client:
        caps = await client.capabilities()
        assert caps["supported_encodings"] == ["PROTO"]
        it = client.records(job_uuid=oam_job_uuid(wanted))
        reader = asyncio.create_task(take(it, 2))
        await asyncio.sleep(0.1)
        await twin.stream.tick()
        await twin.stream.tick()
        records = await reader
    assert {r.job_uuid for r in records} == {oam_job_uuid(wanted)}
    assert records[1].logged_ns > records[0].logged_ns
    assert records[0].measurements["gsnr-db"] is not None
    assert records[0].sampled_ms > 0


async def test_token_is_sent_as_bearer_metadata(twin_config: TwinConfig) -> None:
    twin_config.streaming = StreamingConfig(auth_token="s3cret")
    twin = await start_twin(twin_config)
    try:
        async with TapiStreamClient(twin.target) as anonymous:
            with pytest.raises(grpc.aio.AioRpcError) as info:
                await anonymous.capabilities()
            assert info.value.code() == grpc.StatusCode.UNAUTHENTICATED
        async with TapiStreamClient(twin.target, token="s3cret") as client:
            assert (await client.capabilities())["gnmi_version"]
    finally:
        await twin.close()


async def test_follow_does_not_retry_a_rejection(twin_config: TwinConfig) -> None:
    twin_config.streaming = StreamingConfig(auth_token="s3cret")
    twin = await start_twin(twin_config)
    try:
        async with TapiStreamClient(twin.target, token="wrong") as client:
            with pytest.raises(grpc.aio.AioRpcError) as info:
                await take(client.follow(retry_delay_s=0.01), 1)
            assert info.value.code() == grpc.StatusCode.UNAUTHENTICATED
    finally:
        await twin.close()


async def test_follow_resumes_across_a_server_restart_without_loss(twin: Twin) -> None:
    """ST-6.4: records logged while disconnected arrive, none twice."""
    svc = await twin.add_service()
    job = oam_job_uuid(svc)
    target, port = twin.target, twin.port
    seen: list[MeasurementRecord] = []

    async with TapiStreamClient(target) as client:
        follow = client.follow(job_uuid=job, retry_delay_s=0.05)

        async def consume() -> None:
            async for record in follow:
                seen.append(record)
                if len(seen) == 5:
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        await twin.stream.tick()
        await twin.stream.tick()
        while len(seen) < 2:
            await asyncio.sleep(0.01)

        # Comms lost: the server goes away; the log keeps filling.
        await twin.server.stop(None)
        for _ in range(3):
            await twin.stream.tick()

        # Comms recover on the same address.
        twin.server = build_grpc_server(twin.app)
        twin.server.add_insecure_port(f"127.0.0.1:{port}")
        await twin.server.start()
        await asyncio.wait_for(consumer, TIMEOUT)

    times = [r.logged_ns for r in seen]
    logged = [r.logged_ns for r in twin.stream.log.replay(0) if r.job_uuid == job]
    assert times == logged  # every tick, in order, exactly once
