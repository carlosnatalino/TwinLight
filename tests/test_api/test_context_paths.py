"""The connectivity, path-computation and physical contexts at their YANG paths.

``tapi-connectivity``, ``tapi-path-computation`` and ``tapi-equipment`` augment
``/tapi-common:context``, so RFC 8040 §3.5.3 puts their data resources under
``…/data/tapi-common:context/<module>:<container>/…``. Earlier releases served
them as siblings of the context (``…/data/<module>:<container>/…``); those
short forms stay as aliases, hidden from OpenAPI. These tests pin that the
two forms are one resource: same routes, same bodies, same state, at both the
RESTCONF root and the bare ``/data/`` mount.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app

ROOTS = ("", "/restconf")

#: module → (canonical, short alias), relative to the mount.
CONTAINERS = {
    "connectivity": (
        "/data/tapi-common:context/tapi-connectivity:connectivity-context",
        "/data/tapi-connectivity:connectivity-context",
    ),
    "path-computation": (
        "/data/tapi-common:context/tapi-path-computation:path-computation-context",
        "/data/tapi-path-computation:path-computation-context",
    ),
    "equipment": (
        "/data/tapi-common:context/tapi-equipment:physical-context",
        "/data/tapi-equipment:physical-context",
    ),
}
LONG = {module: forms[0] for module, forms in CONTAINERS.items()}
SHORT = {module: forms[1] for module, forms in CONTAINERS.items()}

#: Every route of the three modules, relative to its container, with the
#: methods it serves. The route-table test below checks this list against
#: what the app actually registers, so a route added later cannot be missed.
ROUTES: dict[str, dict[str, set[str]]] = {
    "connectivity": {
        "": {"GET"},
        "/connectivity-service": {"GET", "POST"},
        "/connectivity-service={uuid}": {"GET", "PUT", "PATCH", "DELETE"},
    },
    "path-computation": {
        "": {"GET"},
        "/path-comp-service": {"GET", "POST"},
        "/path-comp-service={uuid}": {"GET", "DELETE"},
        "/path": {"GET"},
        "/path={uuid}": {"GET"},
    },
    "equipment": {
        "": {"GET"},
        "/device": {"GET"},
        "/device={uuid}": {"GET"},
        "/device={device_uuid}/equipment={uuid}": {"GET"},
        "/physical-span": {"GET"},
        "/physical-span={uuid}": {"GET"},
    },
}

_SVC_KEY = "tapi-connectivity:connectivity-service"
_PCS_KEY = "tapi-path-computation:path-comp-service"


def _service_body(refs: MeshRefs, a: str, z: str, uuid: str | None = None) -> dict:
    body: dict = {"end-point": [tapi_end_point("a", refs.sip(a)),
                                tapi_end_point("z", refs.sip(z))]}
    if uuid is not None:
        body["uuid"] = uuid
    return {_SVC_KEY: [body]}


def _pcs_body(refs: MeshRefs, a: str, z: str) -> dict:
    return {_PCS_KEY: [{"end-point": [
        {"local-id": "a",
         "service-interface-point": {"service-interface-point-uuid": refs.sip(a)}},
        {"local-id": "z",
         "service-interface-point": {"service-interface-point-uuid": refs.sip(z)}},
    ]}]}


@pytest.fixture(params=["gnpy", "egn"])
def mesh(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    """The mesh twin, nothing admitted, once per physics backend."""
    return TestClient(create_app(_mesh_config(tmp_path, request.param)))


@pytest.fixture
def admitted(mesh: TestClient) -> TestClient:
    """The mesh twin with two lightpaths and a path-comp-service.

    Admitted through the short forms, which is how existing clients do it;
    the mutation tests below cover the long forms.
    """
    refs = MeshRefs(mesh.app.state.context)
    for a in ("trx A", "trx B"):
        resp = mesh.post(f"{SHORT['connectivity']}/connectivity-service",
                         json=_service_body(refs, a, "trx C"))
        assert resp.status_code == 201, resp.text
    resp = mesh.post(f"{SHORT['path-computation']}/path-comp-service",
                     json=_pcs_body(refs, "trx A", "trx B"))
    assert resp.status_code == 201, resp.text
    return mesh


def _get_urls(app: TestClient, *, admitted: bool) -> list[tuple[str, str]]:
    """(module, container-relative URL) for every GET route, keys filled in."""
    ctx = app.app.state.context
    physical = ctx.physical_context()
    device = next(d for d in physical.device if d.equipment)
    urls = [
        ("connectivity", ""),
        ("connectivity", "/connectivity-service"),
        ("path-computation", ""),
        ("path-computation", "/path-comp-service"),
        ("path-computation", "/path"),
        ("equipment", ""),
        ("equipment", "/device"),
        ("equipment", f"/device={device.uuid}"),
        ("equipment", f"/device={device.uuid}/equipment={device.equipment[0].uuid}"),
        ("equipment", "/physical-span"),
        ("equipment", f"/physical-span={physical.physical_span[0].uuid}"),
    ]
    if admitted:
        service = ctx.get_services()[0]
        pcs = ctx.get_path_comp_services()[0]
        path = ctx.get_paths()[0]
        urls += [
            ("connectivity", f"/connectivity-service={service.uuid}"),
            ("path-computation", f"/path-comp-service={pcs.uuid}"),
            ("path-computation", f"/path={path['uuid']}"),
        ]
    return urls


def _get(app: TestClient, url: str) -> dict:
    resp = app.get(url)
    assert resp.status_code == 200, (url, resp.text)
    assert resp.headers["content-type"].startswith("application/yang-data+json"), url
    return resp.json()


_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _probe(app: TestClient, method: str, url: str) -> tuple[int, str]:
    """Status and error message of a request that changes nothing.

    Keys are dummies and bodies are empty, so a write is refused (400, 404 or
    422) before it touches state. A route that does not exist answers 404
    with Starlette's bare "Not Found", which a handler's 404 never says.
    """
    resp = app.request(method, url, json={} if method in ("POST", "PUT", "PATCH") else None)
    if resp.status_code == 204:
        return 204, ""
    errors = resp.json().get("ietf-restconf:errors", {}).get("error", [{}])
    return resp.status_code, errors[0].get("error-message", "")


class TestRouteTable:
    @pytest.mark.parametrize("root", ROOTS)
    @pytest.mark.parametrize("module", list(ROUTES))
    def test_both_forms_serve_every_route_and_method(
        self, app: TestClient, root: str, module: str,
    ) -> None:
        for path, methods in ROUTES[module].items():
            concrete = path.replace("{device_uuid}", "x").replace("{uuid}", "x")
            for method in _METHODS:
                long = _probe(app, method, f"{root}{LONG[module]}{concrete}")
                short = _probe(app, method, f"{root}{SHORT[module]}{concrete}")
                where = (method, f"{root}{LONG[module]}{path}")
                assert long == short, where
                if method in methods:
                    assert long != (404, "Not Found") and long[0] != 405, (where, long)
                else:
                    assert long[0] == 405, (where, long)

    def test_the_paths_follow_the_v2_6_tree(self) -> None:
        # RFC 8040 §3.5.3: the augment's top node is qualified with its own
        # module, because it differs from its parent's (tapi-common); the
        # lists inside it are in the same module and are not.
        for module, path in LONG.items():
            context, container = path.removeprefix("/data/").split("/")
            assert context == "tapi-common:context"
            assert container.startswith(f"tapi-{module}:")
            for sub in ROUTES[module]:
                assert ":" not in sub


class TestLongFormEqualsShortForm:
    @pytest.mark.parametrize("root", ROOTS)
    def test_with_nothing_admitted(self, mesh: TestClient, root: str) -> None:
        for module, url in _get_urls(mesh, admitted=False):
            long = _get(mesh, f"{root}{LONG[module]}{url}")
            assert long == _get(mesh, f"{root}{SHORT[module]}{url}"), (module, url)

    @pytest.mark.parametrize("root", ROOTS)
    def test_with_services_and_a_path_comp_service_admitted(
        self, admitted: TestClient, root: str,
    ) -> None:
        for module, url in _get_urls(admitted, admitted=True):
            long = _get(admitted, f"{root}{LONG[module]}{url}")
            assert long == _get(admitted, f"{root}{SHORT[module]}{url}"), (module, url)

    def test_the_two_mounts_agree(self, admitted: TestClient) -> None:
        for module, url in _get_urls(admitted, admitted=True):
            assert _get(admitted, f"/restconf{LONG[module]}{url}") == _get(
                admitted, f"{LONG[module]}{url}"
            ), (module, url)

    def test_the_containers_equal_the_full_context(self, admitted: TestClient) -> None:
        full = _get(admitted, "/restconf/data/tapi-common:context")["tapi-common:context"]
        for module, path in LONG.items():
            ((key, section),) = _get(admitted, f"/restconf{path}").items()
            assert full[key] == section, module

    def test_percent_encoded_colons_route(self, app: TestClient) -> None:
        encoded = LONG["equipment"].replace(":", "%3A")
        assert _get(app, encoded) == _get(app, LONG["equipment"])


class TestConnectivityContainerGet:
    @pytest.mark.parametrize("root", ROOTS)
    @pytest.mark.parametrize("form", [0, 1], ids=["long", "short"])
    def test_serves_the_container(self, admitted: TestClient, root: str, form: int) -> None:
        container = CONTAINERS["connectivity"][form]
        body = _get(admitted, f"{root}{container}")
        assert set(body) == {"tapi-connectivity:connectivity-context"}
        services = body["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert len(services) == 2
        # The same body as the list GET, which answers with the container.
        assert body == _get(admitted, f"{root}{container}/connectivity-service")

    def test_empty(self, app: TestClient) -> None:
        for container in CONTAINERS["connectivity"]:
            assert _get(app, container) == {
                "tapi-connectivity:connectivity-context": {"connectivity-service": []}
            }

    def test_operational_state_is_refreshed_first(
        self, app: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ctx = app.app.state.context
        calls: list[int] = []
        original = ctx.refresh_operational_states

        async def counting() -> None:
            calls.append(1)
            await original()

        monkeypatch.setattr(ctx, "refresh_operational_states", counting)
        for root in ROOTS:
            for container in CONTAINERS["connectivity"]:
                for path in ("", "/connectivity-service"):
                    before = len(calls)
                    _get(app, f"{root}{container}{path}")
                    assert len(calls) == before + 1, f"{root}{container}{path}"


def _services(app: TestClient, root: str = "") -> dict[str, dict]:
    full = _get(app, f"{root}/data/tapi-common:context")["tapi-common:context"]
    return {
        s["uuid"]: s
        for s in full["tapi-connectivity:connectivity-context"]["connectivity-service"]
    }


@pytest.mark.parametrize("root", ROOTS)
class TestConnectivityMutations:
    @pytest.mark.parametrize(("write", "read"), [(0, 1), (1, 0)], ids=["long-to-short", "short-to-long"])
    def test_create_read_replace_update_delete(
        self, mesh: TestClient, root: str, write: int, read: int,
    ) -> None:
        refs = MeshRefs(mesh.app.state.context)
        writes = f"{root}{CONTAINERS['connectivity'][write]}/connectivity-service"
        reads = f"{root}{CONTAINERS['connectivity'][read]}/connectivity-service"

        resp = mesh.post(writes, json=_service_body(refs, "trx A", "trx C"))
        assert resp.status_code == 201, resp.text
        assert resp.headers["content-type"].startswith("application/yang-data+json")
        uuid = resp.json()[_SVC_KEY]["uuid"]

        mine = _get(mesh, f"{writes}={uuid}")
        assert _get(mesh, f"{reads}={uuid}") == mine
        assert _services(mesh, root)[uuid] == mine[_SVC_KEY]

        # PUT: a full replacement to another source transceiver.
        resp = mesh.put(f"{writes}={uuid}",
                        json=_service_body(refs, "trx B", "trx C", uuid=uuid))
        assert resp.status_code == 200, resp.text
        replaced = _get(mesh, f"{reads}={uuid}")[_SVC_KEY]
        sip = replaced["end-point"][0]["service-interface-point"]["service-interface-point-uuid"]
        assert sip == refs.sip("trx B")

        # PATCH: a name.
        name = [{"value-name": "service-name", "value": f"renamed-{write}"}]
        resp = mesh.patch(f"{writes}={uuid}", json={"name": name})
        assert resp.status_code == 200, resp.text
        assert _get(mesh, f"{reads}={uuid}")[_SVC_KEY]["name"] == name
        assert _services(mesh, root)[uuid]["name"] == name

        assert mesh.delete(f"{writes}={uuid}").status_code == 204
        assert mesh.get(f"{reads}={uuid}").status_code == 404
        assert mesh.delete(f"{reads}={uuid}").status_code == 404
        assert uuid not in _services(mesh, root)

    def test_a_uuid_taken_on_one_form_conflicts_on_the_other(
        self, mesh: TestClient, root: str,
    ) -> None:
        refs = MeshRefs(mesh.app.state.context)
        long, short = (f"{root}{c}/connectivity-service" for c in CONTAINERS["connectivity"])
        uuid = "8a6f3f5e-0000-4000-8000-000000000001"
        body = _service_body(refs, "trx A", "trx C", uuid=uuid)
        assert mesh.post(short, json=body).status_code == 201
        assert mesh.post(long, json=body).status_code == 409


@pytest.mark.parametrize("root", ROOTS)
class TestPathCompServiceMutations:
    @pytest.mark.parametrize(("write", "read"), [(0, 1), (1, 0)], ids=["long-to-short", "short-to-long"])
    def test_create_read_delete(self, mesh: TestClient, root: str, write: int, read: int) -> None:
        refs = MeshRefs(mesh.app.state.context)
        writes = f"{root}{CONTAINERS['path-computation'][write]}"
        reads = f"{root}{CONTAINERS['path-computation'][read]}"

        resp = mesh.post(f"{writes}/path-comp-service", json=_pcs_body(refs, "trx A", "trx B"))
        assert resp.status_code == 201, resp.text
        service = resp.json()[_PCS_KEY]
        path_uuid = service["path"][0]["path-uuid"]

        assert _get(mesh, f"{reads}/path-comp-service={service['uuid']}") == {_PCS_KEY: service}
        assert _get(mesh, f"{reads}/path={path_uuid}")
        full = _get(mesh, f"{root}/data/tapi-common:context")["tapi-common:context"]
        section = full["tapi-path-computation:path-computation-context"]
        assert [s["uuid"] for s in section["path-comp-service"]] == [service["uuid"]]

        assert mesh.delete(f"{writes}/path-comp-service={service['uuid']}").status_code == 204
        assert mesh.get(f"{reads}/path-comp-service={service['uuid']}").status_code == 404
        assert mesh.get(f"{reads}/path={path_uuid}").status_code == 404
        assert _get(mesh, reads) == {
            "tapi-path-computation:path-computation-context": {
                "path-comp-service": [], "path": [],
            }
        }


class TestErrorsOnTheLongForm:
    @pytest.mark.parametrize("root", ROOTS)
    @pytest.mark.parametrize("url", [
        f"{LONG['connectivity']}/connectivity-service=nope",
        f"{LONG['path-computation']}/path-comp-service=nope",
        f"{LONG['path-computation']}/path=nope",
        f"{LONG['equipment']}/device=nope",
        f"{LONG['equipment']}/physical-span=nope",
    ])
    def test_404_is_a_restconf_error(self, app: TestClient, root: str, url: str) -> None:
        resp = app.get(f"{root}{url}")
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/yang-data+json")
        (error,) = resp.json()["ietf-restconf:errors"]["error"]
        assert error["error-tag"] == "data-missing"
        short = app.get(f"{root}{url.replace('/tapi-common:context', '', 1)}")
        assert (short.status_code, short.json()) == (resp.status_code, resp.json())

    @pytest.mark.parametrize("root", ROOTS)
    @pytest.mark.parametrize(("method", "module", "path"), [
        ("POST", "connectivity", ""),
        ("DELETE", "connectivity", ""),
        ("PATCH", "connectivity", "/connectivity-service"),
        ("PUT", "path-computation", "/path-comp-service=x"),
        ("POST", "equipment", "/device"),
    ])
    def test_405_carries_allow(
        self, app: TestClient, root: str, method: str, module: str, path: str,
    ) -> None:
        long = app.request(method, f"{root}{LONG[module]}{path}", json={})
        assert long.status_code == 405
        assert long.headers["allow"]
        assert "ietf-restconf:errors" in long.json()
        short = app.request(method, f"{root}{SHORT[module]}{path}", json={})
        assert (short.status_code, short.headers["allow"]) == (405, long.headers["allow"])

    def test_422_on_a_malformed_body(self, app: TestClient) -> None:
        resp = app.post(f"/restconf{LONG['connectivity']}/connectivity-service",
                        json={_SVC_KEY: [{"end-point": "nope"}]})
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("application/yang-data+json")
        assert "ietf-restconf:errors" in resp.json()

    def test_sip_routes_are_not_shadowed(self, app: TestClient) -> None:
        sips = _get(app, "/restconf/data/tapi-common:context/service-interface-point")
        uuid = sips["tapi-common:context"]["service-interface-point"][0]["uuid"]
        _get(app, f"/restconf/data/tapi-common:context/service-interface-point={uuid}")


class TestOpenApi:
    def test_documents_the_long_forms_only(self, app: TestClient) -> None:
        paths = app.get("/openapi.json").json()["paths"]
        for module, routes in ROUTES.items():
            for path, methods in routes.items():
                documented = paths.get(f"/restconf{LONG[module]}{path}")
                assert documented is not None, (module, path)
                assert {m.upper() for m in documented} == methods
            prefix = f"/restconf{LONG[module]}"
            extra = {
                p[len(prefix):] for p in paths if p == prefix or p.startswith(f"{prefix}/")
            } - set(routes)
            assert extra == set(), (module, extra)
        for path in paths:
            for short in SHORT.values():
                assert not path.startswith(f"/restconf{short}"), path
                assert not path.startswith(short), path
            assert not path.startswith("/data/"), path  # the bare mount

    def test_operation_ids_are_unique(self, app: TestClient) -> None:
        paths = app.get("/openapi.json").json()["paths"]
        ids = [op["operationId"] for item in paths.values() for op in item.values()]
        assert len(ids) == len(set(ids))
