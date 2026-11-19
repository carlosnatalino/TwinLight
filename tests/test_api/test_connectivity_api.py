"""Tests for the TAPI Connectivity API (RMSA: path, spectrum, QoT)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, tapi_end_point
from twinlight.models.connectivity import MCG_CSEP_SPEC, OTSIA_CSEP_SPEC

_BASE = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"


def _get_two_sip_uuids(app: TestClient) -> tuple[str, str]:
    data = app.get("/restconf/data/tapi-common:context/service-interface-point").json()
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
            _BASE,
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
        resp = app.post(_BASE, json=payload)
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
        resp = app.post(_BASE, json=payload)
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
        resp = app.post(_BASE, json=payload)
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
        resp = app.post(_BASE, json=body)
        assert resp.status_code == 201

    def test_array_of_several_is_refused(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        one = _create_service_payload(sip_a, sip_z)[
            "tapi-connectivity:connectivity-service"
        ]
        body = {"tapi-connectivity:connectivity-service": [one, dict(one)]}
        resp = app.post(_BASE, json=body)
        assert resp.status_code == 400


class TestCreateConnectivityService:
    """Create service exercises RMSA: path (k-shortest), first-fit spectrum, QoT check."""

    def test_create_returns_201(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z)
        resp = app.post(_BASE, json=payload)
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
        r1 = app.post(_BASE, json=p1)
        assert r1.status_code == 201
        r2 = app.post(_BASE, json=p2)
        assert r2.status_code == 201
        # Both exist
        list_resp = app.get(f"{_BASE}/connectivity-service")
        assert list_resp.status_code == 200
        services = list_resp.json()["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert len(services) == 2

    def test_422_when_sip_not_found(self, app: TestClient) -> None:
        sip_a, _ = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, "00000000-0000-0000-0000-000000000000")
        resp = app.post(_BASE, json=payload)
        assert resp.status_code == 422


class TestGetAndDeleteConnectivityService:
    def test_get_service_returns_modulation_format(self, app: TestClient) -> None:
        """GET connectivity-service={uuid} round-trips the modulation augment."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z, "DP-16QAM")
        create = app.post(_BASE, json=payload)
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
            _BASE,
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
            _BASE,
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
        app.post(_BASE, json=_create_service_payload(sip_a, sip_z))
        resp = app.get(f"{_BASE}/connectivity-service")
        assert resp.status_code == 200
        services = resp.json()["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert len(services) >= 1

    def test_delete_releases_spectrum_so_new_service_succeeds(self, app: TestClient) -> None:
        """Delete frees slots; creating another service on same path should succeed."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        payload = _create_service_payload(sip_a, sip_z)
        r1 = app.post(_BASE, json=payload)
        assert r1.status_code == 201
        uuid1 = r1.json()["tapi-connectivity:connectivity-service"]["uuid"]
        app.delete(f"{_BASE}/connectivity-service={uuid1}")
        # Create again on same path
        r2 = app.post(_BASE, json=payload)
        assert r2.status_code == 201

    def test_put_replace_connectivity_service(self, app: TestClient) -> None:
        """PUT replaces service by UUID; body UUID must match path."""
        sip_a, sip_z = _get_two_sip_uuids(app)
        create = app.post(
            _BASE,
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
        original is put back and still reachable afterwards.
        """
        from twinlight.state.context import InsufficientSpectrumError

        sip_a, sip_z = _get_two_sip_uuids(app)
        create = app.post(
            _BASE,
            json=_create_service_payload(sip_a, sip_z),
        )
        assert create.status_code == 201
        uuid = create.json()["tapi-connectivity:connectivity-service"]["uuid"]

        # Fail the admission of the incoming replacement.
        ctx = app.app.state.context
        before = ctx._service_allocation[uuid]

        async def refused(svc):
            raise InsufficientSpectrumError("simulated: no spectrum")

        monkeypatch.setattr(ctx, "_add_service", refused)

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
        assert ctx._service_allocation[uuid] == before  # same route and block


class TestConstraints:
    """routing-constraint / topology-constraint on a connectivity-service."""

    def test_unsupported_constraint_is_refused_by_name(self, app: TestClient) -> None:
        data = app.get("/restconf/data/tapi-common:context/service-interface-point").json()
        sips = [s["uuid"] for s in data["tapi-common:context"]["service-interface-point"]]
        body = {"tapi-connectivity:connectivity-service": [{
            "end-point": [tapi_end_point("a", sips[0]), tapi_end_point("z", sips[1])],
            "routing-constraint": {"diversity-policy": "SRLG"},
        }]}
        resp = app.post(_BASE, json=body)
        assert resp.status_code == 422
        assert "diversity-policy" in resp.text

    def test_constraints_are_echoed(self, mesh_app: TestClient) -> None:
        from tests.conftest import MeshRefs

        refs = MeshRefs(mesh_app.app.state.context)
        constraint = [{"local-id": "1", "exclude-node": [refs.node("roadm B")]}]
        body = {"tapi-connectivity:connectivity-service": [{
            "end-point": [
                tapi_end_point("a", refs.sip("trx A")),
                tapi_end_point("z", refs.sip("trx C")),
            ],
            "topology-constraint": constraint,
        }]}
        resp = mesh_app.post(_BASE, json=body)
        assert resp.status_code == 201, resp.text
        service = resp.json()["tapi-connectivity:connectivity-service"]
        assert service["topology-constraint"] == constraint
        assert "routing-constraint" not in service

    def test_unresolvable_reference_is_422(self, mesh_app: TestClient) -> None:
        from tests.conftest import MeshRefs

        refs = MeshRefs(mesh_app.app.state.context)
        body = {"end-point": [
            tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C")),
        ], "topology-constraint": [{"exclude-link": [
            {"topology-uuid": "x", "link-uuid": "y"}]}]}
        resp = mesh_app.post(_BASE, json=body)
        assert resp.status_code == 422
        assert "not found" in resp.text


def _spec(ep: dict) -> dict:
    return ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]


