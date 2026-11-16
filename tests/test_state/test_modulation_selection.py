"""Admission walks the k candidates and, without a requested format, tries
formats from the highest spectral efficiency down."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from tests.conftest import MeshRefs, tapi_end_point
from twinlight.models.connectivity import OTSIA_CSEP_SPEC, ConnectivityService
from twinlight.physics.modulation import (
    MODULATION_TO_MT,
    ModulationFormat,
    formats_by_spectral_efficiency,
    get_params,
    spectral_efficiency,
)
from twinlight.state.context import InsufficientQoTError, InsufficientSpectrumError


def _service(refs: MeshRefs, modulation: str | None = None) -> ConnectivityService:
    end_points = [
        tapi_end_point("a", refs.sip("trx A")),
        tapi_end_point("z", refs.sip("trx C")),
    ]
    if modulation is None:
        for ep in end_points:
            del ep["layer-protocol-constraint"]
    else:
        for ep in end_points:
            ep.update(tapi_end_point(ep["local-id"],
                                     ep["service-interface-point"]["service-interface-point-uuid"],
                                     modulation))
    return ConnectivityService.model_validate({"end-point": end_points})


def _stub_gsnr(monkeypatch, ctx, gsnr_by_path) -> list[tuple]:
    """Make propagation return a fixed GSNR per route; record every call."""
    calls: list[tuple] = []
    real = ctx._backend.compute_baseline

    async def fake(path, fmt, *args, **kwargs):
        calls.append((tuple(path), fmt))
        baseline = await real(path, fmt, *args, **kwargs)
        roadms = "".join(MeshRefs(ctx).roadms(path))
        return dataclasses.replace(baseline, gsnr_db=gsnr_by_path(roadms))

    monkeypatch.setattr(ctx._backend, "compute_baseline", fake)
    return calls


def test_formats_are_ordered_by_spectral_efficiency() -> None:
    order = formats_by_spectral_efficiency()
    assert order == [ModulationFormat.DP_64QAM, ModulationFormat.DP_16QAM,
                     ModulationFormat.DP_QPSK]
    assert spectral_efficiency("DP-QPSK") == pytest.approx(2 * 2 * 32e9 / 50e9)


def test_densest_format_that_closes_the_link(mesh_context) -> None:
    """With real propagation, the chosen format is the first in SE order whose
    requirement plus margin the route's GSNR meets."""
    refs = MeshRefs(mesh_context)
    svc = _service(refs)
    assert svc.requested_modulation is None
    asyncio.run(mesh_context.add_service(svc))

    gsnr = mesh_context._baseline_cache[svc.uuid].gsnr_db
    margin = mesh_context._config.rmsa.qot_margin_db
    expected = next(
        f for f in formats_by_spectral_efficiency()
        if gsnr >= get_params(f).req_gsnr_db + margin
    )
    assert svc.modulation_format == expected
    # The format in effect is what a GET reports.
    spec = svc.model_dump(by_alias=True)["end-point"][0]["layer-protocol-constraint"][0]
    mt = spec[OTSIA_CSEP_SPEC]["otsi-config"][0]["modulation"]["standard-modulation-technique"]
    assert mt == MODULATION_TO_MT[expected]


def test_requested_format_is_the_only_one_tried(mesh_context, monkeypatch) -> None:
    refs = MeshRefs(mesh_context)
    calls = _stub_gsnr(monkeypatch, mesh_context, lambda _r: 30.0)
    svc = _service(refs, "DP-16QAM")
    asyncio.run(mesh_context.add_service(svc))
    assert svc.modulation_format == ModulationFormat.DP_16QAM
    assert [fmt for _p, fmt in calls] == ["DP-16QAM"]


def test_formats_of_equal_width_share_one_propagation(mesh_context, monkeypatch) -> None:
    """GSNR 10 dB passes only DP-QPSK (8.5 + 1.5). All three formats occupy
    the same block, so the route is propagated once, not three times."""
    refs = MeshRefs(mesh_context)
    calls = _stub_gsnr(monkeypatch, mesh_context, lambda _r: 10.0)
    svc = _service(refs)
    asyncio.run(mesh_context.add_service(svc))
    assert svc.modulation_format == ModulationFormat.DP_QPSK
    assert len(calls) == 1


def test_qot_failure_on_the_first_candidate_moves_to_the_next(
    mesh_context, monkeypatch,
) -> None:
    refs = MeshRefs(mesh_context)
    _stub_gsnr(monkeypatch, mesh_context, lambda r: 5.0 if r == "ABC" else 12.0)
    svc = _service(refs, "DP-QPSK")
    asyncio.run(mesh_context.add_service(svc))
    path = mesh_context._allocated_path_uids(svc.uuid)
    assert refs.roadms(path) == ["A", "D", "C"]


def test_full_spectrum_on_the_first_candidate_moves_to_the_next(mesh_context) -> None:
    from twinlight.algorithms.spectrum_assignment import path_uids_to_edges

    refs = MeshRefs(mesh_context)
    (best,) = mesh_context.candidate_paths("trx A", "trx C")
    state = mesh_context._spectrum_state
    # Fill the line edges only: the transceivers' access edges are on every route.
    line = [e for e in path_uids_to_edges(best) if not any(u.startswith("trx") for u in e)]
    state.allocate(line, 0, state.num_slots)

    svc = _service(refs, "DP-QPSK")
    asyncio.run(mesh_context.add_service(svc))
    assert refs.roadms(mesh_context._allocated_path_uids(svc.uuid)) != ["A", "B", "C"]


def test_refusal_lists_every_attempt(mesh_context, monkeypatch) -> None:
    refs = MeshRefs(mesh_context)
    _stub_gsnr(monkeypatch, mesh_context, lambda _r: 5.0)
    svc = _service(refs)
    with pytest.raises(InsufficientQoTError) as err:
        asyncio.run(mesh_context.add_service(svc))
    message = str(err.value)
    for fmt in ("DP-64QAM", "DP-16QAM", "DP-QPSK"):
        assert fmt in message
    assert "candidate 2" in message
    assert mesh_context.get_service(svc.uuid) is None
    assert not mesh_context._service_allocation


def test_no_spectrum_anywhere(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    state = mesh_context._spectrum_state
    from twinlight.algorithms.spectrum_assignment import path_uids_to_edges

    for path in mesh_context.candidate_paths("trx A", "trx C", k=10):
        state.allocate(path_uids_to_edges(path), 0, state.num_slots)
    with pytest.raises(InsufficientSpectrumError):
        asyncio.run(mesh_context.add_service(_service(refs, "DP-QPSK")))


def test_without_physics_the_most_robust_format_is_chosen(app) -> None:
    """No QoT model: a request that names no format gets DP-QPSK, never the
    densest format the twin has no way to check."""
    data = app.get("/data/tapi-common:context/service-interface-point").json()
    sips = [s["uuid"] for s in data["tapi-common:context"]["service-interface-point"]]
    end_points = [tapi_end_point("a", sips[0]), tapi_end_point("z", sips[1])]
    for ep in end_points:
        del ep["layer-protocol-constraint"]
    resp = app.post(
        "/data/tapi-connectivity:connectivity-context/connectivity-service",
        json={"end-point": end_points},
    )
    assert resp.status_code == 201
    ep = resp.json()["tapi-connectivity:connectivity-service"]["end-point"][0]
    spec = ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]
    assert spec["otsi-config"][0]["modulation"]["standard-modulation-technique"] == "MT_DP-QPSK"
