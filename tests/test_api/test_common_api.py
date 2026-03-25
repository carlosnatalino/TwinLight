"""Tests for the TAPI Common API endpoints."""

from fastapi.testclient import TestClient


class TestGetContext:
    def test_returns_200(self, app: TestClient) -> None:
        resp = app.get("/data/tapi-common:context")
        assert resp.status_code == 200

    def test_has_tapi_context_key(self, app: TestClient) -> None:
        data = app.get("/data/tapi-common:context").json()
        assert "tapi-common:context" in data

    def test_context_has_sips(self, app: TestClient) -> None:
        ctx = app.get("/data/tapi-common:context").json()["tapi-common:context"]
        assert "service-interface-point" in ctx
        assert len(ctx["service-interface-point"]) == 2

    def test_context_has_topology_context(self, app: TestClient) -> None:
        ctx = app.get("/data/tapi-common:context").json()["tapi-common:context"]
        assert "tapi-topology:topology-context" in ctx

    def test_content_type_yang_data(self, app: TestClient) -> None:
        resp = app.get("/data/tapi-common:context")
        assert resp.headers["content-type"].startswith("application/yang-data+json")


class TestGetSips:
    def test_returns_200(self, app: TestClient) -> None:
        resp = app.get("/data/tapi-common:context/service-interface-point")
        assert resp.status_code == 200

    def test_returns_sip_list(self, app: TestClient) -> None:
        data = app.get("/data/tapi-common:context/service-interface-point").json()
        sips = data["tapi-common:context"]["service-interface-point"]
        assert len(sips) == 2


class TestGetSipByUuid:
    def test_returns_existing_sip(self, app: TestClient) -> None:
        # Get a SIP uuid first
        data = app.get("/data/tapi-common:context/service-interface-point").json()
        sip_uuid = data["tapi-common:context"]["service-interface-point"][0]["uuid"]
        resp = app.get(f"/data/tapi-common:context/service-interface-point={sip_uuid}")
        assert resp.status_code == 200

    def test_404_for_unknown_sip(self, app: TestClient) -> None:
        resp = app.get("/data/tapi-common:context/service-interface-point=nonexistent")
        assert resp.status_code == 404
