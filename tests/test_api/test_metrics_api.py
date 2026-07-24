"""Tests for the Prometheus exposition endpoint (``GET /metrics``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig

FIXTURES = Path(__file__).parent.parent / "fixtures"
_GNPY_EQUIPMENT = (
    Path(__file__).resolve().parents[2]
    / "venv/lib/python3.12/site-packages/gnpy/example-data/eqpt_config.json"
)


def _make_client(backend: str = "gnpy") -> TestClient:
    if backend == "gnpy" and not _GNPY_EQUIPMENT.exists():
        pytest.skip("gnpy equipment file not installed")
    cfg = TwinConfig(
        gnpy=GnpyConfig(
            topology=FIXTURES / "edfa_example_network.json",
            equipment=_GNPY_EQUIPMENT if backend == "gnpy" else None,
        ),
        physics=PhysicsConfig(backend=backend),  # type: ignore[arg-type]
    )
    return TestClient(create_app(cfg))


def _create_service(client: TestClient) -> str:
    sips = client.get(
        "/data/tapi-common:context/service-interface-point"
    ).json()["tapi-common:context"]["service-interface-point"]
    sip_a, sip_z = sips[0]["uuid"], sips[1]["uuid"]
    body = {
        "tapi-connectivity:connectivity-service": {
            "name": [{"value-name": "service-name", "value": "prom-svc"}],
            "modulation-format": "DP-QPSK",
            "end-point": [
                {"local-id": "a", "service-interface-point": {
                    "service-interface-point-uuid": sip_a,
                }},
                {"local-id": "z", "service-interface-point": {
                    "service-interface-point-uuid": sip_z,
                }},
            ],
        }
    }
    r = client.post(
        "/data/tapi-connectivity:connectivity-context/connectivity-service",
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()["tapi-connectivity:connectivity-service"]["uuid"]


class TestExpositionFormat:
    def test_endpoint_returns_200_and_text_format(self) -> None:
        client = _make_client()
        r = client.get("/metrics")
        assert r.status_code == 200
        ctype = r.headers.get("content-type", "")
        # Prometheus 0.4 text format identifier.
        assert "text/plain" in ctype
        assert "version=" in ctype

    def test_info_includes_backend_label(self) -> None:
        client = _make_client()
        body = client.get("/metrics").text
        assert 'twinlight_info{backend="gnpy"} 1.0' in body

    def test_static_help_and_type_lines_present(self) -> None:
        client = _make_client()
        body = client.get("/metrics").text
        # Every gauge family must have its HELP + TYPE prologue (this is
        # what makes the format scrapable; missing lines break promtool).
        for metric in [
            "twinlight_info",
            "twinlight_services_total",
            "twinlight_failed_links_total",
            "twinlight_rmsa_qot_margin_db",
            "twinlight_transient_enabled",
            "twinlight_fiber_loss_coef_db_per_km",
            "twinlight_fiber_failed",
        ]:
            assert f"# HELP {metric} " in body, metric
            assert f"# TYPE {metric} gauge" in body, metric


class TestConfigGauges:
    def test_fiber_loss_coef_matches_topology_default(self) -> None:
        client = _make_client()
        body = client.get("/metrics").text
        # Fixture declares loss_coef: 0.2 on every fiber.
        lines = [
            ln for ln in body.splitlines()
            if ln.startswith("twinlight_fiber_loss_coef_db_per_km{")
        ]
        assert lines, "no fiber loss_coef metric emitted"
        for ln in lines:
            assert ln.endswith(" 0.2"), ln

    def test_fiber_loss_reflects_config_set_mutation(self) -> None:
        client = _make_client()
        fiber_uid = next(
            d["uid"] for d in client.get("/config/get").json()["devices"]
            if d["type"] == "Fiber"
        )
        r = client.post(
            "/config/set",
            json={"devices": {fiber_uid: {"loss_coef": 0.27}}},
        )
        assert r.status_code == 200
        body = client.get("/metrics").text
        # Quote-escaping isn't needed for our UIDs but be loose: just
        # assert that the right UID line ends in 0.27.
        line = next(
            ln for ln in body.splitlines()
            if ln.startswith("twinlight_fiber_loss_coef_db_per_km{")
            and fiber_uid in ln
        )
        assert line.endswith(" 0.27")

    def test_fiber_failed_indicator_flips(self) -> None:
        client = _make_client()
        fiber_uid = next(
            d["uid"] for d in client.get("/config/get").json()["devices"]
            if d["type"] == "Fiber"
        )
        client.post(
            "/config/set",
            json={"devices": {fiber_uid: {"failed": True}}},
        )
        body = client.get("/metrics").text
        line = next(
            ln for ln in body.splitlines()
            if ln.startswith("twinlight_fiber_failed{")
            and fiber_uid in ln
        )
        assert line.endswith(" 1.0")
        # failed_links_total also reflects this.
        assert "twinlight_failed_links_total 1.0" in body

    def test_transient_enabled_reflects_yaml(self) -> None:
        client = _make_client()
        body = client.get("/metrics").text
        # Default config has phase_noise enabled, environmental disabled.
        assert (
            'twinlight_transient_enabled{model="phase_noise"} 1.0' in body
        )
        assert (
            'twinlight_transient_enabled{model="environmental"} 0.0' in body
        )

    def test_rmsa_margin_updates_after_twin_override(self) -> None:
        client = _make_client()
        client.post(
            "/config/set",
            json={"twin": {"rmsa": {"qot_margin_db": 2.5}}},
        )
        body = client.get("/metrics").text
        assert "twinlight_rmsa_qot_margin_db 2.5" in body


class TestOpmGauges:
    def test_service_opm_metrics_emitted_for_admitted_service(self) -> None:
        client = _make_client()
        svc_uuid = _create_service(client)
        body = client.get("/metrics").text
        # Every OPM metric must produce at least one sample whose label
        # set carries this service's UUID.
        for metric in (
            "twinlight_opm_gsnr_db",
            "twinlight_opm_osnr_db",
            "twinlight_opm_q_factor_db",
            "twinlight_opm_pre_fec_ber",
            "twinlight_opm_chromatic_dispersion_ps_per_nm",
            "twinlight_opm_pmd_ps",
        ):
            assert any(
                ln.startswith(f"{metric}{{")
                and svc_uuid in ln
                for ln in body.splitlines()
            ), metric

    def test_service_link_failed_indicator(self) -> None:
        client = _make_client()
        svc_uuid = _create_service(client)
        # Healthy: 0.
        body = client.get("/metrics").text
        line = next(
            ln for ln in body.splitlines()
            if ln.startswith("twinlight_service_link_failed{")
            and svc_uuid in ln
        )
        assert line.endswith(" 0.0")

        # Fail the fiber → indicator flips, OPM gauges drop their sample.
        fiber_uid = next(
            d["uid"] for d in client.get("/config/get").json()["devices"]
            if d["type"] == "Fiber"
        )
        # Find a fiber actually on the service's path.
        for candidate in [
            d["uid"] for d in client.get("/config/get").json()["devices"]
            if d["type"] == "Fiber"
        ]:
            single = client.get(f"/config/devices/{candidate}").json()
            if svc_uuid in single["affected_services"]:
                fiber_uid = candidate
                break
        client.post(
            "/config/set",
            json={"devices": {fiber_uid: {"failed": True}}},
        )
        body = client.get("/metrics").text
        line = next(
            ln for ln in body.splitlines()
            if ln.startswith("twinlight_service_link_failed{")
            and svc_uuid in ln
        )
        assert line.endswith(" 1.0")
        # GSNR for this service should NOT be emitted while it's failed.
        gsnr_lines = [
            ln for ln in body.splitlines()
            if ln.startswith("twinlight_opm_gsnr_db{")
            and svc_uuid in ln
        ]
        assert not gsnr_lines


class TestEgnBackend:
    def test_metrics_endpoint_works_under_egn(self) -> None:
        client = _make_client(backend="egn")
        body = client.get("/metrics").text
        assert 'twinlight_info{backend="egn"} 1.0' in body
        # EGN exposes fiber loss + edfa nf; gain_target / tilt_target /
        # out_voa / roadm target are NOT in its schema, so those
        # gauges have no samples (HELP/TYPE lines are still present —
        # see TestExpositionFormat — but the data lines are missing).
        assert "twinlight_fiber_loss_coef_db_per_km{" in body
        gain_target_samples = [
            ln for ln in body.splitlines()
            if ln.startswith("twinlight_edfa_gain_target_db{")
        ]
        assert not gain_target_samples
