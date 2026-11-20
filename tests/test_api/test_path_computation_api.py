"""T-API v2.6.0 path computation: path-comp-service and path resources."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs

_BASE = "/restconf/data/tapi-common:context/tapi-path-computation:path-computation-context"
_SVC = f"{_BASE}/path-comp-service"
_SVC_KEY = "tapi-path-computation:path-comp-service"


def _end_points(sip_a: str, sip_z: str) -> list[dict]:
    return [
        {"local-id": "a", "service-interface-point": {"service-interface-point-uuid": sip_a}},
        {"local-id": "z", "service-interface-point": {"service-interface-point-uuid": sip_z}},
    ]


def _two_sips(app: TestClient) -> tuple[str, str]:
    data = app.get("/restconf/data/tapi-common:context/service-interface-point").json()
    sips = data["tapi-common:service-interface-point"]
    return sips[0]["uuid"], sips[1]["uuid"]


class TestContext:
    def test_empty_context_uses_v2_6_names(self, app: TestClient) -> None:
        body = app.get(_BASE).json()["tapi-path-computation:path-computation-context"]
        assert body == {"path-comp-service": [], "path": []}

    def test_rpc_is_gone(self, app: TestClient) -> None:
        resp = app.post(f"{_BASE}/path-computation-service/compute-path", json={})
        assert resp.status_code in (404, 405)


class TestPathCompService:
    def test_create_get_delete(self, app: TestClient) -> None:
        sip_a, sip_z = _two_sips(app)
        resp = app.post(_BASE, json={_SVC_KEY: [{"end-point": _end_points(sip_a, sip_z)}]})
        assert resp.status_code == 201, resp.text
        (service,) = resp.json()[_SVC_KEY]
        assert service["direction"] == "UNIDIRECTIONAL"
        assert "routing-constraint" not in service  # absent, not null
        (ref,) = service["path"][:1]

        path = app.get(f"{_BASE}/path={ref['path-uuid']}").json()["tapi-path-computation:path"][0]
        assert path["link"] and path["node-edge-point"]
        cost = path["routing-constraint"]["cost-characteristic"][0]
        assert cost["cost-name"] == "fiber-length-km" and float(cost["cost-value"]) > 0
        assert set(path) <= {
            "uuid", "name", "layer-protocol-name", "direction", "link",
            "node-edge-point", "routing-constraint",
        }

        listed = app.get(_SVC).json()[_SVC_KEY]
        assert [s["uuid"] for s in listed] == [service["uuid"]]
        assert app.get(f"{_SVC}={service['uuid']}").status_code == 200

        assert app.delete(f"{_SVC}={service['uuid']}").status_code == 204
        assert app.get(f"{_BASE}/path={ref['path-uuid']}").status_code == 404
        assert app.delete(f"{_SVC}={service['uuid']}").status_code == 404

    def test_unknown_sip(self, app: TestClient) -> None:
        sip_a, _ = _two_sips(app)
        resp = app.post(_BASE, json={"end-point": _end_points(sip_a, "nope")})
        assert resp.status_code == 409
        error = resp.json()["ietf-restconf:errors"]["error"][0]
        assert (error["error-tag"], error["error-app-tag"]) == ("data-missing", "instance-required")
        assert "not found" in resp.json()["ietf-restconf:errors"]["error"][0]["error-message"]

    @pytest.mark.parametrize(("extra", "named"), [
        ({"routing-constraint": {"max-allowed-hops": {"value": 3}}}, "max-allowed-hops"),
        ({"routing-constraint": {"diversity-policy": "SRLG"}}, "diversity-policy"),
        ({"topology-constraint": [{"local-id": "1", "include-topology": []}]}, "include-topology"),
        ({"direction": "BIDIRECTIONAL"}, "BIDIRECTIONAL"),
    ])
    def test_unsupported_values_are_refused_by_name(
        self, app: TestClient, extra: dict, named: str,
    ) -> None:
        sip_a, sip_z = _two_sips(app)
        resp = app.post(_BASE, json={"end-point": _end_points(sip_a, sip_z), **extra})
        assert resp.status_code == 400
        assert resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "invalid-value"
        assert named in resp.json()["ietf-restconf:errors"]["error"][0]["error-message"]

    @pytest.mark.parametrize(("extra", "named"), [
        ({"path-set-constraint": {}}, "path-set-constraint"),
        ({"path-set": [{"path-set-uuid": "x"}]}, "path-set"),
        ({"adopt-orphan-path": {}}, "adopt-orphan-path"),
    ])
    def test_unimplemented_members_are_501(
        self, app: TestClient, extra: dict, named: str,
    ) -> None:
        """A standard member the twin does not compute from is refused as
        not implemented, as on a connectivity-service, not ignored."""
        sip_a, sip_z = _two_sips(app)
        resp = app.post(_BASE, json={"end-point": _end_points(sip_a, sip_z), **extra})
        assert resp.status_code == 501, resp.text
        (error,) = resp.json()["ietf-restconf:errors"]["error"]
        assert error["error-tag"] == "operation-not-supported"
        assert named in error["error-message"]
        assert app.get(f"{_BASE}/path-comp-service").json() == {_SVC_KEY: []}

    @pytest.mark.parametrize("leaf", ["capacity", "role", "node-edge-point"])
    def test_unimplemented_end_point_member(self, app: TestClient, leaf: str) -> None:
        sip_a, sip_z = _two_sips(app)
        eps = _end_points(sip_a, sip_z)
        eps[0][leaf] = {}
        resp = app.post(_BASE, json={"end-point": eps})
        assert resp.status_code == 501
        assert resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == (
            "operation-not-supported")
        assert f"end-point[{eps[0]['local-id']}]/{leaf}" in resp.text

    @pytest.mark.parametrize("extra", [
        {"path": [{"path-uuid": "x"}]},
        {"objective-function": {"local-id": "o", "cost-optimization": "MINIMIZE"}},
    ])
    def test_config_false_members_are_ignored(self, app: TestClient, extra: dict) -> None:
        """``path`` and the objective's leaves are config false: state the
        server reports, which an edit neither sets nor refuses (RFC 7950
        §7.21.1), so a GET body can be sent back."""
        sip_a, sip_z = _two_sips(app)
        resp = app.post(_BASE, json={"end-point": _end_points(sip_a, sip_z), **extra})
        assert resp.status_code == 201, resp.text
        (service,) = resp.json()[_SVC_KEY]
        assert service["path"] and service["path"] != [{"path-uuid": "x"}]
        assert "cost-optimization" not in service.get("objective-function", {})

    @pytest.mark.parametrize(("where", "member"), [
        ("service", "foo"),
        ("service", "service-layer"),
        ("end-point", "foo"),
        ("objective-function", "foo"),
    ])
    def test_unknown_members_are_400(self, app: TestClient, where: str, member: str) -> None:
        """RFC 6241 Appendix A: "An unexpected element is present" is
        unknown-element, with the member as bad-element and the node
        holding it as error-path; nothing is created."""
        sip_a, sip_z = _two_sips(app)
        body = {"uuid": "pcs-1", "end-point": _end_points(sip_a, sip_z),
                "objective-function": {"local-id": "o"}}
        node = {"service": body, "end-point": body["end-point"][1],
                "objective-function": body["objective-function"]}[where]
        node[member] = 1
        resp = app.post(_BASE, json={_SVC_KEY: [body]})
        assert resp.status_code == 400, resp.text
        (error,) = resp.json()["ietf-restconf:errors"]["error"]
        assert error["error-tag"] == "unknown-element"
        assert error["error-info"] == {"bad-element": member}
        parent = (
            "/tapi-common:context/tapi-path-computation:path-computation-context"
            "/path-comp-service[uuid='pcs-1']"
        )
        assert error["error-path"] == {
            "service": parent,
            "end-point": f"{parent}/end-point[local-id='{body['end-point'][1]['local-id']}']",
            "objective-function": f"{parent}/objective-function",
        }[where]
        assert member in error["error-message"]
        assert app.get(f"{_BASE}/path-comp-service").json() == {_SVC_KEY: []}


class TestOnTheMesh:
    def test_k_ranked_paths_with_constraints(self, mesh_app: TestClient) -> None:
        refs = MeshRefs(mesh_app.app.state.context)
        body = {
            "end-point": _end_points(refs.sip("trx A"), refs.sip("trx C")),
            "routing-constraint": {"route-objective-function": "MIN_WORK_ROUTE_LATENCY"},
            "topology-constraint": [{"local-id": "1", "exclude-node": [refs.node("roadm B")]}],
        }
        (service,) = mesh_app.post(_BASE, json=body).json()[_SVC_KEY]
        assert service["topology-constraint"] == body["topology-constraint"]
        assert len(service["path"]) >= 2
        b_node = refs.node("roadm B")["node-uuid"]
        for ref in service["path"]:
            path = mesh_app.get(f"{_BASE}/path={ref['path-uuid']}").json()
            (entry,) = path["tapi-path-computation:path"]
            neps = entry["node-edge-point"]
            assert all(n["node-uuid"] != b_node for n in neps)

    def test_no_route_is_a_conflict(self, mesh_app: TestClient) -> None:
        refs = MeshRefs(mesh_app.app.state.context)
        body = {
            "end-point": _end_points(refs.sip("trx A"), refs.sip("trx C")),
            "topology-constraint": [{
                "include-node": [refs.node("roadm D")], "explicit-route": True,
            }],
        }
        resp = mesh_app.post(_BASE, json=body)
        assert resp.status_code == 409
        assert mesh_app.get(_SVC).json()[_SVC_KEY] == []


class TestInternalComputePath:
    def test_ranked_candidates(self, mesh_app: TestClient) -> None:
        refs = MeshRefs(mesh_app.app.state.context)
        body = {
            "end-point": _end_points(refs.sip("trx A"), refs.sip("trx C")),
            "max-candidates": 3,
        }
        paths = mesh_app.post("/internal/compute-path", json=body).json()["path"]
        assert len(paths) == 3
        assert paths[0]["fiber-length-km"] == pytest.approx(160.0)
        assert all(len(p["node"]) >= 4 for p in paths)

    def test_constraints_and_errors(self, mesh_app: TestClient) -> None:
        refs = MeshRefs(mesh_app.app.state.context)
        eps = _end_points(refs.sip("trx A"), refs.sip("trx C"))
        body = {"end-point": eps, "topology-constraint": {
            "include-node": [refs.node("roadm D")],
        }}
        (path,) = mesh_app.post("/internal/compute-path", json=body).json()["path"]
        assert path["fiber-length-km"] == pytest.approx(200.0)

        bad = {"end-point": eps, "routing-constraint": {"max-allowed-delay": {"value": 5}}}
        resp = mesh_app.post("/internal/compute-path", json=bad)
        assert resp.status_code == 400 and "max-allowed-delay" in resp.text
        unknown = {"end-point": [eps[0], {"service-interface-point": {
            "service-interface-point-uuid": "nope"}}]}
        assert mesh_app.post("/internal/compute-path", json=unknown).status_code == 409
