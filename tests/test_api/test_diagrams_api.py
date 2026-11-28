"""Tests for diagram generation API endpoints.

Tests constellation and eye-diagram endpoints under /internal/services/{uuid}/
for correct response structure and parameter validation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.api import internal


def _create_service(app: TestClient) -> str:
    """Create a test connectivity service and return its UUID."""
    sips_resp = app.get("/restconf/data/tapi-common:context/service-interface-point")
    sips = sips_resp.json()["tapi-common:service-interface-point"]
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
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context",
        json=body,
    )
    assert resp.status_code == 201
    return resp.json()["tapi-connectivity:connectivity-service"][0]["uuid"]


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
        assert resp.status_code == 400
        assert resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "invalid-value"

    def test_validates_n_symbols_max(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(
            f"/internal/services/{svc_uuid}/constellation?n_symbols=200000"
        )
        assert resp.status_code == 400
        assert resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "invalid-value"

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
        assert resp.status_code == 400
        assert resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "invalid-value"

    def test_validates_samples_per_symbol(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(
            f"/internal/services/{svc_uuid}/eye-diagram?samples_per_symbol=8"
        )
        assert resp.status_code == 400
        assert resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "invalid-value"

    def test_measurements_includes_gsnr_and_pmd(self, app: TestClient) -> None:
        svc_uuid = _create_service(app)
        resp = app.get(f"/internal/services/{svc_uuid}/eye-diagram")
        data = resp.json()
        assert "gsnr-db" in data["measurements"]
        assert "pmd-ps" in data["measurements"]


class TestDiagramsShareTheOpmComputation:
    """A diagram is drawn from the GSNR the OPM endpoints, the stream and
    Prometheus report for the same instant: the one ``sample_service_opm``
    computation, EDFA tracker included."""

    def _in_flight_excursion(self, gnpy_app: TestClient) -> tuple[str, float]:
        """A service just admitted next to another, and an instant inside the
        EDFA add transient that the second admission set off."""
        first = _create_service(gnpy_app)
        _create_service(gnpy_app)
        tracker = gnpy_app.app.state.context.edfa_tracker
        t_event = max(ev.event_time for ev in tracker._last_event.values())
        return first, t_event + 1e-6

    def test_constellation_gsnr_is_the_opm_gsnr(
        self, gnpy_app: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        svc, t = self._in_flight_excursion(gnpy_app)
        monkeypatch.setattr(internal, "wall_clock", lambda: t)
        opm = gnpy_app.get(f"/internal/opm/{svc}").json()["measurements"]
        resp = gnpy_app.get(f"/internal/services/{svc}/constellation?n_symbols=100")
        assert resp.status_code == 200
        assert resp.json()["measurements"]["gsnr-db"] == opm["gsnr-db"]
        # The instant is inside the transient: the tracker moved the GSNR,
        # so a reader that skipped it would not match.
        ctx = gnpy_app.app.state.context
        baseline = ctx._baseline_cache[svc]
        assert opm["gsnr-db"] != pytest.approx(baseline.gsnr_db, abs=1e-3)

    def test_eye_diagram_gsnr_and_pmd_are_the_opm_values(
        self, gnpy_app: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        svc, t = self._in_flight_excursion(gnpy_app)
        monkeypatch.setattr(internal, "wall_clock", lambda: t)
        opm = gnpy_app.get(f"/internal/opm/{svc}").json()["measurements"]
        resp = gnpy_app.get(f"/internal/services/{svc}/eye-diagram?n_traces=10")
        assert resp.status_code == 200
        measurements = resp.json()["measurements"]
        assert measurements["gsnr-db"] == opm["gsnr-db"]
        assert measurements["pmd-ps"] == opm["pmd-ps"]

    @pytest.mark.parametrize("kind", ["constellation", "eye-diagram"])
    def test_a_service_with_no_signal_has_nothing_to_draw(
        self, gnpy_app: TestClient, kind: str,
    ) -> None:
        from gnpy.core.elements import Fiber

        svc = _create_service(gnpy_app)
        ctx = gnpy_app.app.state.context
        path = ctx._allocated_path_uids(svc)
        fiber = next(uid for uid in path if isinstance(ctx._gnpy_uid_map[uid], Fiber))
        ctx.set_link_failed(fiber, True)
        resp = gnpy_app.get(f"/internal/services/{svc}/{kind}")
        assert resp.status_code == 409
        assert "link-failed" in resp.text
