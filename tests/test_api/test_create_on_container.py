"""T-API list entries are created by POST on their parent (RFC 8040 §4.4.1).

"The target resource for the POST method for resource creation is the parent
of the new resource": a ``connectivity-service`` is created on the
``connectivity-context`` and a ``path-comp-service`` on the
``path-computation-context``, each with the new entry in the body. The 201
carries a ``Location`` naming the created entry. The list paths take no POST:
there is one way to create, not two.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.config import TwinConfig

_CONNECTIVITY = "/data/tapi-common:context/tapi-connectivity:connectivity-context"
_PATH_COMPUTATION = (
    "/data/tapi-common:context/tapi-path-computation:path-computation-context"
)
_SVC_KEY = "tapi-connectivity:connectivity-service"
_PCS_KEY = "tapi-path-computation:path-comp-service"


def _service(refs: MeshRefs, a: str, z: str, **extra: object) -> dict:
    return {_SVC_KEY: [{
        "end-point": [tapi_end_point("a", refs.sip(a)), tapi_end_point("z", refs.sip(z))],
        **extra,
    }]}


def _path_comp_service(refs: MeshRefs, a: str, z: str, **extra: object) -> dict:
    return {_PCS_KEY: [{
        "end-point": [
            {"local-id": "a",
             "service-interface-point": {"service-interface-point-uuid": refs.sip(a)}},
            {"local-id": "z",
             "service-interface-point": {"service-interface-point-uuid": refs.sip(z)}},
        ],
        **extra,
    }]}


def _mesh(tmp_path: Path, backend: str, root: str = "/restconf") -> TestClient:
    config: TwinConfig = _mesh_config(tmp_path, backend)
    config.server.restconf_root = root
    return TestClient(create_app(config))


@pytest.fixture(params=["gnpy", "egn"])
def mesh(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    """The mesh twin under the default root, once per physics backend."""
    return _mesh(tmp_path, request.param)


class TestConnectivityService:
    def test_created_on_the_container(self, mesh: TestClient) -> None:
        refs = MeshRefs(mesh.app.state.context)
        resp = mesh.post(f"/restconf{_CONNECTIVITY}", json=_service(refs, "trx A", "trx C"))
        assert resp.status_code == 201, resp.text
        created = resp.json()[_SVC_KEY]
        assert [s.uuid for s in mesh.app.state.context.get_services()] == [created["uuid"]]

        location = resp.headers["location"]
        assert location == (
            f"/restconf{_CONNECTIVITY}/connectivity-service={created['uuid']}"
        )
        fetched = mesh.get(location)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json() == {_SVC_KEY: created}

    def test_the_list_path_takes_no_post(self, mesh: TestClient) -> None:
        refs = MeshRefs(mesh.app.state.context)
        resp = mesh.post(
            f"/restconf{_CONNECTIVITY}/connectivity-service",
            json=_service(refs, "trx A", "trx C"),
        )
        assert resp.status_code == 405
        assert mesh.app.state.context.get_services() == []
        # GET is still served there.
        assert mesh.get(f"/restconf{_CONNECTIVITY}/connectivity-service").status_code == 200

    def test_a_refusal_has_no_location(self, mesh: TestClient) -> None:
        refs = MeshRefs(mesh.app.state.context)
        body = _service(refs, "trx A", "trx C", uuid="fixed")
        assert mesh.post(f"/restconf{_CONNECTIVITY}", json=body).status_code == 201
        again = mesh.post(f"/restconf{_CONNECTIVITY}", json=body)
        assert again.status_code == 409
        assert "location" not in again.headers

    def test_the_key_is_percent_encoded(self, mesh: TestClient) -> None:
        # RFC 8040 §3.5.3: a key value is percent-encoded in the path.
        refs = MeshRefs(mesh.app.state.context)
        body = _service(refs, "trx A", "trx C", uuid="svc 1:a")
        resp = mesh.post(f"/restconf{_CONNECTIVITY}", json=body)
        assert resp.status_code == 201, resp.text
        location = resp.headers["location"]
        assert location.endswith("/connectivity-service=svc%201%3Aa")
        assert mesh.get(location).json()[_SVC_KEY]["uuid"] == "svc 1:a"


class TestPathCompService:
    def test_created_on_the_container(self, mesh: TestClient) -> None:
        refs = MeshRefs(mesh.app.state.context)
        resp = mesh.post(
            f"/restconf{_PATH_COMPUTATION}", json=_path_comp_service(refs, "trx A", "trx C"),
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()[_PCS_KEY]
        assert created["path"]

        location = resp.headers["location"]
        assert location == (
            f"/restconf{_PATH_COMPUTATION}/path-comp-service={created['uuid']}"
        )
        fetched = mesh.get(location)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json() == {_PCS_KEY: created}

    def test_the_list_path_takes_no_post(self, mesh: TestClient) -> None:
        refs = MeshRefs(mesh.app.state.context)
        resp = mesh.post(
            f"/restconf{_PATH_COMPUTATION}/path-comp-service",
            json=_path_comp_service(refs, "trx A", "trx C"),
        )
        assert resp.status_code == 405
        assert mesh.app.state.context.get_path_comp_services() == []
        assert mesh.get(f"/restconf{_PATH_COMPUTATION}/path-comp-service").status_code == 200

    def test_a_refusal_has_no_location(self, mesh: TestClient) -> None:
        refs = MeshRefs(mesh.app.state.context)
        body = _path_comp_service(refs, "trx A", "trx C", uuid="fixed")
        assert mesh.post(f"/restconf{_PATH_COMPUTATION}", json=body).status_code == 201
        again = mesh.post(f"/restconf{_PATH_COMPUTATION}", json=body)
        assert again.status_code == 409
        assert "location" not in again.headers


@pytest.mark.parametrize("root", ["/api", ""])
class TestUnderAnotherRoot:
    """The Location follows ``server.restconf_root``, including an empty one."""

    def test_connectivity_service(self, tmp_path: Path, root: str) -> None:
        client = _mesh(tmp_path, "gnpy", root)
        refs = MeshRefs(client.app.state.context)
        resp = client.post(f"{root}{_CONNECTIVITY}", json=_service(refs, "trx A", "trx C"))
        assert resp.status_code == 201, resp.text
        created = resp.json()[_SVC_KEY]
        location = resp.headers["location"]
        assert location == f"{root}{_CONNECTIVITY}/connectivity-service={created['uuid']}"
        assert client.get(location).json() == {_SVC_KEY: created}
        assert client.post(
            f"{root}{_CONNECTIVITY}/connectivity-service", json=_service(refs, "trx B", "trx C"),
        ).status_code == 405

    def test_path_comp_service(self, tmp_path: Path, root: str) -> None:
        client = _mesh(tmp_path, "gnpy", root)
        refs = MeshRefs(client.app.state.context)
        resp = client.post(
            f"{root}{_PATH_COMPUTATION}", json=_path_comp_service(refs, "trx A", "trx C"),
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()[_PCS_KEY]
        location = resp.headers["location"]
        assert location == f"{root}{_PATH_COMPUTATION}/path-comp-service={created['uuid']}"
        assert client.get(location).json() == {_PCS_KEY: created}
        assert client.post(
            f"{root}{_PATH_COMPUTATION}/path-comp-service",
            json=_path_comp_service(refs, "trx B", "trx C"),
        ).status_code == 405
