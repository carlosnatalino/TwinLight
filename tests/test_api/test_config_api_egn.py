"""End-to-end /config API tests under the EGN backend (M5).

The router code is the same for both backends — these tests prove the
backend-agnostic surface holds: shared knobs (loss_coef, nf0, failed)
work, GNPy-only knobs return 400, and redesign=true returns 501.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig


FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def egn_app() -> TestClient:
    cfg = TwinConfig(
        gnpy=GnpyConfig(topology=FIXTURES / "edfa_example_network.json"),
        physics=PhysicsConfig(backend="egn"),
    )
    return TestClient(create_app(cfg))


def _create_service(client: TestClient) -> str:
    sips = client.get(
        "/data/tapi-common:context/service-interface-point"
    ).json()["tapi-common:context"]["service-interface-point"]
    sip_a, sip_z = sips[0]["uuid"], sips[1]["uuid"]
    body = {
        "tapi-connectivity:connectivity-service": {
            "name": [{"value-name": "service-name", "value": "egn-api"}],
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


class TestGetConfigUnderEgn:
    def test_advertises_backend_name(self, egn_app: TestClient) -> None:
        body = egn_app.get("/config/get").json()
        assert body["backend"] == "egn"

    def test_devices_list_only_writable_kinds(
        self, egn_app: TestClient
    ) -> None:
        body = egn_app.get("/config/get").json()
        # EGN's allow-list covers Fiber + Edfa only — ROADM has no
        # writable attrs, so it must NOT show up in the devices list.
        kinds = {d["type"] for d in body["devices"]}
        assert kinds == {"Fiber", "Edfa"}

    def test_supported_attributes_advertises_egn_subset(
        self, egn_app: TestClient
    ) -> None:
        attrs = egn_app.get("/config/get").json()["allowed_attributes"]
        assert set(attrs["Fiber"]) == {"loss_coef", "att_in"}
        assert set(attrs["Edfa"]) == {"nf0"}
        # ROADM schema is exposed but empty under EGN.
        assert attrs["Roadm"] == {}


class TestSetConfigUnderEgn:
    def test_loss_coef_mutation_and_invalidation(
        self, egn_app: TestClient
    ) -> None:
        svc_uuid = _create_service(egn_app)
        fiber_uid = next(
            d["uid"] for d in egn_app.get("/config/get").json()["devices"]
            if d["type"] == "Fiber"
        )
        r = egn_app.post(
            "/config/set",
            json={"devices": {fiber_uid: {"loss_coef": 0.30}}},
        )
        assert r.status_code == 200, r.text
        assert svc_uuid in r.json()["invalidated_services"][fiber_uid]

        state = egn_app.get("/config/get").json()
        assert state["element_overrides"][fiber_uid]["loss_coef"] == 0.30

    def test_nf0_mutation(self, egn_app: TestClient) -> None:
        edfa_uid = next(
            d["uid"] for d in egn_app.get("/config/get").json()["devices"]
            if d["type"] == "Edfa"
        )
        r = egn_app.post(
            "/config/set",
            json={"devices": {edfa_uid: {"nf0": 6.5}}},
        )
        assert r.status_code == 200, r.text
        single = egn_app.get(f"/config/devices/{edfa_uid}").json()
        assert single["attributes"]["nf0"] == pytest.approx(6.5)

    def test_failed_toggle(self, egn_app: TestClient) -> None:
        fiber_uid = next(
            d["uid"] for d in egn_app.get("/config/get").json()["devices"]
            if d["type"] == "Fiber"
        )
        r = egn_app.post(
            "/config/set",
            json={"devices": {fiber_uid: {"failed": True}}},
        )
        assert r.status_code == 200
        assert fiber_uid in r.json()["failed_links"]

        r = egn_app.post(
            "/config/set",
            json={"devices": {fiber_uid: {"failed": False}}},
        )
        assert r.status_code == 200
        assert fiber_uid not in r.json()["failed_links"]

    def test_gnpy_only_tilt_target_returns_400(
        self, egn_app: TestClient
    ) -> None:
        edfa_uid = next(
            d["uid"] for d in egn_app.get("/config/get").json()["devices"]
            if d["type"] == "Edfa"
        )
        r = egn_app.post(
            "/config/set",
            json={"devices": {edfa_uid: {"tilt_target": 0.5}}},
        )
        assert r.status_code == 400
        body = r.json()
        assert "ietf-restconf:errors" in body
        err_msg = body["ietf-restconf:errors"]["error"][0]["error-message"]
        assert "EGN" in err_msg

    def test_gnpy_only_target_pch_out_db_returns_400(
        self, egn_app: TestClient
    ) -> None:
        # ROADMs aren't in EGN's devices list, but a client may still
        # POST against a known ROADM UID — the backend must reject it.
        roadm_uid = "roadm Brest"
        r = egn_app.post(
            "/config/set",
            json={"devices": {roadm_uid: {"target_pch_out_db": -18.0}}},
        )
        assert r.status_code == 400

    def test_redesign_returns_501(self, egn_app: TestClient) -> None:
        r = egn_app.post(
            "/config/set",
            json={"redesign": True, "devices": {}},
        )
        assert r.status_code == 501
        body = r.json()
        assert "EGN" in body["ietf-restconf:errors"]["error"][0]["error-message"]

    def test_twin_overrides_work_under_egn(
        self, egn_app: TestClient
    ) -> None:
        # Twin-config overrides are backend-agnostic; this just
        # confirms the EGN path doesn't accidentally block them.
        r = egn_app.post(
            "/config/set",
            json={"twin": {"rmsa": {"qot_margin_db": 2.5}}},
        )
        assert r.status_code == 200
        assert r.json()["applied"]["twin"]["rmsa"]["qot_margin_db"] == 2.5
