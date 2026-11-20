"""The RFC 8040 RESTCONF root, its discovery document, and the one T-API mount.

RFC 8040 §3.1 locates the API under a root resource that a client finds via
``/.well-known/host-meta`` rather than by assuming a path. TwinLight serves
the T-API modules under ``{server.restconf_root}/data/…`` and nowhere else:
the bare ``/data/…`` that earlier releases also served is gone, unless the
root itself is empty, in which case ``/data/…`` *is* the data resource, and
the API resource is ``/``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.app import create_app
from twinlight.auth import parse_tokens
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
        sips = client.get(f"{data}{_RESOURCE}").json()["tapi-common:service-interface-point"]
        body = {_SVC_KEY: {"end-point": [
            tapi_end_point("a", sips[0]["uuid"]), tapi_end_point("z", sips[1]["uuid"]),
        ]}}
        resp = client.post(f"{data}{_CONNECTIVITY}", json=body)
        assert resp.status_code == 201, resp.text
        uuid = resp.json()[_SVC_KEY][0]["uuid"]
        assert resp.headers["location"] == f"{data}{_SERVICES}={uuid}"
        name = [{"value-name": "service-name", "value": "renamed"}]
        patch = client.patch(f"{data}{_SERVICES}={uuid}", json={_SVC_KEY: [{"name": name}]})
        assert patch.status_code == 200
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


_XRD_LINK = "{http://docs.oasis-open.org/ns/xri/xrd-1.0}Link"
_YANG = "application/yang-data+json"
_TOKEN = "discovery-token-0123456789abcdef"


def _discover(client: TestClient, base: str, headers: dict[str, str]) -> str:
    """The API resource ``{+restconf}`` as an RFC 8040 §3.1 client finds it.

    Exactly one ``restconf`` link; its ``href`` resolved against host-meta's
    own URL (RFC 3986 §5.2), as a reference in a retrieved document is.
    """
    resp = client.get(f"{base}/.well-known/host-meta", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/xrd+xml")
    assert resp.headers["cache-control"] == "no-cache"
    links = [link for link in ET.fromstring(resp.text).iter(_XRD_LINK)
             if link.get("rel") == "restconf"]
    assert len(links) == 1, resp.text
    href = links[0].get("href")
    # XRD 1.0 §1.5.2: a URI in an XRD has at least one non-whitespace character.
    assert href and href.strip(), resp.text
    return urljoin(f"{base}/.well-known/host-meta", href)


class TestDiscoveryUnderEveryRoot:
    """RFC 8040 §3.1 and §3.3 hold for the default, a custom and an empty root."""

    @pytest.mark.parametrize("auth", [False, True], ids=["open", "tokens"])
    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    def test_the_api_resource_and_its_children(
        self, twin_config: TwinConfig, root: str, auth: bool,
    ) -> None:
        twin_config.server.restconf_root = root
        headers: dict[str, str] = {}
        if auth:
            twin_config = twin_config.model_copy(
                update={"api_tokens": parse_tokens(f"ui:{_TOKEN}")})
            headers = {"Authorization": f"Bearer {_TOKEN}"}
        base = "http://twin.example:8080"
        client = TestClient(create_app(twin_config), base_url=base)
        if auth:
            assert client.get("/.well-known/host-meta").status_code == 401

        restconf = _discover(client, base, headers)
        # {+restconf} is the root under the host the request reached: a
        # path-absolute href resolves to it, and so does the empty root's
        # absolute URI, whose path is empty.
        assert restconf == f"{base}{root}"
        # §3.3: the API resource and each child, at {+restconf}/<child>. An
        # empty path is requested as "/" (RFC 9110 §4.2.3).
        api = client.get(restconf or "/", headers=headers)
        assert api.status_code == 200, (restconf, api.text)
        assert api.headers["content-type"].startswith(_YANG)
        assert set(api.json()["ietf-restconf:restconf"]) == {
            "data", "operations", "yang-library-version"}
        children = {
            "/data": "ietf-restconf:data",
            "/operations": "ietf-restconf:operations",
            "/yang-library-version": "ietf-restconf:yang-library-version",
        }
        for child, member in children.items():
            resp = client.get(f"{restconf}{child}", headers=headers)
            assert resp.status_code == 200, (child, resp.text)
            assert resp.headers["content-type"].startswith(_YANG), child
            assert member in resp.json(), child
        sips = client.get(f"{restconf}/data{_RESOURCE}", headers=headers)
        assert len(sips.json()["tapi-common:service-interface-point"]) == 2

    def test_an_empty_root_is_named_by_the_host_the_request_reached(
        self, twin_config: TwinConfig,
    ) -> None:
        client = _with_root(twin_config, "")
        for host in ("localhost:18306", "twin", "[::1]:8080"):
            resp = client.get("/.well-known/host-meta", headers={"Host": host})
            assert f"href='http://{host}'" in resp.text, resp.text
        https = TestClient(client.app, base_url="https://twin.example")
        assert "href='https://twin.example'" in https.get("/.well-known/host-meta").text

    def test_the_href_is_escaped(self, twin_config: TwinConfig) -> None:
        client = _with_root(twin_config, "")
        for host in ("a&b", "a'b"):
            resp = client.get("/.well-known/host-meta", headers={"Host": host})
            (link,) = ET.fromstring(resp.text).iter(_XRD_LINK)
            assert link.get("href") == f"http://{host}"

    def test_a_root_owns_slash_only_when_it_is_empty(self, twin_config: TwinConfig) -> None:
        """No collision: nothing else is served at /, /operations or
        /yang-library-version, and with a root they stay unserved."""
        client = _with_root(twin_config, "/restconf")
        for path in ("/", "/operations", "/yang-library-version"):
            assert client.get(path).status_code == 404, path
        for path in ("/docs", "/openapi.json", "/health"):
            assert _with_root(twin_config, "").get(path).status_code == 200, path
