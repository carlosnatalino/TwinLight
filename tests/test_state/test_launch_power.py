"""Launch power: otsi-config/channel-output-power, else the equipment SI default.

tapi-photonic-media.yang types channel-output-power as decimal64 with no
``units`` statement; its description gives the unit: "The output power for
this interface in .01 dBm". So 150 on the wire is 1.5 dBm.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, tapi_end_point
from twinlight.models.connectivity import OTSIA_CSEP_SPEC, ConnectivityService

_SVC = "/data/tapi-connectivity:connectivity-context/connectivity-service"


def _body(sip_a: str, sip_z: str, power=None) -> dict:
    end_points = [tapi_end_point("a", sip_a), tapi_end_point("z", sip_z)]
    if power is not None:
        for ep in end_points:
            ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0][
                "channel-output-power"] = power
    return {"tapi-connectivity:connectivity-service": [{"end-point": end_points}]}


def _reported_power(service: dict) -> float:
    return service["end-point"][0]["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC][
        "otsi-config"][0]["channel-output-power"]


@pytest.mark.parametrize("wire", [150, 150.0, "150"])
def test_channel_output_power_is_hundredths_of_a_dbm(mesh_app: TestClient, wire) -> None:
    refs = MeshRefs(mesh_app.app.state.context)
    resp = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"), wire))
    assert resp.status_code == 201, resp.text
    service = resp.json()["tapi-connectivity:connectivity-service"]
    assert mesh_app.app.state.context._service_launch_power[service["uuid"]] == 1.5
    assert _reported_power(service) == 150.0


@pytest.mark.parametrize("wire", ["abc", True, "nan"])
def test_malformed_power_is_422(mesh_app: TestClient, wire) -> None:
    refs = MeshRefs(mesh_app.app.state.context)
    resp = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"), wire))
    assert resp.status_code == 422
    assert "channel-output-power" in resp.text


def test_default_is_the_equipment_si_power(mesh_context) -> None:
    """Absent a request, the channel runs at SI power_dbm, which is also
    what the end-point reports (0.01 dBm units)."""
    ctx = mesh_context
    ctx._backend.spectral_defaults = dataclasses.replace(
        ctx._backend.spectral_defaults, power_dbm=2.0,
    )
    refs = MeshRefs(ctx)
    svc = ConnectivityService.model_validate({"end-point": [
        tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C")),
    ]})
    asyncio.run(ctx.add_service(svc))
    assert ctx._service_launch_power[svc.uuid] == 2.0
    assert _reported_power(svc.model_dump(by_alias=True)) == 200.0


def test_admission_propagates_at_the_requested_power(mesh_context, monkeypatch) -> None:
    ctx, seen = mesh_context, []
    real = ctx._backend.compute_baseline

    async def spy(path, fmt, **channel):
        seen.append(channel["launch_power_dbm"])
        return await real(path, fmt, **channel)

    monkeypatch.setattr(ctx._backend, "compute_baseline", spy)
    refs = MeshRefs(ctx)
    svc = ConnectivityService.model_validate(_body(
        refs.sip("trx A"), refs.sip("trx C"), -250,
    )["tapi-connectivity:connectivity-service"][0])
    asyncio.run(ctx.add_service(svc))
    assert seen and set(seen) == {-2.5}
    # The cached baseline is recomputed at the same power after invalidation.
    ctx.invalidate_baseline(svc.uuid)
    asyncio.run(ctx.get_or_compute_baseline(svc))
    assert seen[-1] == -2.5


def test_gnpy_offsets_the_service_channel_through_delta_pdb(gnpy_context, monkeypatch) -> None:
    """The service channel is emitted at P and carries delta_pdb = P - SI
    power_dbm, which GNPy's ROADMs keep; the rest of the comb has no offset."""
    from gnpy.core import info

    captured: dict = {}
    real = info.create_arbitrary_spectral_information

    def capture(frequency, **kwargs):
        captured.update(frequency=frequency, **kwargs)
        return real(frequency, **kwargs)

    monkeypatch.setattr(info, "create_arbitrary_spectral_information", capture)
    sip_a, sip_z = (s.uuid for s in gnpy_context.get_sips()[:2])
    path = gnpy_context.get_service_path(sip_a, sip_z)
    si = gnpy_context._backend.spectral_defaults
    asyncio.run(gnpy_context._backend.compute_baseline(
        path, "DP-QPSK", centre_frequency_hz=193.1e12, launch_power_dbm=si.power_dbm + 3,
    ))
    service = int(abs(captured["frequency"] - 193.1e12).argmin())
    delta = captured["delta_pdb_per_channel"]
    assert delta[service] == pytest.approx(3.0)
    assert (delta[[i for i in range(len(delta)) if i != service]] == 0).all()
    pch_dbm = 10 * np.log10(captured["pch"] * 1e3)
    assert pch_dbm[service] == pytest.approx(si.power_dbm + 3)


def test_egn_osnr_scales_with_launch_power(tmp_path) -> None:
    """GN model: ASE does not depend on the signal, so OSNR_ASE moves dB for dB
    with the launch power, while NLI makes GSNR move less."""
    from tests.conftest import _mesh_config
    from twinlight.state.context import TapiContext

    ctx = TapiContext(_mesh_config(tmp_path, "egn"))
    (path,) = ctx.candidate_paths("trx A", "trx C")

    def at(power: float):
        return asyncio.run(ctx._backend.compute_baseline(
            path, "DP-QPSK", centre_frequency_hz=193.1e12, launch_power_dbm=power,
        ))

    low, high = at(0.0), at(3.0)
    assert high.osnr_ase_db - low.osnr_ase_db == pytest.approx(3.0)
    assert high.gsnr_db - low.gsnr_db < 3.0


def test_launch_power_survives_a_snapshot(mesh_gnpy_context, tmp_path) -> None:
    ctx = mesh_gnpy_context
    refs = MeshRefs(ctx)
    svc = ConnectivityService.model_validate(_body(
        refs.sip("trx A"), refs.sip("trx C"), 120,
    )["tapi-connectivity:connectivity-service"][0])
    asyncio.run(ctx.add_service(svc))
    snap = ctx.snapshot(tmp_path / "s.json")

    ctx.restore_from(snap)
    assert ctx._service_launch_power[svc.uuid] == 1.2

    # A snapshot from before launch power was recorded restores the default.
    data = json.loads(snap.read_text())
    for alloc in data["service_allocation"].values():
        del alloc["launch_power_dbm"]
    snap.write_text(json.dumps(data))
    ctx.restore_from(snap)
    assert ctx._service_launch_power[svc.uuid] == ctx.default_launch_power_dbm