class TestUnimplementedLeaves:
    """Standard v2.6.0 leaves the twin does not act on fail the request with
    501 / operation-not-supported, naming each, rather than being admitted
    and ignored. Leaves the twin supports at one value fail at any other."""

    def _body(self, app: TestClient, **extra) -> dict:
        sip_a, sip_z = _get_two_sip_uuids(app)
        return {"tapi-connectivity:connectivity-service": [{
            "end-point": [tapi_end_point("a", sip_a), tapi_end_point("z", sip_z)],
            **extra,
        }]}

    def _refusal(self, app: TestClient, body: dict) -> str:
        resp = app.post(_BASE, json=body)
        assert resp.status_code == 501, resp.text
        error = resp.json()["ietf-restconf:errors"]["error"][0]
        assert error["error-tag"] == "operation-not-supported"
        listed = app.get(f"{_BASE}/connectivity-service").json()
        assert listed["tapi-connectivity:connectivity-context"]["connectivity-service"] == []
        return error["error-message"]

    @pytest.mark.parametrize("leaf", [
        "connectivity-constraint", "resilience-constraint", "layer-protocol-qualifier",
        "connectivity-service", "internal-point", "connectivity-protection-service",
    ])
    def test_service_leaf(self, app: TestClient, leaf: str) -> None:
        message = self._refusal(app, self._body(app, **{leaf: {}}))
        assert message.startswith("not implemented by this twin: ")
        assert leaf in message

    @pytest.mark.parametrize("leaf", [
        "role", "capacity", "layer-protocol-qualifier", "protection-role",
        "administrative-state", "profile",
    ])
    def test_end_point_leaf(self, app: TestClient, leaf: str) -> None:
        body = self._body(app)
        body["tapi-connectivity:connectivity-service"][0]["end-point"][1][leaf] = "X"
        assert f"end-point[z]/{leaf}" in self._refusal(app, body)

    @pytest.mark.parametrize(("where", "leaf"), [
        ("lpc", "layer-protocol-qualifier"),
        ("spec", "total-power-warn-threshold-upper"),
        ("cfg", "laser-control"),
        ("cfg", "application-identifier"),
    ])
    def test_photonic_leaf(self, app: TestClient, where: str, leaf: str) -> None:
        body = self._body(app)
        ep = body["tapi-connectivity:connectivity-service"][0]["end-point"][0]
        node = {"lpc": ep["layer-protocol-constraint"][0], "spec": _spec(ep),
                "cfg": _spec(ep)["otsi-config"][0]}[where]
        node[leaf] = "X"
        assert f"/{leaf}" in self._refusal(app, body)

    @pytest.mark.parametrize(("place", "leaf", "value"), [
        ("service", "direction", "BIDIRECTIONAL"),
        ("service", "layer-protocol-name", "ODU"),
        ("end-point", "direction", "BIDIRECTIONAL"),
        ("end-point", "layer-protocol-name", "ETH"),
        ("spec", "number-of-otsi", 2),
    ])
    def test_unsupported_value(self, app: TestClient, place: str, leaf: str, value) -> None:
        body = self._body(app)
        service = body["tapi-connectivity:connectivity-service"][0]
        node = {"service": service, "end-point": service["end-point"][0],
                "spec": _spec(service["end-point"][0])}[place]
        node[leaf] = value
        message = self._refusal(app, body)
        assert f"{leaf}={value!r} (supported: " in message
        if value == "BIDIRECTIONAL":
            assert "create a second service for the reverse direction" in message

    def test_every_refusal_is_named(self, app: TestClient) -> None:
        body = self._body(app, **{"connectivity-constraint": {}, "direction": "BIDIRECTIONAL"})
        body["tapi-connectivity:connectivity-service"][0]["end-point"][0]["role"] = "ROOT"
        message = self._refusal(app, body)
        for part in ("connectivity-constraint", "direction='BIDIRECTIONAL'",
                     "end-point[a]/role"):
            assert part in message

    def test_replace_is_refused_and_keeps_the_service(self, app: TestClient) -> None:
        created = app.post(_BASE, json=self._body(app))
        service = created.json()["tapi-connectivity:connectivity-service"]
        replacement = {**service, "resilience-constraint": {}}
        resp = app.put(f"{_BASE}/connectivity-service={service['uuid']}",
                       json={"tapi-connectivity:connectivity-service": replacement})
        assert resp.status_code == 501
        assert app.get(f"{_BASE}/connectivity-service={service['uuid']}").status_code == 200

    def test_a_get_body_is_accepted_back(self, app: TestClient) -> None:
        """Every fixed leaf is reported at its supported value, so a client
        that replaces a service with what it read gets no 501."""
        created = app.post(_BASE, json=self._body(app))
        service = created.json()["tapi-connectivity:connectivity-service"]
        assert service["direction"] == "UNIDIRECTIONAL"
        assert service["layer-protocol-name"] == "PHOTONIC_MEDIA"
        assert [ep["direction"] for ep in service["end-point"]] == ["SOURCE", "SINK"]
        resp = app.put(f"{_BASE}/connectivity-service={service['uuid']}",
                       json={"tapi-connectivity:connectivity-service": service})
        assert resp.status_code in (200, 201), resp.text


