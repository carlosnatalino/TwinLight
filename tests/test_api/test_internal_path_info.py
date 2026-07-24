"""Tests for internal path-info endpoint (Path UI: hops + GSNR estimate)."""

from __future__ import annotations


def _get_two_sip_uuids(app) -> tuple[str, str]:
    data = app.get("/data/tapi-common:context/service-interface-point").json()
    sips = data["tapi-common:context"]["service-interface-point"]
    assert len(sips) >= 2
    return sips[0]["uuid"], sips[1]["uuid"]


def test_path_info_returns_hops_and_optional_measurements(app) -> None:
    sip_a, sip_z = _get_two_sip_uuids(app)
    resp = app.get(
        f"/internal/path-info?sip_a={sip_a}&sip_z={sip_z}&modulation=DP-QPSK"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("sip-a") == sip_a
    assert data.get("sip-z") == sip_z
    assert data.get("modulation-format") == "DP-QPSK"
    assert "hops" in data
    assert isinstance(data["hops"], list)
    assert "total-fiber-km" in data
    if data["hops"]:
        hop = data["hops"][0]
        assert "uid" in hop
        assert "type" in hop
        assert hop["type"] in ("Transceiver", "Roadm")
    # measurements may be present when GNPy is loaded
    assert "measurements" in data


def test_path_info_422_when_missing_params(app) -> None:
    resp = app.get("/internal/path-info")
    assert resp.status_code == 422


def test_path_info_404_when_sip_not_found(app) -> None:
    sip_a, _ = _get_two_sip_uuids(app)
    resp = app.get(
        f"/internal/path-info?sip_a={sip_a}&sip_z=00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404
