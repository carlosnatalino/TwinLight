"""Tests for the TAPI Common API endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.models.connectivity import MCG_CSEP_SPEC

_SVC = "/data/tapi-connectivity:connectivity-context/connectivity-service"
_PATH_COMP_SVC = "/data/tapi-path-computation:path-computation-context/path-comp-service"


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


# The full-context GET carries every augment of /tapi-common:context the twin
# serves. Each section must equal what that module's own GET serves: one
# row per section, keyed by the module-qualified name (RFC 7951 §4), with the
# URL whose response carries the same container under the same key.
_SECTIONS = {
    "tapi-topology:topology-context":
        "/data/tapi-common:context/tapi-topology:topology-context",
    "tapi-connectivity:connectivity-context":
        "/data/tapi-connectivity:connectivity-context/connectivity-service",
    "tapi-path-computation:path-computation-context":
        "/data/tapi-path-computation:path-computation-context",
    "tapi-equipment:physical-context":
        "/data/tapi-equipment:physical-context",
    "tapi-oam:oam-context":
        "/data/tapi-common:context/tapi-oam:oam-context",
    "tapi-streaming:stream-context":
        "/data/tapi-common:context/tapi-streaming:stream-context",
}


def _full_context(app: TestClient, root: str = "") -> dict:
    resp = app.get(f"{root}/data/tapi-common:context")
    assert resp.status_code == 200, resp.text
    return resp.json()["tapi-common:context"]


def _per_module(app: TestClient, section: str, root: str = "") -> dict:
    resp = app.get(f"{root}{_SECTIONS[section]}")
    assert resp.status_code == 200, resp.text
    return resp.json()[section]


@pytest.fixture
def admitted(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    """The mesh twin with two lightpaths and a path-comp-service."""
    client = TestClient(create_app(_mesh_config(tmp_path, request.param)))
    refs = MeshRefs(client.app.state.context)
    for a in ("trx A", "trx B"):
        body = {"end-point": [tapi_end_point("a", refs.sip(a)),
                              tapi_end_point("z", refs.sip("trx C"))]}
        resp = client.post(_SVC, json={"tapi-connectivity:connectivity-service": [body]})
        assert resp.status_code == 201, resp.text
    resp = client.post(_PATH_COMP_SVC, json={
        "tapi-path-computation:path-comp-service": [{"end-point": [
            {"local-id": "a",
             "service-interface-point": {"service-interface-point-uuid": refs.sip("trx A")}},
            {"local-id": "z",
             "service-interface-point": {"service-interface-point-uuid": refs.sip("trx B")}},
        ]}],
    })
    assert resp.status_code == 201, resp.text
    return client


class TestFullContextAugments:
    def test_carries_every_augment(self, app: TestClient) -> None:
        assert set(_SECTIONS) <= set(_full_context(app))

    @pytest.mark.parametrize("section", list(_SECTIONS))
    def test_section_equals_its_module_get_with_nothing_admitted(
        self, app: TestClient, section: str,
    ) -> None:
        assert _full_context(app)[section] == _per_module(app, section)

    @pytest.mark.parametrize("admitted", ["gnpy", "egn"], indirect=True)
    @pytest.mark.parametrize("section", list(_SECTIONS))
    def test_section_equals_its_module_get_with_services_admitted(
        self, admitted: TestClient, section: str,
    ) -> None:
        full = _full_context(admitted)
        assert len(full["tapi-connectivity:connectivity-context"]["connectivity-service"]) == 2
        assert full["tapi-path-computation:path-computation-context"]["path"]
        assert full[section] == _per_module(admitted, section)

    @pytest.mark.parametrize("admitted", ["gnpy"], indirect=True)
    def test_restconf_root_serves_the_same_context(self, admitted: TestClient) -> None:
        full = _full_context(admitted, root="/restconf")
        assert full == _full_context(admitted)
        for section in _SECTIONS:
            assert full[section] == _per_module(admitted, section, root="/restconf")

    @pytest.mark.parametrize("admitted", ["gnpy", "egn"], indirect=True)
    def test_oam_jobs_reference_services_in_the_same_response(
        self, admitted: TestClient,
    ) -> None:
        # One read is a consistent sync: every service an oam-job names is
        # in the same document, with the end-point the job measures at.
        full = _full_context(admitted)
        services = {
            s["uuid"]: {ep["local-id"] for ep in s["end-point"]}
            for s in full["tapi-connectivity:connectivity-context"]["connectivity-service"]
        }
        jobs = full["tapi-oam:oam-context"]["oam-job"]
        assert len(jobs) == len(services) == 2
        for job in jobs:
            ref = job["connectivity-service-end-point"]
            assert ref["connectivity-service-uuid"] in services
            assert (
                ref["connectivity-service-end-point-local-id"]
                in services[ref["connectivity-service-uuid"]]
            )

    @pytest.mark.parametrize("admitted", ["gnpy"], indirect=True)
    def test_services_carry_their_live_spectrum(self, admitted: TestClient) -> None:
        # The connectivity section is assembled per request, like the module
        # GET: each end-point carries the block its service holds now.
        services = _full_context(admitted)["tapi-connectivity:connectivity-context"][
            "connectivity-service"
        ]
        for service in services:
            for end_point in service["end-point"]:
                for constraint in end_point["layer-protocol-constraint"]:
                    assert MCG_CSEP_SPEC in constraint
