"""The RFC 8040 RESTCONF root, its discovery document, and the one T-API mount.

RFC 8040 §3.1 locates the API under a root resource that a client finds via
``/.well-known/host-meta`` rather than by assuming a path. TwinLight serves
the T-API modules under ``{server.restconf_root}/data/…`` and nowhere else:
the bare ``/data/…`` that earlier releases also served is gone, unless the
root itself is empty, in which case ``/data/…`` *is* the data resource.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.app import create_app
from twinlight.config import GnpyConfig, ServerConfig, TwinConfig

_RESOURCE = "/tapi-common:context/service-interface-point"

#: One GET per T-API module, relative to the data resource.
MODULE_GETS = [
    "/tapi-common:context",
    "/tapi-common:context/service-interface-point",
    "/tapi-common:context/tapi-topology:topology-context",
    "/tapi-common:context/tapi-connectivity:connectivity-context",
    "/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service",
    "/tapi-common:context/tapi-path-computation:path-computation-context",
    "/tapi-common:context/tapi-equipment:physical-context",
    "/tapi-common:context/tapi-oam:oam-context",
    "/tapi-common:context/tapi-oam:oam-context/oam-job",
    "/tapi-common:context/tapi-streaming:stream-context",
]

_SVC_KEY = "tapi-connectivity:connectivity-service"
_CONNECTIVITY = "/tapi-common:context/tapi-connectivity:connectivity-context"
_SERVICES = f"{_CONNECTIVITY}/connectivity-service"


def _with_root(twin_config: TwinConfig, root: str) -> TestClient:
    twin_config.server.restconf_root = root
    return TestClient(create_app(twin_config))


class TestOneMount:
    @pytest.mark.parametrize("path", MODULE_GETS)
    def test_served_under_the_root(self, app: TestClient, path: str) -> None:
        resp = app.get(f"/restconf/data{path}")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/yang-data+json")

    @pytest.mark.parametrize("path", MODULE_GETS)
    def test_the_bare_mount_is_gone(self, app: TestClient, path: str) -> None:
        resp = app.get(f"/data{path}")
        assert resp.status_code == 404
        (error,) = resp.json()["ietf-restconf:errors"]["error"]
        assert error["error-message"] == "Not Found"  # no route, not a missing entry

    def test_the_bare_mount_takes_no_writes(self, app: TestClient) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            resp = app.request(method, f"/data{_SERVICES}=x", json={})
            assert resp.status_code == 404, method

    def test_openapi_documents_t_api_under_the_root_only(self, app: TestClient) -> None:
        paths = app.get("/openapi.json").json()["paths"]
        t_api = [p for p in paths if "tapi-" in p]
        assert len(t_api) > 20
        assert all(p.startswith("/restconf/data/tapi-common:context") for p in t_api), t_api

    def test_percent_encoded_colon_works_under_the_root(
        self, app: TestClient
    ) -> None:
        """PathDecodeMiddleware runs before routing."""
        resp = app.get(
            "/restconf/data/tapi-common%3Acontext/service-interface-point"
        )
        assert resp.status_code == 200


class TestRootResource:
    def test_host_meta_advertises_the_root(self, app: TestClient) -> None:
        resp = app.get("/.well-known/host-meta")
        assert resp.status_code == 200
        # RFC 6415 XRD — the format RFC 8040 §3.1 specifies. A JSON variant
        # would not be found by a conforming client.
        assert resp.headers["content-type"].startswith("application/xrd+xml")
        assert "rel='restconf'" in resp.text
        assert "href='/restconf'" in resp.text

    def test_root_resource_lists_its_children(self, app: TestClient) -> None:
        resp = app.get("/restconf")
        assert resp.status_code == 200
        body = resp.json()["ietf-restconf:restconf"]
        assert "data" in body
        assert body["yang-library-version"] == "2019-01-04"

    def test_yang_library_version(self, app: TestClient) -> None:
        """The capability discovery TAPI_COMPLIANCE.md recorded as missing."""
        resp = app.get("/restconf/yang-library-version")
        assert resp.status_code == 200
        assert resp.json() == {
            "ietf-restconf:yang-library-version": "2019-01-04"
        }


class TestConfigurableRoot:
    @pytest.mark.parametrize("path", MODULE_GETS)
    def test_custom_root(self, twin_config: TwinConfig, path: str) -> None:
        client = _with_root(twin_config, "/api")
        resp = client.get(f"/api/data{path}")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/yang-data+json")
        # Only there: not at the default root, not bare.
        assert client.get(f"/restconf/data{path}").status_code == 404
        assert client.get(f"/data{path}").status_code == 404

    def test_custom_root_is_advertised_and_documented(self, twin_config: TwinConfig) -> None:
        client = _with_root(twin_config, "/api")
        assert "href='/api'" in client.get("/.well-known/host-meta").text
        assert client.get("/api/yang-library-version").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        t_api = [p for p in paths if "tapi-" in p]
        assert t_api and all(p.startswith("/api/data/") for p in t_api)

    @pytest.mark.parametrize("root", ["/api", ""])
    def test_services_are_managed_under_any_root(
        self, twin_config: TwinConfig, root: str
    ) -> None:
        client = _with_root(twin_config, root)
        data = f"{root}/data"
        sips = client.get(f"{data}{_RESOURCE}").json()["tapi-common:context"][
            "service-interface-point"
        ]
        body = {_SVC_KEY: {"end-point": [
            tapi_end_point("a", sips[0]["uuid"]), tapi_end_point("z", sips[1]["uuid"]),
        ]}}
        resp = client.post(f"{data}{_CONNECTIVITY}", json=body)
        assert resp.status_code == 201, resp.text
        uuid = resp.json()[_SVC_KEY]["uuid"]
        assert resp.headers["location"] == f"{data}{_SERVICES}={uuid}"
        name = [{"value-name": "service-name", "value": "renamed"}]
        assert client.patch(f"{data}{_SERVICES}={uuid}", json={"name": name}).status_code == 200
        full = client.get(f"{data}/tapi-common:context").json()["tapi-common:context"]
        (svc,) = full["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert svc["name"] == name
        assert client.get(f"{data}{_SERVICES}").json() == {_SVC_KEY: [svc]}
        assert client.delete(f"{data}{_SERVICES}={uuid}").status_code == 204
        assert client.get(f"{data}{_SERVICES}={uuid}").status_code == 404

    @pytest.mark.parametrize("path", MODULE_GETS)
    def test_empty_root_serves_the_data_resource_at_data(
        self, twin_config: TwinConfig, path: str
    ) -> None:
        client = _with_root(twin_config, "")
        resp = client.get(f"/data{path}")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/yang-data+json")
        assert client.get(f"/restconf/data{path}").status_code == 404

    def test_empty_root_serves_no_host_meta(self, twin_config: TwinConfig) -> None:
        # Clients read the 404 as "T-API is at /data" (twinlight_client.restconf).
        client = _with_root(twin_config, "")
        assert client.get("/.well-known/host-meta").status_code == 404

    @pytest.mark.parametrize("root", ["restconf", "no/leading/slash"])
    def test_relative_root_is_rejected(
        self, edfa_topology_path, root: str
    ) -> None:
        """A root without a leading slash would build unroutable paths."""
        with pytest.raises(ValueError, match="must start with"):
            TwinConfig(
                gnpy=GnpyConfig(topology=edfa_topology_path),
                server=ServerConfig(restconf_root=root),
            )

    def test_trailing_slash_is_normalised(self) -> None:
        """Otherwise the mount would be /restconf//data."""
        assert ServerConfig(restconf_root="/restconf/").restconf_root == (
            "/restconf"
        )
