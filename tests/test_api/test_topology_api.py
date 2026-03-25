"""Tests for the TAPI Topology API endpoints."""

from fastapi.testclient import TestClient

PREFIX = "/data/tapi-common:context/tapi-topology:topology-context"


class TestGetTopologyContext:
    def test_returns_200(self, app: TestClient) -> None:
        resp = app.get(PREFIX)
        assert resp.status_code == 200

    def test_has_topology_list(self, app: TestClient) -> None:
        data = app.get(PREFIX).json()
        topos = data["tapi-topology:topology-context"]["topology"]
        assert len(topos) == 1


class TestGetTopology:
    def _get_topo_uuid(self, app: TestClient) -> str:
        data = app.get(PREFIX).json()
        return data["tapi-topology:topology-context"]["topology"][0]["uuid"]

    def test_returns_200(self, app: TestClient) -> None:
        uuid = self._get_topo_uuid(app)
        resp = app.get(f"{PREFIX}/topology={uuid}")
        assert resp.status_code == 200

    def test_topology_has_nodes(self, app: TestClient) -> None:
        uuid = self._get_topo_uuid(app)
        data = app.get(f"{PREFIX}/topology={uuid}").json()
        nodes = data["tapi-topology:topology"][0]["node"]
        assert len(nodes) == 4  # 2 trx + 2 roadm

    def test_topology_has_links(self, app: TestClient) -> None:
        uuid = self._get_topo_uuid(app)
        data = app.get(f"{PREFIX}/topology={uuid}").json()
        links = data["tapi-topology:topology"][0]["link"]
        assert len(links) >= 2

    def test_404_for_unknown(self, app: TestClient) -> None:
        resp = app.get(f"{PREFIX}/topology=nonexistent")
        assert resp.status_code == 404


class TestGetNode:
    def _get_ids(self, app: TestClient) -> tuple[str, str]:
        data = app.get(PREFIX).json()
        topo = data["tapi-topology:topology-context"]["topology"][0]
        return topo["uuid"], topo["node"][0]["uuid"]

    def test_returns_200(self, app: TestClient) -> None:
        topo_uuid, node_uuid = self._get_ids(app)
        resp = app.get(f"{PREFIX}/topology={topo_uuid}/node={node_uuid}")
        assert resp.status_code == 200

    def test_node_has_neps(self, app: TestClient) -> None:
        topo_uuid, node_uuid = self._get_ids(app)
        data = app.get(f"{PREFIX}/topology={topo_uuid}/node={node_uuid}").json()
        node = data["tapi-topology:node"][0]
        assert "owned-node-edge-point" in node

    def test_404_for_unknown(self, app: TestClient) -> None:
        data = app.get(PREFIX).json()
        topo_uuid = data["tapi-topology:topology-context"]["topology"][0]["uuid"]
        resp = app.get(f"{PREFIX}/topology={topo_uuid}/node=nonexistent")
        assert resp.status_code == 404


class TestGetLink:
    def _get_ids(self, app: TestClient) -> tuple[str, str]:
        data = app.get(PREFIX).json()
        topo = data["tapi-topology:topology-context"]["topology"][0]
        return topo["uuid"], topo["link"][0]["uuid"]

    def test_returns_200(self, app: TestClient) -> None:
        topo_uuid, link_uuid = self._get_ids(app)
        resp = app.get(f"{PREFIX}/topology={topo_uuid}/link={link_uuid}")
        assert resp.status_code == 200

    def test_link_has_nep_refs(self, app: TestClient) -> None:
        topo_uuid, link_uuid = self._get_ids(app)
        data = app.get(f"{PREFIX}/topology={topo_uuid}/link={link_uuid}").json()
        link = data["tapi-topology:link"][0]
        assert len(link["node-edge-point"]) == 2

    def test_link_may_have_latency_characteristic(self, app: TestClient) -> None:
        """Links over fiber spans include propagation-delay latency-characteristic."""
        topo_uuid, _ = self._get_ids(app)
        data = app.get(f"{PREFIX}/topology={topo_uuid}").json()
        links = data["tapi-topology:topology"][0]["link"]
        # At least one link with fiber has latency-characteristic (total-size in ns)
        with_latency = [l for l in links if l.get("latency-characteristic")]
        assert len(with_latency) >= 1
        for link in with_latency:
            lc = link["latency-characteristic"][0]
            assert lc.get("traffic-property-name") == "propagation-delay"
            assert isinstance(lc.get("total-size"), int)
            assert lc["total-size"] >= 0

    def test_404_for_unknown(self, app: TestClient) -> None:
        data = app.get(PREFIX).json()
        topo_uuid = data["tapi-topology:topology-context"]["topology"][0]["uuid"]
        resp = app.get(f"{PREFIX}/topology={topo_uuid}/link=nonexistent")
        assert resp.status_code == 404


class TestRestconfErrors:
    """RESTCONF error responses use ietf-restconf:errors format (RFC 8040)."""

    def test_404_returns_yang_data_errors(self, app: TestClient) -> None:
        resp = app.get(
            "/data/tapi-connectivity:connectivity-context/"
            "connectivity-service=nonexistent"
        )
        assert resp.status_code == 404
        assert "application/yang-data+json" in resp.headers.get("content-type", "")
        data = resp.json()
        assert "ietf-restconf:errors" in data
        errors = data["ietf-restconf:errors"]["error"]
        assert len(errors) >= 1
        assert errors[0].get("error-tag") == "data-missing"
        assert "error-message" in errors[0]


class TestHealthEndpoint:
    def test_health(self, app: TestClient) -> None:
        resp = app.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
