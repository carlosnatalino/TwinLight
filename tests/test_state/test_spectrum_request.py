"""Requested spectrum, grid validation and the amplifier band.

T-API v2.6.0 lets a client name the spectrum
(mcg-connectivity-service-end-point-spec/mc-spectrum-config-pac/spectrum) or
the carrier (otsi-config/central-frequency), both optional. Given, they are
validated and used, or refused with the conflict named; absent, first-fit
assigns a block inside the band every amplifier on the path passes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, tapi_end_point
from twinlight.models.connectivity import MCG_CSEP_SPEC, OTSIA_CSEP_SPEC

_SVC = "/data/tapi-connectivity:connectivity-context/connectivity-service"
GRID_LOWER = 190_696_875_000_000   # 193.1 THz - 384.5 x 6.25 GHz
SLOT = 6_250_000_000
WIDTH = 9                          # 8 slots (50 GHz) + 1 guard
FIRST_IN_BAND = 93                 # first slot inside GNPy's 191.275 THz EDFA edge


def _block(start: int, width: int = WIDTH) -> tuple[int, int]:
    return GRID_LOWER + start * SLOT, GRID_LOWER + (start + width) * SLOT


def _body(sip_a: str, sip_z: str, *, spectrum=None, centre=None, mcg_extra=None,
          modulation: str = "DP-QPSK") -> dict:
    end_points = [tapi_end_point("a", sip_a, modulation), tapi_end_point("z", sip_z, modulation)]
    for ep in end_points:
        constraint = ep["layer-protocol-constraint"][0]
        if centre is not None:
            constraint[OTSIA_CSEP_SPEC]["otsi-config"][0]["central-frequency"] = centre
        if spectrum is not None or mcg_extra is not None:
            pac = {"local-id": "1"}
            if spectrum is not None:
                pac["spectrum"] = {"lower-frequency": spectrum[0], "upper-frequency": spectrum[1]}
            constraint[MCG_CSEP_SPEC] = {"mc-spectrum-config-pac": [pac], **(mcg_extra or {})}
    return {"tapi-connectivity:connectivity-service": [{"end-point": end_points}]}


def _assigned(service: dict) -> tuple[tuple[int, int], int]:
    constraint = service["end-point"][0]["layer-protocol-constraint"][0]
    band = constraint[MCG_CSEP_SPEC]["mc-spectrum-config-pac"][0]["spectrum"]
    centre = constraint[OTSIA_CSEP_SPEC]["otsi-config"][0]["central-frequency"]
    return (band["lower-frequency"], band["upper-frequency"]), centre


def _error(resp) -> str:
    return resp.json()["ietf-restconf:errors"]["error"][0]["error-message"]


@pytest.fixture
def refs(mesh_app: TestClient) -> MeshRefs:
    return MeshRefs(mesh_app.app.state.context)


class TestFirstFitInsideTheAmplifierBand:
    def test_first_block_starts_inside_the_edfa_band(self, mesh_app, refs) -> None:
        resp = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C")))
        assert resp.status_code == 201, resp.text
        (lower, upper), centre = _assigned(resp.json()["tapi-connectivity:connectivity-service"])
        assert (lower, upper) == _block(FIRST_IN_BAND)
        assert lower >= 191.275e12
        assert centre == (lower + upper) // 2

    def test_mask_follows_gnpy_is_in_band(self, mesh_context) -> None:
        """Every assignable slot passes gnpy.core.info.is_in_band for every band."""
        import numpy as np
        from gnpy.core.info import is_in_band

        (path,) = mesh_context.candidate_paths("trx A", "trx C")
        mask = mesh_context._assignable_slots(path)
        lower = GRID_LOWER + np.arange(len(mask)) * SLOT
        centres, widths = lower + SLOT / 2, np.full(len(mask), SLOT)
        bands = mesh_context._backend.amplifier_bands(path)
        expected = np.zeros(len(mask), dtype=bool)
        for f_min, f_max in bands:
            expected |= is_in_band(centres, widths, {"f_min": f_min, "f_max": f_max})
        assert (mask == expected).all()
        assert mask.argmax() == FIRST_IN_BAND

    def test_without_physics_the_whole_grid_is_assignable(self, app: TestClient) -> None:
        data = app.get("/data/tapi-common:context/service-interface-point").json()
        sips = [s["uuid"] for s in data["tapi-common:context"]["service-interface-point"]]
        resp = app.post(_SVC, json=_body(sips[0], sips[1]))
        (lower, _), _ = _assigned(resp.json()["tapi-connectivity:connectivity-service"])
        assert lower == GRID_LOWER


class TestRequestedSpectrum:
    @pytest.mark.parametrize("as_string", [False, True])
    def test_spectrum_is_used(self, mesh_app, refs, as_string: bool) -> None:
        block = _block(200)
        spectrum = tuple(str(f) for f in block) if as_string else block
        resp = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"),
                                              spectrum=spectrum))
        assert resp.status_code == 201, resp.text
        assert _assigned(resp.json()["tapi-connectivity:connectivity-service"]) == (
            block, sum(block) // 2)

    def test_central_frequency_alone_is_used(self, mesh_app, refs) -> None:
        block = _block(300)
        resp = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"),
                                              centre=sum(block) // 2))
        assert resp.status_code == 201, resp.text
        assert _assigned(resp.json()["tapi-connectivity:connectivity-service"])[0] == block

    def test_a_get_can_be_put_back_unchanged(self, mesh_app, refs) -> None:
        created = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"),
                                                 spectrum=_block(250)))
        service = created.json()["tapi-connectivity:connectivity-service"]
        uuid = service["uuid"]
        fetched = mesh_app.get(f"{_SVC}={uuid}").json()["tapi-connectivity:connectivity-service"]
        put = mesh_app.put(f"{_SVC}={uuid}", json={"tapi-connectivity:connectivity-service": fetched})
        assert put.status_code == 200, put.text
        assert _assigned(put.json()["tapi-connectivity:connectivity-service"]) == _assigned(service)

    def test_occupied_on_the_first_candidate_moves_to_the_next(self, mesh_app, refs) -> None:
        block = _block(200)
        # Occupy the block on the B → C span only, which only candidate 1 uses
        # (a service would also hold its transceivers' access edges).
        from twinlight.algorithms.spectrum_assignment import path_uids_to_edges

        ctx = mesh_app.app.state.context
        (best,) = ctx.candidate_paths("trx A", "trx C")
        span = best[best.index("roadm B"):best.index("roadm C") + 1]
        ctx._spectrum_state.allocate(path_uids_to_edges(span), 200, WIDTH)
        second = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"), spectrum=block))
        assert second.status_code == 201, second.text
        uuid = second.json()["tapi-connectivity:connectivity-service"]["uuid"]
        path = mesh_app.app.state.context._allocated_path_uids(uuid)
        assert refs.roadms(path) == ["A", "D", "C"]


class TestRefusals:
    @pytest.mark.parametrize(("kwargs", "named"), [
        ({"spectrum": (GRID_LOWER + 1, GRID_LOWER + 1 + WIDTH * SLOT)}, "not aligned"),
        ({"spectrum": _block(200, 8)}, "8 slots wide"),
        ({"spectrum": _block(-20)}, "outside the grid"),
        ({"spectrum": _block(200), "centre": sum(_block(201)) // 2}, "not the centre"),
        ({"centre": sum(_block(200)) // 2 + SLOT // 2}, "not the centre of any"),
        ({"spectrum": _block(200), "mcg_extra": {"mc-bandwidth-config-pac": []}},
         "mc-bandwidth-config-pac"),
        ({"spectrum": _block(200), "mcg_extra": {"number-of-mc": 2}}, "one media channel"),
    ])
    def test_malformed_request_is_422_naming_it(self, mesh_app, refs, kwargs, named) -> None:
        resp = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"), **kwargs))
        assert resp.status_code == 422, resp.text
        assert named in _error(resp)

    def test_end_points_must_agree(self, mesh_app, refs) -> None:
        body = _body(refs.sip("trx A"), refs.sip("trx C"), spectrum=_block(200))
        pac = body["tapi-connectivity:connectivity-service"][0]["end-point"][1][
            "layer-protocol-constraint"][0][MCG_CSEP_SPEC]["mc-spectrum-config-pac"][0]
        pac["spectrum"] = dict(zip(("lower-frequency", "upper-frequency"), _block(210), strict=True))
        resp = mesh_app.post(_SVC, json=body)
        assert resp.status_code == 422 and "different spectrum" in _error(resp)

    def test_other_grid_is_refused(self, mesh_app, refs) -> None:
        body = _body(refs.sip("trx A"), refs.sip("trx C"), spectrum=_block(200))
        pac = body["tapi-connectivity:connectivity-service"][0]["end-point"][0][
            "layer-protocol-constraint"][0][MCG_CSEP_SPEC]["mc-spectrum-config-pac"][0]
        pac["edge-frequency-constraint"] = {"grid-type": "GRID_TYPE_DWDM"}
        resp = mesh_app.post(_SVC, json=body)
        assert resp.status_code == 422 and "grid-type" in _error(resp)

    def test_outside_the_amplifier_band_is_409(self, mesh_app, refs) -> None:
        resp = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"),
                                              spectrum=_block(10)))
        assert resp.status_code == 409
        assert "outside the band every amplifier on the path passes" in _error(resp)
        assert "191.275000" in _error(resp)

    def test_occupied_everywhere_names_the_holder(self, mesh_app, refs) -> None:
        block = _block(200)
        first = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"), spectrum=block))
        holder = first.json()["tapi-connectivity:connectivity-service"]["uuid"]
        resp = mesh_app.post(_SVC, json=_body(refs.sip("trx A"), refs.sip("trx C"), spectrum=block))
        assert resp.status_code == 409
        assert f"occupied by service {holder}" in _error(resp)
