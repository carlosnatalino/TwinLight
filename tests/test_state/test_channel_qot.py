"""Every QoT view reports the service's own channel.

Admission, the cached baseline, the QoT what-if and the /internal path
estimate all evaluate the channel at the service's (or the would-be
service's) central frequency and launch power on a fully loaded line, so
they agree with each other — on both backends.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import MeshRefs, tapi_end_point
from twinlight.models.connectivity import ConnectivityService


def _admit(ctx, start_slot: int | None = None) -> ConnectivityService:
    refs = MeshRefs(ctx)
    body = {"end-point": [tapi_end_point("a", refs.sip("trx A")),
                          tapi_end_point("z", refs.sip("trx C"))]}
    svc = ConnectivityService.model_validate(body)
    if start_slot is not None:
        lower, upper = ctx._slot_range_hz(start_slot, ctx._block_width(svc.modulation_format))
        svc.requested_spectrum_hz = (lower, upper)
    asyncio.run(ctx.add_service(svc))
    return svc


def test_baseline_depends_on_where_the_channel_sits(mesh_context) -> None:
    """Two services on the same route, at the band edge and mid-band, get
    different baselines: the report is per channel, not the comb's worst."""
    edge = _admit(mesh_context)          # first fit: the lowest in-band block
    middle = _admit(mesh_context, 400)
    a = mesh_context._baseline_cache[edge.uuid]
    b = mesh_context._baseline_cache[middle.uuid]
    assert a.osnr_ase_db != pytest.approx(b.osnr_ase_db, abs=1e-6)


def test_recomputed_baseline_matches_admission(mesh_context) -> None:
    svc = _admit(mesh_context, 300)
    at_admission = mesh_context._baseline_cache[svc.uuid].gsnr_db
    mesh_context.invalidate_baseline(svc.uuid)
    recomputed = asyncio.run(mesh_context.get_or_compute_baseline(svc)).gsnr_db
    assert recomputed == pytest.approx(at_admission)


def test_what_if_evaluates_the_service_channel(mesh_context) -> None:
    """A what-if that changes nothing predicts exactly the current baseline."""
    svc = _admit(mesh_context, 300)
    fiber = next(uid for uid in mesh_context._allocated_path_uids(svc.uuid)
                 if mesh_context._element_kind(uid) == "Fiber")
    value = mesh_context._backend.read_attribute(fiber, "loss_coef")
    (report,) = asyncio.run(mesh_context.preview_qot({fiber: {"loss_coef": value}}, []))
    assert report["predicted"]["gsnr_db"] == pytest.approx(report["current"]["gsnr_db"])


def test_path_estimate_is_what_admission_sees(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    estimate = asyncio.run(mesh_context.get_path_baseline(
        refs.sip("trx A"), refs.sip("trx C"), "DP-QPSK"))
    assert estimate is not None
    baseline, centre_hz, power_dbm = estimate

    refs_body = {"end-point": [
        tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C")),
    ]}
    svc = ConnectivityService.model_validate(refs_body)
    asyncio.run(mesh_context.add_service(svc))
    assert svc.central_frequency_hz == centre_hz
    assert svc.launch_power_dbm == power_dbm
    assert mesh_context._baseline_cache[svc.uuid].gsnr_db == pytest.approx(baseline.gsnr_db)


def test_path_info_reports_the_evaluated_channel(mesh_app) -> None:
    refs = MeshRefs(mesh_app.app.state.context)
    resp = mesh_app.get("/internal/path-info", params={
        "sip_a": refs.sip("trx A"), "sip_z": refs.sip("trx C"), "modulation": "DP-QPSK",
    })
    body = resp.json()
    assert body["central-frequency-thz"] == pytest.approx(191.30625)
    assert body["launch-power-dbm"] == 0.0
    assert body["measurements"]["gsnr-db"] is not None
    bad = mesh_app.get("/internal/path-info", params={
        "sip_a": refs.sip("trx A"), "sip_z": refs.sip("trx C"), "modulation": "DP-8PSK",
    })
    assert bad.status_code == 422


def test_egn_loaded_line_is_below_the_lone_channel(tmp_path) -> None:
    """The cross-channel term is active under EGN: the same channel with the
    SI comb present has a lower GSNR than alone (no comb, no band)."""
    from tests.conftest import _mesh_config
    from twinlight.physics.egn_kernel import accumulate_path_noise
    from twinlight.state.context import TapiContext

    ctx = TapiContext(_mesh_config(tmp_path, "egn"))
    (path,) = ctx.candidate_paths("trx A", "trx C")
    loaded = asyncio.run(ctx._backend.compute_baseline(
        path, "DP-QPSK", centre_frequency_hz=193.1e12, media_channel_width_hz=56.25e9,
        launch_power_dbm=0.0,
    ))
    spans, _fibers, _edfas = ctx._backend._spans_for_path(path)
    alone = accumulate_path_noise(
        [ctx._backend._span_inputs_for_key(key) for key in spans],
        launch_power_w=1e-3, bandwidth_hz=32e9, centre_frequency_hz=193.1e12,
    )
    assert loaded.gsnr_db < alone.gsnr_db - 1.0
    assert loaded.osnr_ase_db == pytest.approx(alone.osnr_ase_db)
