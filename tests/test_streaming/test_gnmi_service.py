"""The gNMI server end to end: a real grpc.aio server on an ephemeral port."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import grpc
import httpx
import pytest

from tests.conftest import tapi_end_point
from twinlight.app import create_app
from twinlight.config import StreamingConfig, TwinConfig
from twinlight.grpc_server import build_grpc_server
from twinlight.models.oam import oam_job_uuid
from twinlight.streaming.measurement_stream import MeasurementStream
from twinlight.streaming.proto import gnmi_ext_pb2, gnmi_pb2, gnmi_pb2_grpc
from twinlight.streaming.proto import tapi_streaming_performance_pb2 as tapi
from twinlight.streaming.records import decode_measurements, job_uuid_of

TIMEOUT = 5.0


class Twin:
    """An app, its gRPC server, and a stub — all on the test's event loop."""

    def __init__(self, app: Any, port: int, server: grpc.aio.Server) -> None:
        self.app = app
        self.server = server
        self.channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        self.stub = gnmi_pb2_grpc.gNMIStub(self.channel)
        self.rest = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://twin"
        )

    @property
    def stream(self) -> MeasurementStream:
        return self.app.state.measurement_stream

    async def add_service(self) -> str:
        sips = (await self.rest.get(
            "/data/tapi-common:context/service-interface-point"
        )).json()["tapi-common:context"]["service-interface-point"]
        resp = await self.rest.post(
            "/data/tapi-connectivity:connectivity-context/connectivity-service",
            json={"tapi-connectivity:connectivity-service": {"end-point": [
                tapi_end_point("a", sips[0]["uuid"]), tapi_end_point("z", sips[1]["uuid"]),
            ]}},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["tapi-connectivity:connectivity-service"]["uuid"]

    async def close(self) -> None:
        await self.rest.aclose()
        await self.channel.close()
        await self.server.stop(None)


async def start_twin(config: TwinConfig) -> Twin:
    app = create_app(config)
    server = build_grpc_server(app)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return Twin(app, port, server)


@pytest.fixture
async def twin(twin_config: TwinConfig) -> AsyncIterator[Twin]:
    t = await start_twin(twin_config)
    yield t
    await t.close()


def subscribe_request(start_ns: int | None = None) -> gnmi_pb2.SubscribeRequest:
    """The TR-548 §6.3.4 subscription, optionally resuming from ``start_ns``."""
    req = gnmi_pb2.SubscribeRequest(
        subscribe=gnmi_pb2.SubscriptionList(
            prefix=gnmi_pb2.Path(target="tapi"),
            subscription=[gnmi_pb2.Subscription(
                path=gnmi_pb2.Path(), mode=gnmi_pb2.SubscriptionMode.TARGET_DEFINED,
            )],
            mode=gnmi_pb2.SubscriptionList.STREAM,
            updates_only=True,
        )
    )
    if start_ns is not None:
        req.extension.append(gnmi_ext_pb2.Extension(
            history=gnmi_ext_pb2.History(range=gnmi_ext_pb2.TimeRange(start=start_ns))
        ))
    return req


async def _requests(*reqs: gnmi_pb2.SubscribeRequest, hold: bool = True) -> AsyncIterator[Any]:
    for r in reqs:
        yield r
    if hold:  # keep the send side open, as a long-lived client does
        await asyncio.Event().wait()


async def collect(call: Any, n: int) -> list[gnmi_pb2.SubscribeResponse]:
    out: list[gnmi_pb2.SubscribeResponse] = []

    async def read() -> None:
        async for resp in call:
            out.append(resp)
            if len(out) == n:
                return

    await asyncio.wait_for(read(), TIMEOUT)
    return out


async def expect_error(call: Any) -> grpc.aio.AioRpcError:
    with pytest.raises(grpc.aio.AioRpcError) as info:
        await asyncio.wait_for(collect(call, 1_000), TIMEOUT)
    return info.value


def _jobs_and_times(responses: list[gnmi_pb2.SubscribeResponse]) -> list[tuple[str | None, int]]:
    return [
        (job_uuid_of(r.update.update[0].path), r.update.timestamp)
        for r in responses if r.HasField("update")
    ]


# -- Capabilities ---------------------------------------------------------------


async def test_capabilities_advertise_tapi_models_and_proto(twin: Twin) -> None:
    caps = await twin.stub.Capabilities(gnmi_pb2.CapabilityRequest())
    names = {m.name for m in caps.supported_models}
    assert {"tapi-streaming", "tapi-gnmi-streaming"} <= names
    assert {m.version for m in caps.supported_models} == {"2.6.0"}
    assert list(caps.supported_encodings) == [gnmi_pb2.Encoding.PROTO]
    assert caps.gNMI_version == "0.10.0"


async def test_get_and_set_are_unimplemented(twin: Twin) -> None:
    for rpc, req in ((twin.stub.Get, gnmi_pb2.GetRequest()), (twin.stub.Set, gnmi_pb2.SetRequest())):
        with pytest.raises(grpc.aio.AioRpcError) as info:
            await rpc(req)
        assert info.value.code() == grpc.StatusCode.UNIMPLEMENTED


# -- Accepted subscriptions -------------------------------------------------------


def _tr548_example() -> gnmi_pb2.SubscribeRequest:
    """The literal example of TR-548 §6.3.4: SAMPLE with sample_interval 0."""
    req = subscribe_request()
    req.subscribe.subscription[0].mode = gnmi_pb2.SubscriptionMode.SAMPLE
    return req


def _minimal() -> gnmi_pb2.SubscribeRequest:
    req = subscribe_request()
    req.subscribe.ClearField("prefix")
    return req


def _with_ignored_fields() -> gnmi_pb2.SubscribeRequest:
    req = subscribe_request()
    req.subscribe.qos.marking = 10
    req.subscribe.allow_aggregation = True
    req.subscribe.encoding = gnmi_pb2.Encoding.PROTO
    return req


@pytest.mark.parametrize("make", [subscribe_request, _tr548_example, _minimal, _with_ignored_fields])
async def test_accepted_subscription_streams_decodable_records(
    twin: Twin, make: Callable[[], gnmi_pb2.SubscribeRequest]
) -> None:
    svc = await twin.add_service()
    await twin.stream.tick()
    call = twin.stub.Subscribe(_requests(make()))
    [sync] = await collect(call, 1)
    assert sync.sync_response
    await twin.stream.tick()
    [resp] = await collect(call, 1)
    call.cancel()

    [update] = resp.update.update
    assert job_uuid_of(update.path) == oam_job_uuid(svc)
    assert resp.update.timestamp == twin.stream.last_logged_ns
    details = tapi.MeasurementDetails.FromString(update.val.proto_bytes)
    opm = decode_measurements(details)
    assert set(opm) >= {"gsnr-db", "pre-fec-ber", "chromatic-dispersion-ps-per-nm"}


async def test_half_close_keeps_the_subscription(twin: Twin) -> None:
    await twin.add_service()
    call = twin.stub.Subscribe(_requests(subscribe_request(), hold=False))
    await collect(call, 1)  # sync_response
    await twin.stream.tick()
    [resp] = await collect(call, 1)
    call.cancel()
    assert resp.HasField("update")


async def test_every_subscriber_gets_the_same_records(twin: Twin) -> None:
    await twin.add_service()
    await twin.add_service()
    calls = [twin.stub.Subscribe(_requests(subscribe_request())) for _ in range(3)]
    for call in calls:
        await collect(call, 1)
    await twin.stream.tick()
    received = [await collect(call, 2) for call in calls]
    for call in calls:
        call.cancel()
    wire = [[r.SerializeToString() for r in rs] for rs in received]
    assert wire[0] == wire[1] == wire[2]


async def test_subscriber_connected_while_aligning_waits(twin: Twin) -> None:
    """ST-1.1: a client may connect during ALIGNING; no records until ACTIVE."""
    await twin.add_service()
    assert twin.stream.state.value.endswith("ALIGNING")
    call = twin.stub.Subscribe(_requests(subscribe_request()))
    [sync] = await collect(call, 1)
    assert sync.sync_response
    reader = asyncio.create_task(collect(call, 1))
    await asyncio.sleep(0.2)
    assert not reader.done()
    await twin.stream.tick()
    assert twin.stream.state.value.endswith("ACTIVE")
    [resp] = await reader
    call.cancel()
    assert resp.HasField("update")


# -- Rejected subscriptions -------------------------------------------------------


def _mutate(fn: Callable[[gnmi_pb2.SubscribeRequest], Any]) -> gnmi_pb2.SubscribeRequest:
    req = subscribe_request()
    fn(req)
    return req


def _set_ext(req: gnmi_pb2.SubscribeRequest, ext: gnmi_ext_pb2.Extension) -> None:
    req.extension.append(ext)


REJECTED: list[tuple[str, gnmi_pb2.SubscribeRequest]] = [
    ("request", gnmi_pb2.SubscribeRequest(poll=gnmi_pb2.Poll())),
    ("subscribe.mode", _mutate(lambda r: setattr(r.subscribe, "mode", gnmi_pb2.SubscriptionList.ONCE))),
    ("subscribe.mode", _mutate(lambda r: setattr(r.subscribe, "mode", gnmi_pb2.SubscriptionList.POLL))),
    ("subscribe.prefix.origin", _mutate(lambda r: setattr(r.subscribe.prefix, "origin", "openconfig"))),
    ("subscribe.prefix.elem", _mutate(lambda r: r.subscribe.prefix.elem.add(name="x"))),
    ("subscribe.prefix.target", _mutate(lambda r: setattr(r.subscribe.prefix, "target", "other"))),
    ("subscribe.subscription", _mutate(lambda r: r.subscribe.subscription.add())),
    ("subscribe.subscription", _mutate(lambda r: r.subscribe.ClearField("subscription"))),
    ("subscribe.subscription[0].path", _mutate(
        lambda r: r.subscribe.subscription[0].path.elem.add(name="oam-job"))),
    ("subscribe.subscription[0].mode", _mutate(
        lambda r: setattr(r.subscribe.subscription[0], "mode", gnmi_pb2.SubscriptionMode.ON_CHANGE))),
    ("subscribe.subscription[0].sample_interval", _mutate(
        lambda r: setattr(r.subscribe.subscription[0], "sample_interval", 10**9))),
    ("subscribe.subscription[0].suppress_redundant", _mutate(
        lambda r: setattr(r.subscribe.subscription[0], "suppress_redundant", True))),
    ("subscribe.subscription[0].heartbeat_interval", _mutate(
        lambda r: setattr(r.subscribe.subscription[0], "heartbeat_interval", 10**9))),
    ("subscribe.updates_only", _mutate(lambda r: setattr(r.subscribe, "updates_only", False))),
    ("subscribe.use_models", _mutate(lambda r: r.subscribe.use_models.add(name="openconfig"))),
    ("subscribe.encoding", _mutate(lambda r: setattr(r.subscribe, "encoding", gnmi_pb2.Encoding.JSON_IETF))),
    ("extension.depth", _mutate(lambda r: _set_ext(r, gnmi_ext_pb2.Extension(
        depth=gnmi_ext_pb2.Depth(level=1))))),
    ("extension.history.snapshot_time", _mutate(lambda r: _set_ext(r, gnmi_ext_pb2.Extension(
        history=gnmi_ext_pb2.History(snapshot_time=1))))),
    ("extension.history.range.end", _mutate(lambda r: _set_ext(r, gnmi_ext_pb2.Extension(
        history=gnmi_ext_pb2.History(range=gnmi_ext_pb2.TimeRange(start=1, end=5)))))),
    ("extension.history.range.start", _mutate(lambda r: _set_ext(r, gnmi_ext_pb2.Extension(
        history=gnmi_ext_pb2.History(range=gnmi_ext_pb2.TimeRange(start=-1)))))),
    ("extension.history", _mutate(lambda r: [_set_ext(r, gnmi_ext_pb2.Extension(
        history=gnmi_ext_pb2.History(range=gnmi_ext_pb2.TimeRange(start=1)))) for _ in range(2)])),
]


@pytest.mark.parametrize("field, request_", REJECTED, ids=[f for f, _ in REJECTED])
async def test_rejected_subscription_names_the_field(
    twin: Twin, field: str, request_: gnmi_pb2.SubscribeRequest
) -> None:
    err = await expect_error(twin.stub.Subscribe(_requests(request_, hold=False)))
    assert err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert err.details().startswith(f"{field}:")


async def test_second_request_on_the_stream_is_rejected(twin: Twin) -> None:
    call = twin.stub.Subscribe(_requests(
        subscribe_request(), gnmi_pb2.SubscribeRequest(poll=gnmi_pb2.Poll()), hold=False
    ))
    err = await expect_error(call)
    assert err.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert err.details().startswith("request:")


# -- Resume from the log (TR-548 ST-6.4) -------------------------------------------


async def _three_ticks(twin: Twin) -> tuple[str, list[int]]:
    svc = await twin.add_service()
    times = []
    for _ in range(3):
        await twin.stream.tick()
        times.append(twin.stream.last_logged_ns)
    return oam_job_uuid(svc), times


async def test_resume_replays_from_start_inclusive_then_syncs(twin: Twin) -> None:
    job, (_t1, t2, t3) = await _three_ticks(twin)
    call = twin.stub.Subscribe(_requests(subscribe_request(start_ns=t2)))
    responses = await collect(call, 3)
    assert _jobs_and_times(responses) == [(job, t2), (job, t3)]
    assert responses[2].sync_response
    await twin.stream.tick()
    [live] = await collect(call, 1)
    call.cancel()
    assert live.update.timestamp == twin.stream.last_logged_ns


async def test_resume_between_ticks_starts_at_the_next(twin: Twin) -> None:
    job, (t1, t2, t3) = await _three_ticks(twin)
    call = twin.stub.Subscribe(_requests(subscribe_request(start_ns=t1 + 1)))
    responses = await collect(call, 3)
    call.cancel()
    assert _jobs_and_times(responses) == [(job, t2), (job, t3)]


async def test_resume_after_truncation_starts_at_the_oldest(twin: Twin) -> None:
    job, (t1, t2, t3) = await _three_ticks(twin)
    twin.stream.log.trim(t2)  # t1 aged out
    call = twin.stub.Subscribe(_requests(subscribe_request(start_ns=t1)))
    responses = await collect(call, 3)
    call.cancel()
    assert _jobs_and_times(responses) == [(job, t2), (job, t3)]


async def test_resume_exactly_at_the_oldest_skips_it(twin: Twin) -> None:
    """ST-6.4: "that record must not be sent as a record at that time may have been lost"."""
    job, (t1, t2, t3) = await _three_ticks(twin)
    assert twin.stream.log.oldest_ns() == t1
    call = twin.stub.Subscribe(_requests(subscribe_request(start_ns=t1)))
    responses = await collect(call, 3)
    call.cancel()
    assert _jobs_and_times(responses) == [(job, t2), (job, t3)]


async def test_resume_from_the_future_streams_live(twin: Twin) -> None:
    await _three_ticks(twin)
    future = twin.stream.now_ns() + 3600 * 10**9
    call = twin.stub.Subscribe(_requests(subscribe_request(start_ns=future)))
    [sync] = await collect(call, 1)
    assert sync.sync_response
    await twin.stream.tick()
    [live] = await collect(call, 1)
    call.cancel()
    assert live.update.timestamp == twin.stream.last_logged_ns


async def test_resume_with_an_empty_window_just_syncs(twin: Twin) -> None:
    _, (_t1, _t2, t3) = await _three_ticks(twin)
    call = twin.stub.Subscribe(_requests(subscribe_request(start_ns=t3 + 1)))
    [sync] = await collect(call, 1)
    call.cancel()
    assert sync.sync_response


# -- Authentication ---------------------------------------------------------------


@pytest.fixture
async def secured(twin_config: TwinConfig) -> AsyncIterator[Twin]:
    twin_config.streaming = StreamingConfig(auth_token="s3cret")
    t = await start_twin(twin_config)
    yield t
    await t.close()


@pytest.mark.parametrize(
    "metadata",
    [None, (("authorization", "Bearer wrong"),), (("authorization", "s3cret"),),
     (("authorization", "Basic s3cret"),)],
    ids=["missing", "wrong-token", "no-scheme", "wrong-scheme"],
)
async def test_auth_on_rejects_without_the_token(secured: Twin, metadata: Any) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as info:
        await secured.stub.Capabilities(gnmi_pb2.CapabilityRequest(), metadata=metadata)
    assert info.value.code() == grpc.StatusCode.UNAUTHENTICATED
    err = await expect_error(
        secured.stub.Subscribe(_requests(subscribe_request(), hold=False), metadata=metadata)
    )
    assert err.code() == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.parametrize("scheme", ["Bearer", "bearer"])
async def test_auth_on_accepts_the_token(secured: Twin, scheme: str) -> None:
    metadata = (("authorization", f"{scheme} s3cret"),)
    caps = await secured.stub.Capabilities(gnmi_pb2.CapabilityRequest(), metadata=metadata)
    assert caps.gNMI_version
    await secured.add_service()
    call = secured.stub.Subscribe(_requests(subscribe_request()), metadata=metadata)
    await collect(call, 1)
    await secured.stream.tick()
    [resp] = await collect(call, 1)
    call.cancel()
    assert resp.HasField("update")


async def test_auth_off_needs_no_metadata(twin: Twin) -> None:
    caps = await twin.stub.Capabilities(gnmi_pb2.CapabilityRequest())
    assert caps.gNMI_version
    caps = await twin.stub.Capabilities(
        gnmi_pb2.CapabilityRequest(), metadata=(("authorization", "Bearer anything"),)
    )
    assert caps.gNMI_version
