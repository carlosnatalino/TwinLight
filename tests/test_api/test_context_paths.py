"""The connectivity, path-computation and physical contexts at their YANG paths.

``tapi-connectivity``, ``tapi-path-computation`` and ``tapi-equipment`` augment
``/tapi-common:context``, so RFC 8040 §3.5.3 puts their data resources under
``…/data/tapi-common:context/<module>:<container>/…``, and that is the only
place they are served. Earlier releases served them as siblings of the
context (``…/data/<module>:<container>/…``); those short forms are gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app

#: The mounts the canonical paths are tested at: the default RESTCONF root.
#: (The bare /data/ mount is gone; tests/test_api/test_restconf_root.py
#: covers it and other roots.)
ROOTS = ("/restconf",)
#: Where earlier releases served the short forms: under the root and bare.
FORMER_MOUNTS = ("", "/restconf")

#: module → its container's RESTCONF path, relative to the mount.
LONG = {
    "connectivity": "/data/tapi-common:context/tapi-connectivity:connectivity-context",
    "path-computation": (
        "/data/tapi-common:context/tapi-path-computation:path-computation-context"
    ),
    "equipment": "/data/tapi-common:context/tapi-equipment:physical-context",
}
#: The short forms earlier releases served, which must now be gone.
SHORT = {
    "connectivity": "/data/tapi-connectivity:connectivity-context",
    "path-computation": "/data/tapi-path-computation:path-computation-context",
    "equipment": "/data/tapi-equipment:physical-context",
}

#: Every route of the three modules, relative to its container, with the
#: methods it serves. The route-table test below checks this list against
#: what the app actually registers, so a route added later cannot be missed.
ROUTES: dict[str, dict[str, set[str]]] = {
    "connectivity": {
        "": {"GET", "POST"},
        "/connectivity-service": {"GET"},
        "/connectivity-service={uuid}": {"GET", "PUT", "PATCH", "DELETE"},
    },
    "path-computation": {
        "": {"GET", "POST"},
        "/path-comp-service": {"GET"},
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

    """
    refs = MeshRefs(mesh.app.state.context)
    for a in ("trx A", "trx B"):
        resp = mesh.post(f"{ROOTS[-1]}{LONG['connectivity']}",
                         json=_service_body(refs, a, "trx C"))
        assert resp.status_code == 201, resp.text
    resp = mesh.post(f"{ROOTS[-1]}{LONG['path-computation']}",
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
    def test_every_route_and_method_is_served(
        self, app: TestClient, root: str, module: str,
    ) -> None:
        for path, methods in ROUTES[module].items():
            concrete = path.replace("{device_uuid}", "x").replace("{uuid}", "x")
            for method in _METHODS:
                long = _probe(app, method, f"{root}{LONG[module]}{concrete}")
                where = (method, f"{root}{LONG[module]}{path}")
                if method in methods:
                    assert long != (404, "Not Found") and long[0] != 405, (where, long)
                else:
                    assert long[0] == 405, (where, long)

    @pytest.mark.parametrize("root", FORMER_MOUNTS)
    @pytest.mark.parametrize("module", list(ROUTES))
    def test_the_short_forms_are_gone(self, app: TestClient, root: str, module: str) -> None:
        for path in ROUTES[module]:
            concrete = path.replace("{device_uuid}", "x").replace("{uuid}", "x")
            for method in _METHODS:
                url = f"{root}{SHORT[module]}{concrete}"
                assert _probe(app, method, url) == (404, "Not Found"), (method, url)

    @pytest.mark.parametrize("module", list(ROUTES))
    def test_the_bare_mount_is_gone(self, app: TestClient, module: str) -> None:
        for path in ROUTES[module]:
            concrete = path.replace("{device_uuid}", "x").replace("{uuid}", "x")
            for method in _METHODS:
                url = f"{LONG[module]}{concrete}"
                assert _probe(app, method, url) == (404, "Not Found"), (method, url)

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


class TestCanonicalForm:
    @pytest.mark.parametrize("root", ROOTS)
    def test_every_get_answers(self, admitted: TestClient, root: str) -> None:
        for module, url in _get_urls(admitted, admitted=True):
            _get(admitted, f"{root}{LONG[module]}{url}")

    def test_the_containers_equal_the_full_context(self, admitted: TestClient) -> None:
        root = ROOTS[-1]
        full = _get(admitted, f"{root}/data/tapi-common:context")["tapi-common:context"]
        for module, path in LONG.items():
            ((key, section),) = _get(admitted, f"{root}{path}").items()
            assert full[key] == section, module

    def test_percent_encoded_colons_route(self, app: TestClient) -> None:
        url = f"{ROOTS[-1]}{LONG['equipment']}"
        assert _get(app, url.replace(":", "%3A")) == _get(app, url)


class TestConnectivityContainerGet:
    @pytest.mark.parametrize("root", ROOTS)
    def test_serves_the_container(self, admitted: TestClient, root: str) -> None:
        container = LONG["connectivity"]
        body = _get(admitted, f"{root}{container}")
        assert set(body) == {"tapi-connectivity:connectivity-context"}
        services = body["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert len(services) == 2
        # The list GET answers with the list itself (RFC 8040 §4.3): the
        # same entries, under the list's module-qualified name.
        assert _get(admitted, f"{root}{container}/connectivity-service") == {
            _SVC_KEY: services
        }

    def test_empty(self, app: TestClient) -> None:
        assert _get(app, f"{ROOTS[-1]}{LONG['connectivity']}") == {
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
            for path in ("", "/connectivity-service"):
                before = len(calls)
                _get(app, f"{root}{LONG['connectivity']}{path}")
                assert len(calls) == before + 1, f"{root}{path}"


def _services(app: TestClient, root: str = "") -> dict[str, dict]:
    full = _get(app, f"{root}/data/tapi-common:context")["tapi-common:context"]
    return {
        s["uuid"]: s
        for s in full["tapi-connectivity:connectivity-context"]["connectivity-service"]
    }


@pytest.mark.parametrize("root", ROOTS)
class TestConnectivityMutations:
    def test_create_read_replace_update_delete(self, mesh: TestClient, root: str) -> None:
        refs = MeshRefs(mesh.app.state.context)
        services = f"{root}{LONG['connectivity']}/connectivity-service"

        resp = mesh.post(f"{root}{LONG['connectivity']}",
                         json=_service_body(refs, "trx A", "trx C"))
        assert resp.status_code == 201, resp.text
        assert resp.headers["content-type"].startswith("application/yang-data+json")
        uuid = resp.json()[_SVC_KEY]["uuid"]

        mine = _get(mesh, f"{services}={uuid}")
        assert _services(mesh, root)[uuid] == mine[_SVC_KEY]

        # PUT: a full replacement to another source transceiver.
        resp = mesh.put(f"{services}={uuid}",
                        json=_service_body(refs, "trx B", "trx C", uuid=uuid))
        assert resp.status_code == 200, resp.text
        replaced = _get(mesh, f"{services}={uuid}")[_SVC_KEY]
        sip = replaced["end-point"][0]["service-interface-point"]["service-interface-point-uuid"]
        assert sip == refs.sip("trx B")

        # PATCH: a name.
        name = [{"value-name": "service-name", "value": "renamed"}]
        resp = mesh.patch(f"{services}={uuid}", json={"name": name})
        assert resp.status_code == 200, resp.text
        assert _get(mesh, f"{services}={uuid}")[_SVC_KEY]["name"] == name
        assert _services(mesh, root)[uuid]["name"] == name

        assert mesh.delete(f"{services}={uuid}").status_code == 204
        assert mesh.get(f"{services}={uuid}").status_code == 404
        assert mesh.delete(f"{services}={uuid}").status_code == 404
        assert uuid not in _services(mesh, root)


@pytest.mark.parametrize("root", ROOTS)
class TestPathCompServiceMutations:
    def test_create_read_delete(self, mesh: TestClient, root: str) -> None:
        refs = MeshRefs(mesh.app.state.context)
        container = f"{root}{LONG['path-computation']}"

        resp = mesh.post(container, json=_pcs_body(refs, "trx A", "trx B"))
        assert resp.status_code == 201, resp.text
        service = resp.json()[_PCS_KEY]
        path_uuid = service["path"][0]["path-uuid"]

        assert _get(mesh, f"{container}/path-comp-service={service['uuid']}") == {
            _PCS_KEY: service
        }
        assert _get(mesh, f"{container}/path={path_uuid}")
        full = _get(mesh, f"{root}/data/tapi-common:context")["tapi-common:context"]
        section = full["tapi-path-computation:path-computation-context"]
        assert [s["uuid"] for s in section["path-comp-service"]] == [service["uuid"]]

        assert mesh.delete(f"{container}/path-comp-service={service['uuid']}").status_code == 204
        assert mesh.get(f"{container}/path-comp-service={service['uuid']}").status_code == 404
        assert mesh.get(f"{container}/path={path_uuid}").status_code == 404
        assert _get(mesh, container) == {
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
        assert error["error-message"] != "Not Found"  # the handler's, not the router's

    @pytest.mark.parametrize("root", ROOTS)
    @pytest.mark.parametrize(("method", "module", "path"), [
        ("PUT", "connectivity", ""),
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

    def test_422_on_a_malformed_body(self, app: TestClient) -> None:
        resp = app.post(f"{ROOTS[-1]}{LONG['connectivity']}",
                        json={_SVC_KEY: [{"end-point": "nope"}]})
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("application/yang-data+json")
        assert "ietf-restconf:errors" in resp.json()

    def test_sip_routes_are_not_shadowed(self, app: TestClient) -> None:
        sips = _get(app, f"{ROOTS[-1]}/data/tapi-common:context/service-interface-point")
        uuid = sips["tapi-common:context"]["service-interface-point"][0]["uuid"]
        _get(app, f"{ROOTS[-1]}/data/tapi-common:context/service-interface-point={uuid}")


class TestOpenApi:
    def test_documents_the_canonical_forms_only(self, app: TestClient) -> None:
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
            assert not path.startswith("/data/"), path  # the bare mount is gone

    def test_operation_ids_are_unique(self, app: TestClient) -> None:
        paths = app.get("/openapi.json").json()["paths"]
        ids = [op["operationId"] for item in paths.values() for op in item.values()]
        assert len(ids) == len(set(ids))
