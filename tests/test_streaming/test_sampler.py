"""The sampler: one record per OAM job per tick, valued exactly as /internal/opm."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.test_api.test_oam_api import _sips, create_service
from twinlight.api import internal
from twinlight.models.oam import oam_job_uuid
from twinlight.streaming.measurement_stream import MeasurementStream
from twinlight.streaming.proto import tapi_streaming_performance_pb2 as tapi
from twinlight.streaming.records import decode_measurements, job_uuid_of

FROZEN_T = 1_731_000_000.5


def _stream(app: TestClient) -> MeasurementStream:
    return app.app.state.measurement_stream


def _decode(record) -> tuple[str | None, tapi.MeasurementDetails]:
    [update] = record.response.update.update
    return job_uuid_of(update.path), tapi.MeasurementDetails.FromString(
        update.val.proto_bytes
    )


def _two_services(app: TestClient) -> list[str]:
    sip_a, sip_z = _sips(app)[:2]
    return [create_service(app, sip_a, sip_z), create_service(app, sip_a, sip_z)]


def test_one_record_per_job_per_tick(app: TestClient) -> None:
    services = _two_services(app)
    stream = _stream(app)
    assert asyncio.run(stream.tick()) == 2
    assert asyncio.run(stream.tick()) == 2
    records = stream.log.replay(0)
    assert len(records) == 4
    first, second = records[0].logged_ns, records[2].logged_ns
    assert records[1].logged_ns == first and records[3].logged_ns == second
    assert second > first
    assert [r.job_uuid for r in records[:2]] == [oam_job_uuid(s) for s in services]
    for record in records:
        job, _ = _decode(record)
        assert job == record.job_uuid
        assert record.response.update.timestamp == record.logged_ns


def test_ticks_without_services_still_activate_and_advance(app: TestClient) -> None:
    stream = _stream(app)
    assert asyncio.run(stream.tick()) == 0
    assert stream.last_logged_ns > 0
    assert stream.state.value == "tapi-streaming:STREAM_STATE_ACTIVE"


@pytest.mark.parametrize("client", ["app", "gnpy_app"])
def test_values_equal_internal_opm_at_the_same_instant(
    client: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stream and GET /internal/opm are one computation at one instant."""
    app: TestClient = request.getfixturevalue(client)
    _two_services(app)
    monkeypatch.setattr(internal, "wall_clock", lambda: FROZEN_T)
    stream = _stream(app)
    asyncio.run(stream.tick())
    opm = {
        entry["service-uuid"]: entry
        for entry in app.get("/internal/opm").json()["services"]
    }
    assert opm, "no services sampled"
    ctx = app.app.state.context
    for record in stream.log.replay(0):
        job_uuid, details = _decode(record)
        svc_uuid = ctx.get_oam_job(job_uuid).service_uuid
        assert details.time_measurement_was_sampled == round(FROZEN_T * 1000)
        assert decode_measurements(details) == opm[svc_uuid]["measurements"]
        svc = ctx.get_service(svc_uuid)
        assert details.native_resource_id == ctx.receiver_gnpy_uid(svc)
        assert details.sample_interval == 1_000_000_000


def test_link_failed_service_reports_missing(gnpy_app: TestClient) -> None:
    from gnpy.core.elements import Fiber

    sip_a, sip_z = _sips(gnpy_app)[:2]
    svc_uuid = create_service(gnpy_app, sip_a, sip_z)
    ctx = gnpy_app.app.state.context
    fiber = next(
        uid for uid in ctx.get_service_path(sip_a, sip_z)
        if isinstance(ctx._gnpy_uid_map[uid], Fiber)
    )
    ctx.set_link_failed(fiber, True)
    assert gnpy_app.get(f"/internal/opm/{svc_uuid}").json()["status"] == "link-failed"
    stream = _stream(gnpy_app)
    asyncio.run(stream.tick())
    [record] = stream.log.replay(0)
    _, details = _decode(record)
    assert {qm.qualified_measured_value.value_qualifier
            for qm in details.qualified_measurement} == {tapi.MISSING}


def test_failed_sample_is_logged_as_missing(
    app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_services(app)

    async def broken(*_args: object) -> dict:
        raise RuntimeError("propagation blew up")

    monkeypatch.setattr(internal, "sample_service_opm", broken)
    stream = _stream(app)
    assert asyncio.run(stream.tick()) == 2
    for record in stream.log.replay(0):
        _, details = _decode(record)
        assert {qm.qualified_measured_value.value_qualifier
                for qm in details.qualified_measurement} == {tapi.MISSING}


def test_service_deleted_mid_tick_gets_no_record(
    app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    keep, drop = _two_services(app)
    ctx = app.app.state.context
    real = internal.sample_service_opm

    async def deleting(c, cfg, svc, t):  # type: ignore[no-untyped-def]
        sample = await real(c, cfg, svc, t)
        ctx.delete_service(drop)
        return sample

    monkeypatch.setattr(internal, "sample_service_opm", deleting)
    stream = _stream(app)
    asyncio.run(stream.tick())
    assert [r.job_uuid for r in stream.log.replay(0)] == [oam_job_uuid(keep)]


def test_retention_trims_by_logged_age(app: TestClient) -> None:
    _two_services(app)
    now = [0]
    cfg = app.app.state.config
    cfg.streaming.record_retention_min = 1
    stream = MeasurementStream(app.app.state.context, cfg, clock_ns=lambda: now[0])
    minute = 60 * 10**9
    for now[0] in (1 * minute, 1 * minute + 10**9, 2 * minute + 10**9 // 2):
        asyncio.run(stream.tick())
    # At t = 2 min 0.5 s the cut-off is 1 min 0.5 s: the first tick is gone.
    assert stream.log.oldest_ns() == 1 * minute + 10**9
    assert len(stream.log.replay(0)) == 4


def test_logged_time_strictly_increases_when_the_clock_stalls(app: TestClient) -> None:
    stream = MeasurementStream(
        app.app.state.context, app.app.state.config, clock_ns=lambda: 5
    )
    asyncio.run(stream.tick())
    asyncio.run(stream.tick())
    assert stream.last_logged_ns == 6


def test_run_samples_without_any_subscriber(app: TestClient) -> None:
    """The log is always on: records accumulate with nobody connected."""
    _two_services(app)
    cfg = app.app.state.config
    cfg.streaming.sample_interval_s = 0.01
    stream = MeasurementStream(app.app.state.context, cfg)

    async def run_briefly() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(stream.run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run_briefly())
    assert len(stream.log.replay(0)) >= 4


def test_records_after_waits_for_the_next_tick(app: TestClient) -> None:
    _two_services(app)
    stream = _stream(app)

    async def scenario() -> list:
        await stream.tick()
        cursor = stream.last_logged_ns
        waiter = asyncio.create_task(stream.records_after(cursor))
        await asyncio.sleep(0.01)
        assert not waiter.done()
        await stream.tick()
        return await asyncio.wait_for(waiter, timeout=1)

    records = asyncio.run(scenario())
    assert len(records) == 2
    assert {r.logged_ns for r in records} == {stream.last_logged_ns}
