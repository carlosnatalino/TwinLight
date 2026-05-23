"""HTTP tests for the runtime /config plane (M4)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _device_uids_by_type(client: TestClient) -> dict[str, list[str]]:
    """Group device UIDs from GET /config/get by GNPy element type."""
    state = client.get("/config/get").json()
    by_type: dict[str, list[str]] = {}
    for entry in state["devices"]:
        by_type.setdefault(entry["type"], []).append(entry["uid"])
    return by_type


def _create_service_via_api(client: TestClient) -> str:
    """Create a service A→Z via the public TAPI route; return its UUID."""
    sips = client.get(
        "/data/tapi-common:context/service-interface-point"
    ).json()["tapi-common:context"]["service-interface-point"]
    sip_a, sip_z = sips[0]["uuid"], sips[1]["uuid"]
    body = {
        "tapi-connectivity:connectivity-service": {
            "name": [{"value-name": "service-name", "value": "config-api-test"}],
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
    base = "/data/tapi-connectivity:connectivity-context"
    r = client.post(f"{base}/connectivity-service", json=body)
    assert r.status_code == 201, r.text
    return r.json()["tapi-connectivity:connectivity-service"]["uuid"]


class TestGetConfig:
    def test_returns_devices_and_schema(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get("/config/get")
        assert r.status_code == 200
        body = r.json()
        assert {"devices", "failed_links", "element_overrides",
                "twin", "allowed_attributes",
                "allowed_twin_keys"} <= body.keys()
        # Fixture topology has at least one Fiber and one Edfa.
        kinds = {d["type"] for d in body["devices"]}
        assert {"Fiber", "Edfa", "Roadm"} <= kinds
        # Schema covers each type.
        assert {"Fiber", "Edfa", "Roadm"} <= body["allowed_attributes"].keys()

    def test_503_when_gnpy_not_loaded(self, app: TestClient) -> None:
        # The plain `app` fixture uses an empty equipment file → no GNPy.
        r = app.get("/config/get")
        assert r.status_code == 503


class TestGetDevice:
    def test_single_fiber(self, gnpy_app: TestClient) -> None:
        fibers = _device_uids_by_type(gnpy_app)["Fiber"]
        r = gnpy_app.get(f"/config/devices/{fibers[0]}")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "Fiber"
        assert "loss_coef" in body["attributes"]
        assert body["failed"] is False

    def test_unknown_uid_404(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get("/config/devices/no-such-thing")
        assert r.status_code == 404


class TestSetConfig:
    def test_mutate_fiber_loss_records_override_and_invalidates(
        self, gnpy_app: TestClient
    ) -> None:
        svc_uuid = _create_service_via_api(gnpy_app)
        fibers = _device_uids_by_type(gnpy_app)["Fiber"]
        # Pick a fiber actually on the service's path so we get an
        # invalidation entry back.
        target = None
        for fiber_uid in fibers:
            r = gnpy_app.get(f"/config/devices/{fiber_uid}")
            if svc_uuid in r.json()["affected_services"]:
                target = fiber_uid
                break
        assert target is not None, "no fiber on the test service's path"

        r = gnpy_app.post(
            "/config/set",
            json={"devices": {target: {"loss_coef": 0.27}}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["invalidated_services"][target] == [svc_uuid]

        # The override is reflected in /config/get.
        state = gnpy_app.get("/config/get").json()
        assert state["element_overrides"][target]["loss_coef"] == 0.27

    def test_twin_override_runtime_safe(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(
            "/config/set",
            json={"twin": {"rmsa": {"qot_margin_db": 3.5}}},
        )
        assert r.status_code == 200
        assert r.json()["applied"]["twin"]["rmsa"]["qot_margin_db"] == 3.5

        state = gnpy_app.get("/config/get").json()
        assert state["twin"]["rmsa"]["qot_margin_db"] == 3.5

    def test_fail_fiber_disables_link_and_marks_state(
        self, gnpy_app: TestClient
    ) -> None:
        fiber_uid = _device_uids_by_type(gnpy_app)["Fiber"][0]
        r = gnpy_app.post(
            "/config/set",
            json={"devices": {fiber_uid: {"failed": True}}},
        )
        assert r.status_code == 200
        assert fiber_uid in r.json()["failed_links"]

        state = gnpy_app.get("/config/get").json()
        assert fiber_uid in state["failed_links"]

        # Restore.
        r = gnpy_app.post(
            "/config/set",
            json={"devices": {fiber_uid: {"failed": False}}},
        )
        assert r.status_code == 200
        assert fiber_uid not in r.json()["failed_links"]

    def test_out_of_range_value_returns_400(self, gnpy_app: TestClient) -> None:
        fiber_uid = _device_uids_by_type(gnpy_app)["Fiber"][0]
        r = gnpy_app.post(
            "/config/set",
            json={"devices": {fiber_uid: {"loss_coef": 999.0}}},
        )
        assert r.status_code == 400
        # RESTCONF error envelope.
        body = r.json()
        assert "ietf-restconf:errors" in body

    def test_unknown_uid_returns_404(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(
            "/config/set",
            json={"devices": {"no-such-uid": {"loss_coef": 0.2}}},
        )
        assert r.status_code == 404

    def test_unknown_twin_key_returns_400(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(
            "/config/set",
            json={"twin": {"gnpy": {"topology": "/etc/passwd"}}},
        )
        assert r.status_code == 400

    def test_redesign_flag_501(self, gnpy_app: TestClient) -> None:
        # M5 wires this; for now it must clearly fail rather than silently
        # appear to take effect.
        r = gnpy_app.post(
            "/config/set",
            json={"redesign": True, "devices": {}},
        )
        assert r.status_code == 501

    def test_empty_body_is_noop(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post("/config/set", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["invalidated_services"] == {}
        assert body["applied"]["devices"] == {}

    def test_post_must_be_object(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post("/config/set", json=[1, 2, 3])
        # FastAPI rejects non-object bodies for dict typing with 422.
        assert r.status_code in (400, 422)
