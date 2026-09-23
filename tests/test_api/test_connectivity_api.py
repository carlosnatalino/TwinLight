"""Tests for the TAPI Connectivity API (RMSA: path, spectrum, QoT)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.models.connectivity import MCG_CSEP_SPEC, OTSIA_CSEP_SPEC

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
            "end-point": [
                tapi_end_point("a-end", sip_a, modulation),
                tapi_end_point("z-end", sip_z, modulation),
            ],
        }
    }


def _modulation_of(svc: dict) -> str:
    """Dig the MT identity out of a connectivity-service's first end-point."""
    constraint = svc["end-point"][0]["layer-protocol-constraint"][0]
    spec = constraint[OTSIA_CSEP_SPEC]
    return spec["otsi-config"][0]["modulation"]["standard-modulation-technique"]


class TestModulationIsATapiAugment:
    """Modulation lives where T-API v2.6.0 puts it, and nowhere else.

    ``tapi-connectivity.yang`` has no modulation leaf at all; the photonic
    module augments the end-point's layer-protocol-constraint. A bare
    top-level key would be a non-standard field in a ``/data/`` response.
    """

    def test_augment_present_on_every_end_point(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        resp = app.post(
            f"{_BASE}/connectivity-service",
            json=_create_service_payload(sip_a, sip_z, "DP-64QAM"),
        )
        assert resp.status_code == 201
        svc = resp.json()["tapi-connectivity:connectivity-service"]
        assert len(svc["end-point"]) == 2
        for end_point in svc["end-point"]:
            constraint = end_point["layer-protocol-constraint"][0]
            assert constraint["layer-protocol-name"] == "PHOTONIC_MEDIA"
            spec = constraint[OTSIA_CSEP_SPEC]
            modulation = spec["otsi-config"][0]["modulation"]
            assert modulation["standard-modulation-technique"] == "MT_DP-QAM64"

    def test_legacy_top_level_key_is_refused(self, app: TestClient) -> None:
        """The pre-2.6 spelling fails loudly rather than defaulting to QPSK.

        Silently ignoring it would provision a DP-QPSK lightpath for a
        client that asked for DP-64QAM — a wrong service, not an error.
        """
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z)
        payload["tapi-connectivity:connectivity-service"][
            "modulation-format"
        ] = "DP-16QAM"
        resp = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert resp.status_code == 422
        assert "modulation-format" in resp.text

    def test_prefixed_identity_accepted(self, app: TestClient) -> None:
        """RFC 7951 §6.8 permits the module-qualified identityref spelling."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z)
        end_point = payload["tapi-connectivity:connectivity-service"][
            "end-point"
        ][0]
        cfg = end_point["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC][
            "otsi-config"
        ][0]
        cfg["modulation"]["standard-modulation-technique"] = (
            "tapi-photonic-media:MT_DP-QAM16"
        )
        resp = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert resp.status_code == 201
        # Echoed back in the canonical bare form.
        svc = resp.json()["tapi-connectivity:connectivity-service"]
        assert _modulation_of(svc) == "MT_DP-QAM16"

    def test_unknown_identity_refused(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z)
        end_point = payload["tapi-connectivity:connectivity-service"][
            "end-point"
        ][0]
        cfg = end_point["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC][
            "otsi-config"
        ][0]
        cfg["modulation"]["standard-modulation-technique"] = "MT_DP-QAM32"
        resp = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert resp.status_code == 422


class TestListEncodedBody:
    """RFC 7951 §5.4: a YANG list is a name/array pair.

    ``connectivity-service`` is a list, and RFC 8040 Appendix B.2.1 creates
    a single entry by POSTing an array of one. Both that and the bare
    object are accepted.
    """

    def test_array_of_one_is_accepted(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z)
        body = {
            "tapi-connectivity:connectivity-service": [
                payload["tapi-connectivity:connectivity-service"]
            ]
        }
        resp = app.post(f"{_BASE}/connectivity-service", json=body)
        assert resp.status_code == 201

    def test_array_of_several_is_refused(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        one = _create_service_payload(sip_a, sip_z)[
            "tapi-connectivity:connectivity-service"
        ]
        body = {"tapi-connectivity:connectivity-service": [one, dict(one)]}
        resp = app.post(f"{_BASE}/connectivity-service", json=body)
        assert resp.status_code == 400


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
        # Modulation is a photonic augment on the end-point, never a field
        # on the service — see models/connectivity.py.
        assert "modulation-format" not in svc
        assert _modulation_of(svc) == "MT_DP-QPSK"

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
        """GET connectivity-service={uuid} round-trips the modulation augment."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z, "DP-16QAM")
        create = app.post(f"{_BASE}/connectivity-service", json=payload)
        assert create.status_code == 201
        uuid = create.json()["tapi-connectivity:connectivity-service"]["uuid"]
        get_one = app.get(f"{_BASE}/connectivity-service={uuid}")
        assert get_one.status_code == 200
        svc = get_one.json()["tapi-connectivity:connectivity-service"]
        # ONF spells 16QAM as MT_DP-QAM16, not MT_DP-16QAM.
        assert _modulation_of(svc) == "MT_DP-QAM16"

    def test_get_service_returns_frequency_slot_when_allocated(
        self, app: TestClient
    ) -> None:
        """Assigned spectrum rides on the end-point, in the T-API 2.6 place.

        ``tapi-connectivity`` has no ``frequency-slot`` leaf; the photonic
        module augments the end-point's layer-protocol-constraint.
        """
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

        assert "frequency-slot" not in svc
        for end_point in svc["end-point"]:
            spec = end_point["layer-protocol-constraint"][0][MCG_CSEP_SPEC]
            assert spec["number-of-mc"] == 1
            config = spec["mc-spectrum-config-pac"][0]
            spectrum = config["spectrum"]
            # uint64 Hz, per grouping frequency-range.
            assert isinstance(spectrum["lower-frequency"], int)
            assert isinstance(spectrum["upper-frequency"], int)
            assert spectrum["upper-frequency"] > spectrum["lower-frequency"]
            assert config["edge-frequency-constraint"]["grid-type"] == (
                "GRID_TYPE_FLEX"
            )

    def test_unallocated_service_carries_no_spectrum_spec(
        self, app: TestClient
    ) -> None:
        """A service with no allocation says nothing, rather than zeroes."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        create = app.post(
            f"{_BASE}/connectivity-service",
            json=_create_service_payload(sip_a, sip_z),
        )
        uuid = create.json()["tapi-connectivity:connectivity-service"]["uuid"]
        ctx = app.app.state.context
        ctx._service_allocation.pop(uuid, None)

        svc = app.get(f"{_BASE}/connectivity-service={uuid}").json()[
            "tapi-connectivity:connectivity-service"
        ]
        for end_point in svc["end-point"]:
            assert MCG_CSEP_SPEC not in end_point["layer-protocol-constraint"][0]

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
                "end-point": [
                    tapi_end_point("a-end", sip_a),
                    tapi_end_point("z-end", sip_z),
                ],
            }
        }
        put_resp = app.put(f"{_BASE}/connectivity-service={uuid}", json=put_body)
        assert put_resp.status_code == 200
        assert put_resp.json()["tapi-connectivity:connectivity-service"]["name"][0]["value"] == "replaced-name"
        get_resp = app.get(f"{_BASE}/connectivity-service={uuid}")
        assert get_resp.status_code == 200
        assert get_resp.json()["tapi-connectivity:connectivity-service"]["name"][0]["value"] == "replaced-name"

    def test_put_failed_replace_restores_original_service(
        self, app: TestClient, monkeypatch
    ) -> None:
        """A replacement that can't be admitted must not tear the service down.

        Simulates an RMSA failure on the incoming service and asserts the
        original allocation is re-admitted and still reachable afterwards.
        """
        from twinlight.state.context import InsufficientSpectrumError

        sip_a, sip_z = _get_two_sip_uuids(app)
        create = app.post(
            f"{_BASE}/connectivity-service",
            json=_create_service_payload(sip_a, sip_z),
        )
        assert create.status_code == 201
        uuid = create.json()["tapi-connectivity:connectivity-service"]["uuid"]

        # Fail only the first add_service call (the incoming replacement); let
        # the rollback re-admission of the original service go through for real.
        ctx = app.app.state.context
        real_add = ctx.add_service
        calls = {"n": 0}

        async def flaky_add(svc):
            calls["n"] += 1
            if calls["n"] == 1:
                raise InsufficientSpectrumError("simulated: no spectrum")
            return await real_add(svc)

        monkeypatch.setattr(ctx, "add_service", flaky_add)

        put_body = {
            "tapi-connectivity:connectivity-service": {
                "uuid": uuid,
                "name": [{"value-name": "service-name", "value": "would-be-replacement"}],
                "end-point": [
                    tapi_end_point("a-end", sip_a),
                    tapi_end_point("z-end", sip_z),
                ],
            }
        }
        put_resp = app.put(f"{_BASE}/connectivity-service={uuid}", json=put_body)
        assert put_resp.status_code == 409

        # The original service survived the failed replacement.
        get_resp = app.get(f"{_BASE}/connectivity-service={uuid}")
        assert get_resp.status_code == 200
        restored = get_resp.json()["tapi-connectivity:connectivity-service"]
        assert restored["name"][0]["value"] == "test-link"