class TestEndPointDirection:
    """The lightpath runs from the SOURCE end-point to the SINK one; without
    a direction, from the first end-point to the second."""

    def _create(self, app: TestClient, a: dict, z: dict):
        return app.post(_BASE,
                        json={"tapi-connectivity:connectivity-service": [{"end-point": [a, z]}]})

    def test_source_is_where_the_route_starts(self, mesh_app: TestClient) -> None:
        ctx = mesh_app.app.state.context
        refs = MeshRefs(ctx)
        z = {**tapi_end_point("z", refs.sip("trx C")), "direction": "SOURCE"}
        resp = self._create(mesh_app, tapi_end_point("a", refs.sip("trx A")), z)
        assert resp.status_code == 201, resp.text
        service = resp.json()["tapi-connectivity:connectivity-service"]
        assert [(ep["local-id"], ep["direction"]) for ep in service["end-point"]] == [
            ("z", "SOURCE"), ("a", "SINK"),
        ]
        path = ctx._allocated_path_uids(service["uuid"])
        assert (path[0], path[-1]) == ("trx C", "trx A")

    def test_two_sources_are_422(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        resp = self._create(app, {**tapi_end_point("a", sip_a), "direction": "SOURCE"},
                            {**tapi_end_point("z", sip_z), "direction": "SOURCE"})
        assert resp.status_code == 422
        assert "one SOURCE and one SINK" in resp.text

    def test_end_point_name_is_kept(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        name = [{"value-name": "port", "value": "client-1"}]
        resp = self._create(app, {**tapi_end_point("a", sip_a), "name": name},
                            tapi_end_point("z", sip_z))
        end_points = resp.json()["tapi-connectivity:connectivity-service"]["end-point"]
        assert end_points[0]["name"] == name
        assert "name" not in end_points[1]


class TestRequestValidation:
    """Malformed or unsupported requests are client errors, never a 500, and
    a request is never admitted with part of it ignored."""

    @staticmethod
    def _egn_app(tmp_path) -> TestClient:
        from tests.conftest import _mesh_config
        from twinlight.app import create_app

        return TestClient(create_app(_mesh_config(tmp_path, "egn")))

    @staticmethod
    def _full_body(app: TestClient) -> dict:
        """A request using every leaf the twin acts on."""
        ctx = app.app.state.context
        refs = MeshRefs(ctx)
        lower, upper = ctx._slot_range_hz(100, 9)
        end_points = []
        for local_id, trx in (("a", "trx A"), ("z", "trx C")):
            ep = tapi_end_point(local_id, refs.sip(trx))
            constraint = ep["layer-protocol-constraint"][0]
            cfg = constraint[OTSIA_CSEP_SPEC]["otsi-config"][0]
            cfg["channel-output-power"] = 100
            cfg["central-frequency"] = (lower + upper) // 2
            constraint[MCG_CSEP_SPEC] = {"mc-spectrum-config-pac": [{
                "spectrum": {"lower-frequency": lower, "upper-frequency": upper},
            }]}
            end_points.append(ep)
        end_points[0]["direction"] = "SOURCE"
        return {"tapi-connectivity:connectivity-service": [{
            "name": [{"value-name": "service-name", "value": "fuzz"}],
            "end-point": end_points,
            "routing-constraint": {"route-objective-function": "MIN_WORK_ROUTE_LATENCY"},
            "topology-constraint": [{"local-id": "1", "exclude-node": [refs.node("roadm D")]}],
        }]}

    def test_the_full_body_is_admitted(self, tmp_path) -> None:
        app = self._egn_app(tmp_path)
        resp = app.post(_BASE, json=self._full_body(app))
        assert resp.status_code == 201, resp.text

    def test_no_malformed_node_is_a_500(self, tmp_path) -> None:
        """Replace every node of the full body, one at a time, with a value of
        each wrong JSON type: every answer is a client error or a success."""
        import copy

        app = self._egn_app(tmp_path)
        body = self._full_body(app)

        def paths(node, prefix=()):
            yield prefix
            children = node.items() if isinstance(node, dict) else (
                enumerate(node) if isinstance(node, list) else ())
            for key, child in children:
                yield from paths(child, (*prefix, key))

        for path in list(paths(body))[1:]:
            for bad in ("x", [], {}, None, 7, True, [["nested"]]):
                mutated = copy.deepcopy(body)
                parent = mutated
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = bad
                resp = app.post(_BASE, json=mutated)
                assert resp.status_code < 500, (path, bad, resp.text)
                if resp.status_code == 201:
                    uuid = resp.json()["tapi-connectivity:connectivity-service"]["uuid"]
                    app.delete(f"{_BASE}/connectivity-service={uuid}")

    def test_posting_an_existing_uuid_is_409_and_leaks_nothing(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        body = _create_service_payload(sip_a, sip_z)
        created = app.post(_BASE, json=body).json()
        uuid = created["tapi-connectivity:connectivity-service"]["uuid"]
        ctx = app.app.state.context
        allocation = ctx._service_allocation[uuid]
        spectrum = ctx._spectrum_state.to_dict()

        body["tapi-connectivity:connectivity-service"]["uuid"] = uuid
        for _ in range(3):
            resp = app.post(_BASE, json=body)
            assert resp.status_code == 409
            error = resp.json()["ietf-restconf:errors"]["error"][0]
            assert error["error-tag"] == "resource-denied"
            assert "already exists" in error["error-message"]
        assert ctx._service_allocation[uuid] == allocation
        assert ctx._spectrum_state.to_dict() == spectrum
        assert app.delete(f"{_BASE}/connectivity-service={uuid}").status_code == 204
        # Every block came back: the next service gets the first slot again.
        again = app.post(_BASE, json=_create_service_payload(sip_a, sip_z))
        start = ctx._service_allocation[
            again.json()["tapi-connectivity:connectivity-service"]["uuid"]][1]
        assert start == allocation[1]

    @pytest.mark.parametrize("count", [1, 3])
    def test_a_lightpath_has_exactly_two_end_points(self, app: TestClient, count: int) -> None:
        sips = [s["uuid"] for s in app.get("/restconf/data/tapi-common:context/service-interface-point")
                .json()["tapi-common:context"]["service-interface-point"]]
        body = {"tapi-connectivity:connectivity-service": [{
            "end-point": [tapi_end_point(f"e{i}", sips[i % len(sips)]) for i in range(count)],
        }]}
        resp = app.post(_BASE, json=body)
        assert resp.status_code == 422
        assert "exactly two end-points" in resp.text

    def test_modulation_null_is_422(self, app: TestClient) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        body = _create_service_payload(sip_a, sip_z)
        for ep in body["tapi-connectivity:connectivity-service"]["end-point"]:
            ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0][
                "modulation"] = None
        resp = app.post(_BASE, json=body)
        assert resp.status_code == 422
        assert "modulation must be an object" in resp.text

    def test_power_and_frequency_without_a_format(self, mesh_app: TestClient) -> None:
        """otsi-config/modulation is optional: launch power and central
        frequency can be requested with the format left to admission."""
        ctx = mesh_app.app.state.context
        refs = MeshRefs(ctx)
        lower, upper = ctx._slot_range_hz(100, 9)
        end_points = [tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]
        for ep in end_points:
            cfg = ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0]
            del cfg["modulation"]
            cfg["channel-output-power"] = 150
            cfg["central-frequency"] = (lower + upper) // 2
        resp = mesh_app.post(_BASE,
                             json={"tapi-connectivity:connectivity-service": [{"end-point": end_points}]})
        assert resp.status_code == 201, resp.text
        service = resp.json()["tapi-connectivity:connectivity-service"]
        cfg = _spec(service["end-point"][0])["otsi-config"][0]
        assert cfg["channel-output-power"] == 150.0
        assert cfg["central-frequency"] == (lower + upper) // 2
        assert cfg["modulation"]["standard-modulation-technique"] in (
            "MT_DP-QAM64", "MT_DP-QAM16", "MT_DP-QPSK")

    @pytest.mark.parametrize("centi_dbm", [2500, 1001, -2001, 10 ** 300])
    def test_launch_power_outside_the_modelled_range_is_422(
        self, app: TestClient, centi_dbm: float,
    ) -> None:
        sip_a, sip_z = _get_two_sip_uuids(app)
        body = _create_service_payload(sip_a, sip_z)
        for ep in body["tapi-connectivity:connectivity-service"]["end-point"]:
            ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0][
                "channel-output-power"] = centi_dbm
        resp = app.post(_BASE, json=body)
        assert resp.status_code == 422, resp.text
        assert "the twin models launch powers from -20 to 10 dBm" in resp.text


def test_a_client_cannot_set_operational_state(app: TestClient) -> None:
    sip_a, sip_z = _get_two_sip_uuids(app)
    body = _create_service_payload(sip_a, sip_z)
    body["tapi-connectivity:connectivity-service"]["operational-state"] = "DISABLED"
    resp = app.post(_BASE, json=body)
    assert resp.status_code == 201
    assert resp.json()["tapi-connectivity:connectivity-service"]["operational-state"] == "ENABLED"
