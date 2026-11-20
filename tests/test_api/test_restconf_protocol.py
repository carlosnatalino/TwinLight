"""RESTCONF protocol requirements beyond the T-API resources (RFC 8040).

- §5.5: every RESTCONF response carries ``Cache-Control``; §3.2/§3.3 the
  API resource and every data resource are ``application/yang-data+json``.
- §5.2: an unsupported ``Accept`` is a 406, an unsupported request body
  media type a 415.
- §4.8: an unexpected or repeated query parameter is a 400; §4.8.1's
  mandatory ``content`` is accepted, refused on writes.
- §3.3.1, §3.4: the datastore resource ``{root}/data``; §10 the YANG
  library; §9 ``ietf-restconf-monitoring``; §3.3.2 ``operations``.

Under the default root, a non-default one and an empty one; with tokens,
every check comes after authentication.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.auth import parse_tokens
from twinlight.config import TwinConfig

_RESTCONF = "application/yang-data+json"
_TOKEN = "protocol-token-0123456789"
ROOTS = ["/restconf", "/api", ""]


def _client(twin_config: TwinConfig, root: str) -> TestClient:
    twin_config.server.restconf_root = root
    return TestClient(create_app(twin_config))


def _tag(resp) -> str:
    return resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"]


def _restconf_urls(root: str) -> list[str]:
    data = f"{root}/data"
    urls = [
        data, f"{data}/tapi-common:context", f"{data}/tapi-common:context/service-interface-point",
        f"{data}/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=x",
        f"{data}/ietf-yang-library:yang-library",
    ]
    # The API resource and its other children, under an empty root too: "/"
    # is the API resource then (RFC 8040 §3.3, RFC 9110 §4.2.3).
    return urls + [root or "/", f"{root}/yang-library-version", f"{root}/operations"]


class TestHeaderFields:
    @pytest.mark.parametrize("root", ROOTS)
    def test_every_restconf_response_is_uncacheable_yang_data(
        self, twin_config: TwinConfig, root: str,
    ) -> None:
        client = _client(twin_config, root)
        for url in _restconf_urls(root):
            for method in ("GET", "HEAD", "OPTIONS", "PATCH"):
                resp = client.request(method, url, json={} if method == "PATCH" else None)
                assert resp.headers.get("cache-control") == "no-cache", (method, url)
                if resp.content:
                    assert resp.headers["content-type"] == _RESTCONF, (method, url)

    def test_proprietary_endpoints_keep_their_media_type(self, app: TestClient) -> None:
        resp = app.get("/health")
        assert resp.headers["content-type"] == "application/json"
        assert "cache-control" not in resp.headers

    def test_a_401_carries_them_too(self, twin_config: TwinConfig) -> None:
        config = twin_config.model_copy(update={"api_tokens": parse_tokens(f"ops:{_TOKEN}")})
        resp = TestClient(create_app(config)).get("/restconf/data/tapi-common:context")
        assert resp.status_code == 401
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["content-type"] == _RESTCONF


class TestMediaTypes:
    @pytest.mark.parametrize("accept", [
        "application/yang-data+xml", "application/xml", "text/html",
        "application/yang-data+json;q=0, application/xml",
    ])
    def test_an_output_the_twin_cannot_give_is_406(self, app: TestClient, accept: str) -> None:
        resp = app.get("/restconf/data/tapi-common:context", headers={"Accept": accept})
        assert resp.status_code == 406
        assert _tag(resp) == "invalid-value"

    @pytest.mark.parametrize("accept", [
        "application/yang-data+json", "application/json", "*/*", "application/*",
        "application/yang-data+json, application/json",  # what the UI sends
        "text/html;q=0.9, application/yang-data", "",
    ])
    def test_json_in_any_spelling_is_acceptable(self, app: TestClient, accept: str) -> None:
        resp = app.get("/restconf/data/tapi-common:context", headers={"Accept": accept})
        assert resp.status_code == 200

    @pytest.mark.parametrize("content_type", [
        "application/yang-data+xml", "application/xml", "text/plain",
        "application/x-www-form-urlencoded", "application/yang-patch+json",
    ])
    def test_a_body_the_twin_cannot_read_is_415(self, app: TestClient, content_type: str) -> None:
        cs = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
        for method, url in (("POST", cs), ("PUT", f"{cs}/connectivity-service=x"),
                            ("PATCH", f"{cs}/connectivity-service=x")):
            resp = app.request(method, url, content=b"{}", headers={"Content-Type": content_type})
            assert resp.status_code == 415, (method, content_type)
            assert _tag(resp) == "invalid-value"
        assert app.get(f"{cs}/connectivity-service").json() == {
            "tapi-connectivity:connectivity-service": []}

    def test_a_body_without_a_content_type_is_415(self, app: TestClient) -> None:
        cs = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
        resp = app.post(cs, content=b"{}", headers={"Content-Type": ""})
        assert resp.status_code == 415

    @pytest.mark.parametrize("content_type", [
        _RESTCONF, "application/json", "application/yang-data+json; charset=utf-8",
    ])
    def test_json_bodies_are_read(self, app: TestClient, content_type: str) -> None:
        cs = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
        resp = app.post(cs, content=b'{"end-point": 3}', headers={"Content-Type": content_type})
        assert resp.status_code == 400  # read, and refused for what it says
        assert _tag(resp) == "bad-element"

    def test_proprietary_endpoints_are_not_checked(self, app: TestClient) -> None:
        resp = app.get("/internal/opm", headers={"Accept": "text/html"})
        assert resp.status_code == 200


class TestQueryParameters:
    @pytest.mark.parametrize("root", ROOTS)
    @pytest.mark.parametrize("query", [
        "depth=1", "fields=uuid", "with-defaults=report-all", "filter=x", "insert=first",
        "point=x", "start-time=2026-01-01T00:00:00Z", "vendor=1", "content=bogus",
        "content=all&content=all",
    ])
    def test_an_unexpected_or_repeated_parameter_is_400(
        self, twin_config: TwinConfig, root: str, query: str,
    ) -> None:
        client = _client(twin_config, root)
        resp = client.get(f"{root}/data/tapi-common:context?{query}")
        assert resp.status_code == 400, query
        assert _tag(resp) == "invalid-value"

    def test_content_all_is_the_default(self, app: TestClient) -> None:
        url = "/restconf/data/tapi-common:context/service-interface-point"
        assert app.get(f"{url}?content=all").json() == app.get(url).json()

    @pytest.mark.parametrize("root", ROOTS)
    @pytest.mark.parametrize("value", ["config", "nonconfig"])
    def test_a_content_filter_is_served(
        self, twin_config: TwinConfig, root: str, value: str,
    ) -> None:
        # The filter itself: tests/test_api/test_content_parameter.py.
        client = _client(twin_config, root)
        resp = client.get(f"{root}/data/tapi-common:context?content={value}")
        assert resp.status_code == 200
        assert list(resp.json()) == ["tapi-common:context"]

    def test_content_is_only_for_retrieval(self, app: TestClient) -> None:
        cs = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
        resp = app.post(f"{cs}?content=all", json={})
        assert resp.status_code == 400
        assert "not allowed on POST" in resp.json()["ietf-restconf:errors"]["error"][0][
            "error-message"]

    def test_proprietary_query_parameters_are_untouched(self, app: TestClient) -> None:
        assert app.get("/internal/opm?anything=1").status_code == 200


class TestProtocolResources:
    @pytest.mark.parametrize("root", ROOTS)
    def test_the_datastore_holds_every_top_level_node(
        self, twin_config: TwinConfig, root: str,
    ) -> None:
        client = _client(twin_config, root)
        body = client.get(f"{root}/data").json()
        assert list(body) == ["ietf-restconf:data"]
        data = body["ietf-restconf:data"]
        assert set(data) == {
            "tapi-common:context", "ietf-yang-library:yang-library",
            "ietf-yang-library:modules-state", "ietf-restconf-monitoring:restconf-state",
        }
        assert data["tapi-common:context"] == client.get(
            f"{root}/data/tapi-common:context").json()["tapi-common:context"]

    @pytest.mark.parametrize("root", ROOTS)
    def test_the_yang_library_lists_every_module_used(
        self, twin_config: TwinConfig, root: str,
    ) -> None:
        client = _client(twin_config, root)
        library = client.get(f"{root}/data/ietf-yang-library:yang-library").json()[
            "ietf-yang-library:yang-library"]
        (module_set,) = library["module-set"]
        implemented = {m["name"]: m for m in module_set["module"]}
        # Every module whose prefix appears in a body the twin serves.
        for name in ("tapi-common", "tapi-topology", "tapi-connectivity",
                     "tapi-path-computation", "tapi-equipment", "tapi-oam", "tapi-streaming",
                     "tapi-gnmi-streaming", "tapi-photonic-media", "ietf-yang-library",
                     "ietf-restconf-monitoring"):
            assert name in implemented, name
        assert implemented["tapi-streaming"]["feature"] == ["tapi-streaming"]
        assert implemented["tapi-common"]["namespace"] == "urn:onf:otcc:yang:tapi-common"
        assert {m["name"] for m in module_set["import-only-module"]} >= {"tapi-notification"}
        assert library["content-id"]
        state = client.get(f"{root}/data/ietf-yang-library:modules-state").json()[
            "ietf-yang-library:modules-state"]
        assert state["module-set-id"] == library["content-id"]
        kinds = {m["name"]: m["conformance-type"] for m in state["module"]}
        assert kinds["tapi-common"] == "implement" and kinds["tapi-notification"] == "import"

    @pytest.mark.parametrize("root", ROOTS)
    def test_restconf_state_names_the_defaults_mode(
        self, twin_config: TwinConfig, root: str,
    ) -> None:
        client = _client(twin_config, root)
        caps = client.get(
            f"{root}/data/ietf-restconf-monitoring:restconf-state/capabilities").json()
        assert caps == {"ietf-restconf-monitoring:capabilities": {"capability": [
            "urn:ietf:params:restconf:capability:defaults:1.0?basic-mode=explicit"]}}

    def test_the_api_resource_and_its_children(self, app: TestClient) -> None:
        root = app.get("/restconf")
        assert root.headers["content-type"] == _RESTCONF
        assert root.json()["ietf-restconf:restconf"]["yang-library-version"] == "2019-01-04"
        assert app.get("/restconf/operations").json() == {"ietf-restconf:operations": {}}
        version = app.get("/restconf/yang-library-version")
        assert version.json() == {"ietf-restconf:yang-library-version": "2019-01-04"}

    def test_the_datastore_takes_put_only(self, app: TestClient) -> None:
        """RFC 8040 §4.5: "The PUT method MUST be supported for data and
        datastore resources"; POST, PATCH and DELETE are not offered."""
        for method in ("POST", "PATCH", "DELETE"):
            resp = app.request(method, "/restconf/data", json={})
            assert resp.status_code == 405, method
            assert resp.headers["allow"] == "GET, HEAD, PUT, OPTIONS"
        resp = app.put("/restconf/data", json={})
        assert resp.status_code == 400
        assert _tag(resp) == "invalid-value"


class TestWithTokens:
    def test_authentication_comes_first(self, twin_config: TwinConfig) -> None:
        config = twin_config.model_copy(update={"api_tokens": parse_tokens(f"ops:{_TOKEN}")})
        client = TestClient(create_app(config))
        url = "/restconf/data/tapi-common:context?depth=1"
        assert client.get(url, headers={"Accept": "text/html"}).status_code == 401
        authorised = {"Authorization": f"Bearer {_TOKEN}"}
        assert client.get(url, headers=authorised).status_code == 400
        assert client.get("/restconf/data", headers=authorised).status_code == 200
