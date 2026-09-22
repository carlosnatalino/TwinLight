"""Tests for diagram generation API endpoints.

Tests constellation and eye-diagram endpoints under /internal/services/{uuid}/
for correct response structure and parameter validation.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point


def _create_service(app: TestClient) -> str:
    """Create a test connectivity service and return its UUID."""
    sips_resp = app.get("/data/tapi-common:context/service-interface-point")
    sips = sips_resp.json()["tapi-common:context"]["service-interface-point"]
    sip_a, sip_z = sips[0]["uuid"], sips[1]["uuid"]

    body = {
        "tapi-connectivity:connectivity-service": {
            "name": [{"value-name": "service-name", "value": "test-diagram"}],
            "end-point": [
                tapi_end_point("a-end", sip_a),
                tapi_end_point("z-end", sip_z),
            ],
        }
    }
    resp = app.post(
        "/data/tapi-connectivity:connectivity-context/connectivity-service",
        json=body,
    )
    assert resp.status_code == 201
    return resp.json()["tapi-connectivity:connectivity-service"]["uuid"]


class TestConstellationEndpoint:
    def test_returns_200_for_existing_service(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(f"/internal/services/{svc_uuid}/constellation")
        assert resp.status_code == 200

    def test_returns_404_for_unknown_service(self, app: TestClient) -> None:
        resp = app.get("/internal/services/nonexistent/constellation")
        assert resp.status_code == 404

    def test_response_has_required_keys(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(f"/internal/services/{svc_uuid}/constellation")
        data = resp.json()
        assert "service-uuid" in data
        assert "modulation-format" in data
        assert "n_symbols" in data
        assert "i" in data
        assert "q" in data
        assert "measurements" in data

    def test_i_and_q_are_lists(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(f"/internal/services/{svc_uuid}/constellation")
        data = resp.json()
        assert isinstance(data["i"], list)
        assert isinstance(data["q"], list)
        assert len(data["i"]) == len(data["q"])

    def test_respects_n_symbols_parameter(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(
            f"/internal/services/{svc_uuid}/constellation?n_symbols=500"
        )
        data = resp.json()
        assert len(data["i"]) == 500
        assert data["n_symbols"] == 500

    def test_validates_n_symbols_min(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(
            f"/internal/services/{svc_uuid}/constellation?n_symbols=10"
        )
        assert resp.status_code == 422

    def test_validates_n_symbols_max(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(
            f"/internal/services/{svc_uuid}/constellation?n_symbols=200000"
        )
        assert resp.status_code == 422

    def test_measurements_includes_gsnr(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(f"/internal/services/{svc_uuid}/constellation")
        data = resp.json()
        assert "gsnr-db" in data["measurements"]
        assert "linewidth-hz" in data["measurements"]


class TestEyeDiagramEndpoint:
    def test_returns_200_for_existing_service(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(f"/internal/services/{svc_uuid}/eye-diagram")
        assert resp.status_code == 200

    def test_returns_404_for_unknown_service(self, app: TestClient) -> None:
        resp = app.get("/internal/services/nonexistent/eye-diagram")
        assert resp.status_code == 404

    def test_response_has_required_keys(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(f"/internal/services/{svc_uuid}/eye-diagram")
        data = resp.json()
        assert "service-uuid" in data
        assert "modulation-format" in data
        assert "time_ns" in data
        assert "traces" in data
        assert "symbol_period_ns" in data
        assert "n_traces" in data
        assert "measurements" in data

    def test_traces_is_list_of_lists(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        url = f"/internal/services/{svc_uuid}/eye-diagram?n_traces=10"
        resp = app.get(url)
        data = resp.json()
        assert isinstance(data["traces"], list)
        assert isinstance(data["traces"][0], list)

    def test_respects_n_traces_parameter(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(
            f"/internal/services/{svc_uuid}/eye-diagram?n_traces=50"
        )
        data = resp.json()
        assert data["n_traces"] <= 50

    def test_validates_n_traces_min(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(
            f"/internal/services/{svc_uuid}/eye-diagram?n_traces=5"
        )
        assert resp.status_code == 422

    def test_validates_samples_per_symbol(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(
            f"/internal/services/{svc_uuid}/eye-diagram?samples_per_symbol=8"
        )
        assert resp.status_code == 422

    def test_measurements_includes_gsnr_and_pmd(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(f"/internal/services/{svc_uuid}/eye-diagram")
        data = resp.json()
        assert "gsnr-db" in data["measurements"]
        assert "pmd-ps" in data["measurements"]
