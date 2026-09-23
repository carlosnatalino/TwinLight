"""The grid parameters, and their absence from the T-API surface.

These three leaves are names this project invented — T-API v2.6.0 defines no
``spectrum-context`` container to hold them. Publishing them under the
``tapi-photonic-media`` prefix, as an earlier release did, gave a client no
way to tell they were not standard, so they moved to ``/internal/``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestInternalSpectrumContext:
    def test_reports_the_configured_grid(self, app: TestClient) -> None:
        resp = app.get("/internal/spectrum-context")
        assert resp.status_code == 200
        assert resp.json() == {
            "num-slots": 768,
            "slot-width-ghz": 6.25,
            "nominal-central-frequency-thz": 193.1,
        }

    def test_follows_the_config(self, twin_config) -> None:
        from twinlight.app import create_app

        twin_config.spectrum.num_slots = 320
        twin_config.spectrum.slot_width_ghz = 12.5
        client = TestClient(create_app(twin_config))
        body = client.get("/internal/spectrum-context").json()
        assert body["num-slots"] == 320
        assert body["slot-width-ghz"] == 12.5


class TestNotOnTheTapiSurface:
    def test_gone_from_data(self, app: TestClient) -> None:
        for path in (
            "/data/tapi-photonic-media:spectrum-context",
            "/restconf/data/tapi-photonic-media:spectrum-context",
        ):
            assert app.get(path).status_code == 404, path

    def test_internal_responses_are_not_yang_data(
        self, app: TestClient
    ) -> None:
        """It is not a RESTCONF resource, so it must not claim to be one."""
        resp = app.get("/internal/spectrum-context")
        assert "yang-data" not in resp.headers["content-type"]
