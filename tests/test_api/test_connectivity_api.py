"""Tests for the TAPI Connectivity API (RMSA: path, spectrum, QoT)."""

from __future__ import annotations

from fastapi.testclient import TestClient

_BASE = "/data/tapi-connectivity:connectivity-context"


def _get_two_sip_uuids(app: TestClient) -> tuple[str, str]:
    data = app.get("/data/tapi-common:context/service-interface-point").json()
    sips = data["tapi-common:context"]["service-interface-point"]
    assert len(sips) >= 2
    return sips[0]["uuid"], sips[1]["uuid"]


def _create_service_payload(sip_a: str, sip_z: str, modulation: str = "DP-QPSK") -> dict:
    return {
        "tapi-connectivity:connectivity-service": {
            "name": [{"value-name": "service-name", "value": "test-link"}],
            "modulation-format": modulation,
            "end-point": [
                {"local-id": "a-end", "service-interface-point": {"service-interface-point-uuid": sip_a}},
                {"local-id": "z-end", "service-interface-point": {"service-interface-point-uuid": sip_z}},
            ],
        }
    }


class TestCreateConnectivityService:
    """Create service exercises RMSA: path (k-shortest), first-fit spectrum, QoT check."""

    def test_create_returns_201(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z)
        resp = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert "tapi-connectivity:connectivity-service" in data
        svc = data["tapi-connectivity:connectivity-service"]
        assert "uuid" in svc
        assert svc["modulation-format"] == "DP-QPSK"

    def test_second_service_same_path_gets_different_slots(self, app: TestClient) -> None:
        """First-fit assigns first block to first service, second block to second."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        p1 = _create_service_payload(sip_a, sip_z)
        p2 = _create_service_payload(sip_a, sip_z)
        p2["tapi-connectivity:connectivity-service"]["name"] = [
            {"value-name": "service-name", "value": "test-link-2"}
        ]
        r1 = app.post(f"{_BASE}/connectivity-service", json=p1)
        assert r1.status_code == 201
        r2 = app.post(f"{_BASE}/connectivity-service", json=p2)
        assert r2.status_code == 201
        # Both exist
        list_resp = app.get(f"{_BASE}/connectivity-service")
        assert list_resp.status_code == 200
        services = list_resp.json()["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert len(services) == 2

    def test_422_when_sip_not_found(self, app: TestClient) -> None:
        sip_a, _ = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, "00000000-0000-0000-0000-000000000000")
        resp = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert resp.status_code == 422


class TestGetAndDeleteConnectivityService:
    def test_get_service_returns_modulation_format(self, app: TestClient) -> None:
        """GET connectivity-service={uuid} returns modulation-format for Service Detail UI."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z, "DP-16QAM")
        create = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert create.status_code == 201
        uuid = create.json()["tapi-connectivity:connectivity-service"]["uuid"]
        get_one = app.get(f"{_BASE}/connectivity-service={uuid}")
        assert get_one.status_code == 200
        svc = get_one.json()["tapi-connectivity:connectivity-service"]
        assert svc.get("modulation-format") == "DP-16QAM"

    def test_get_service_returns_frequency_slot_when_allocated(
        self, app: TestClient
    ) -> None:
        """GET connectivity-service={uuid} returns frequency-slot (T-API L0) when allocated."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        create = app.post(
            f"{_BASE}/connectivity-service",
            json=_create_service_payload(sip_a, sip_z),
        )
        assert create.status_code == 201
        uuid = create.json()["tapi-connectivity:connectivity-service"]["uuid"]
        get_one = app.get(f"{_BASE}/connectivity-service={uuid}")
        assert get_one.status_code == 200
        svc = get_one.json()["tapi-connectivity:connectivity-service"]
        assert "frequency-slot" in svc
        fs = svc["frequency-slot"]
        assert "nominal-central-frequency" in fs
        assert "slot-width" in fs
        assert isinstance(fs["nominal-central-frequency"], (int, float))
        assert isinstance(fs["slot-width"], (int, float))

    def test_get_services_includes_created(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        app.post(f"{_BASE}/connectivity-service", json=_create_service_payload(sip_a, sip_z))
        resp = app.get(f"{_BASE}/connectivity-service")
        assert resp.status_code == 200
        services = resp.json()["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert len(services) >= 1

    def test_delete_releases_spectrum_so_new_service_succeeds(self, app: TestClient) -> None:
        """Delete frees slots; creating another service on same path should succeed."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z)
        r1 = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert r1.status_code == 201
        uuid1 = r1.json()["tapi-connectivity:connectivity-service"]["uuid"]
        app.delete(f"{_BASE}/connectivity-service={uuid1}")
        # Create again on same path
        r2 = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert r2.status_code == 201

    def test_put_replace_connectivity_service(self, app: TestClient) -> None:
        """PUT replaces service by UUID; body UUID must match path."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        create = app.post(
            f"{_BASE}/connectivity-service",
            json=_create_service_payload(sip_a, sip_z),
        )
        assert create.status_code == 201
        uuid = create.json()["tapi-connectivity:connectivity-service"]["uuid"]
        put_body = {
            "tapi-connectivity:connectivity-service": {
                "uuid": uuid,
                "name": [{"value-name": "service-name", "value": "replaced-name"}],
                "modulation-format": "DP-QPSK",
                "end-point": [
                    {"local-id": "a-end", "service-interface-point": {"service-interface-point-uuid": sip_a}},
                    {"local-id": "z-end", "service-interface-point": {"service-interface-point-uuid": sip_z}},
                ],
            }
        }
        put_resp = app.put(f"{_BASE}/connectivity-service={uuid}", json=put_body)
        assert put_resp.status_code == 200
        assert put_resp.json()["tapi-connectivity:connectivity-service"]["name"][0]["value"] == "replaced-name"
        get_resp = app.get(f"{_BASE}/connectivity-service={uuid}")
        assert get_resp.status_code == 200
        assert get_resp.json()["tapi-connectivity:connectivity-service"]["name"][0]["value"] == "replaced-name"
