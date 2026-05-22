"""Tests for TAPI Path Computation API."""

from __future__ import annotations

from fastapi.testclient import TestClient

_BASE = "/data/tapi-path-computation:path-computation-context"
_SVC = f"{_BASE}/path-computation-service"


def _get_two_sip_uuids(app: TestClient) -> tuple[str, str]:
    data = app.get("/data/tapi-common:context/service-interface-point").json()
    sips = data["tapi-common:context"]["service-interface-point"]
    assert len(sips) >= 2
    return sips[0]["uuid"], sips[1]["uuid"]


class TestPathComputationContext:
    def test_get_context_returns_services(self, app: TestClient) -> None:
        resp = app.get(_BASE)
        assert resp.status_code == 200
        data = resp.json()
        assert "tapi-path-computation:path-computation-context" in data
        ctx = data["tapi-path-computation:path-computation-context"]
        assert "path-computation-service" in ctx
        assert len(ctx["path-computation-service"]) >= 1

    def test_get_services_list(self, app: TestClient) -> None:
        resp = app.get(f"{_BASE}/path-computation-service")
        assert resp.status_code == 200
        data = resp.json()
        assert "path-computation-service" in data["tapi-path-computation:path-computation-context"]


class TestComputePath:
    def test_compute_path_returns_path(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = {
            "tapi-path-computation:input": {
                "end-point": [
                    {"service-interface-point": {"service-interface-point-uuid": sip_a}},
                    {"service-interface-point": {"service-interface-point-uuid": sip_z}},
                ],
                "max-candidates": 1,
            }
        }
        resp = app.post(f"{_SVC}/compute-path", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "tapi-path-computation:output" in data
        out = data["tapi-path-computation:output"]
        assert "path" in out
        assert len(out["path"]) >= 1
        path0 = out["path"][0]
        assert "link" in path0
        assert "node" in path0
        assert len(path0["node"]) >= 2

    def test_compute_path_422_when_sip_not_found(self, app: TestClient) -> None:
        sip_a, _ = _get_two_sip_uuids(app)
        payload = {
            "tapi-path-computation:input": {
                "end-point": [
                    {"service-interface-point": {"service-interface-point-uuid": sip_a}},
                    {"service-interface-point": {"service-interface-point-uuid": "00000000-0000-0000-0000-000000000000"}},
                ],
                "max-candidates": 1,
            }
        }
        resp = app.post(f"{_SVC}/compute-path", json=payload)
        assert resp.status_code == 422
