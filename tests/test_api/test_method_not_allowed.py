"""405 Method Not Allowed: a complete ``Allow`` header and an RFC 8040 body.

RFC 9110 §15.5.6: a 405 MUST carry ``Allow`` listing the methods the target
resource supports. FastAPI registers one route per method, and Starlette
built the header from the first route matching the path, so a path served
by several routes named only one of them. RFC 8040 §7 maps 405 to the
``operation-not-supported`` error-tag.

The expectations are derived from the app itself — the paths its OpenAPI
schema documents, and which methods do not answer 405 there — so they stay
right when routes move. Hand-written sets are used only for resources whose
methods are not expected to change.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.auth import parse_tokens
from twinlight.config import TwinConfig

#: Every method a route can serve; each is tried on each path.
_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE")
_BODY_METHODS = ("POST", "PUT", "PATCH")
_RESTCONF = "application/yang-data+json"
_TOKEN = "ops-token-0123456789abcdef"


def _client(twin_config: TwinConfig, root: str) -> TestClient:
    twin_config.server.restconf_root = root
    return TestClient(create_app(twin_config))


def _send(client: TestClient, method: str, url: str, **kwargs):
    if method in _BODY_METHODS:
        kwargs.setdefault("json", {})
    return client.request(method, url, **kwargs)


def _allow(resp) -> list[str]:
    """The Allow header as a list; present (possibly empty) on every 405."""
    assert "allow" in resp.headers, resp.request.url
    value = resp.headers["allow"]
    return [m.strip() for m in value.split(",")] if value.strip() else []


def _assert_405(resp, allowed: set[str] | list[str]) -> None:
    assert resp.status_code == 405, (resp.request.method, resp.request.url)
    allow = _allow(resp)
    assert len(allow) == len(set(allow)), allow
    assert set(allow) == set(allowed), (resp.request.method, resp.request.url, allow)
    if resp.request.method == "HEAD":
        return  # no body on a HEAD response
    assert resp.headers["content-type"].startswith(_RESTCONF)
    (error,) = resp.json()["ietf-restconf:errors"]["error"]
    assert error["error-tag"] == "operation-not-supported"
    assert error["error-type"] == "application"
    assert error["error-message"]


def _concrete(path: str) -> str:
    """A documented path with every parameter filled by a dummy key."""
    return re.sub(r"\{[^}]+\}", "x", path)


class TestEveryDocumentedPath:
    """Sweep every path in the OpenAPI schema with every method."""

    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    def test_each_405_lists_exactly_the_methods_served(
        self, twin_config: TwinConfig, root: str,
    ) -> None:
        client = _client(twin_config, root)
        paths = client.get("/openapi.json").json()["paths"]
        assert any(p.startswith(f"{root}/data/") for p in paths)
        failures = []
        refused_somewhere = False
        for path, operations in paths.items():
            documented = {m.upper() for m in operations}
            url = _concrete(path)
            responses = {m: _send(client, m, url) for m in _METHODS}
            # Served: the methods that do not themselves answer 405.
            served = {m for m, r in responses.items() if r.status_code != 405}
            # Nothing undocumented is served, and every undocumented method
            # is refused (a documented one can be too: POST /transparent/data).
            if not served <= documented:
                failures.append((path, "served but undocumented", served - documented))
            for method, resp in responses.items():
                if resp.status_code != 405:
                    continue
                refused_somewhere = True
                try:
                    _assert_405(resp, served)
                except AssertionError as exc:
                    failures.append((path, method, str(exc)))
        assert refused_somewhere
        assert not failures, failures


class TestMultiRoutePaths:
    """Paths served by more than one route: the case the router got wrong."""

    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    @pytest.mark.parametrize("container", [
        "tapi-connectivity:connectivity-context",
        "tapi-path-computation:path-computation-context",
        "tapi-connectivity:connectivity-context/connectivity-service",
        "tapi-connectivity:connectivity-context/connectivity-service={uuid}",
        "tapi-path-computation:path-computation-context/path-comp-service",
        "tapi-path-computation:path-computation-context/path-comp-service={uuid}",
    ])
    def test_allow_names_every_documented_method(
        self, twin_config: TwinConfig, root: str, container: str,
    ) -> None:
        client = _client(twin_config, root)
        path = f"{root}/data/tapi-common:context/{container}"
        paths = client.get("/openapi.json").json()["paths"]
        if path not in paths:
            pytest.skip(f"{path} is no longer a documented path")
        documented = {m.upper() for m in paths[path]}
        for method in sorted(set(_METHODS) - documented):
            _assert_405(_send(client, method, _concrete(path)), documented)

    def test_the_two_reported_examples(self, app: TestClient) -> None:
        """The cases observed on a running twin, against the documented sets."""
        cc = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
        paths = app.get("/openapi.json").json()["paths"]
        for path, method in [
            (cc, "PATCH"),
            (f"{cc}/connectivity-service={{uuid}}", "POST"),
        ]:
            documented = {m.upper() for m in paths.get(path, {})}
            if not documented or method in documented:
                pytest.skip(f"{method} {path} is no longer a 405")
            assert len(documented) > 1
            _assert_405(_send(app, method, _concrete(path)), documented)


class TestHandWritten:
    """Resources whose methods are settled, checked against literal sets."""

    @pytest.mark.parametrize(("method", "url", "allowed"), [
        ("POST", "/restconf/data/tapi-common:context/service-interface-point", {"GET"}),
        ("DELETE", ("/restconf/data/tapi-common:context/tapi-topology:topology-context/"
                    "topology=x/node=y"), {"GET"}),
        ("POST", "/restconf", {"GET"}),
        ("PUT", "/internal/opm", {"GET"}),
        ("DELETE", "/config/parameters", {"GET", "POST"}),
        ("GET", "/admin/snapshot", {"POST"}),
        ("GET", "/transparent/operations/commit", {"POST"}),
        ("POST", "/health", {"GET"}),
        ("POST", "/metrics", {"GET"}),  # not in the OpenAPI schema
        ("POST", "/.well-known/host-meta", {"GET"}),  # nor this
    ])
    def test_allow(self, app: TestClient, method: str, url: str, allowed: set[str]) -> None:
        _assert_405(_send(app, method, url), allowed)

    def test_post_on_transparent_data_is_refused_by_its_handler(self, app: TestClient) -> None:
        # A route serves it, to explain; the Allow header must not list it.
        for url in ("/transparent/data", "/transparent/data/ElementList"):
            resp = _send(app, "POST", url)
            _assert_405(resp, {"GET", "PUT", "PATCH", "DELETE"})
            assert "Nothing can be created" in resp.json()[
                "ietf-restconf:errors"]["error"][0]["error-message"]
        _assert_405(_send(app, "OPTIONS", "/transparent/data"),
                    {"GET", "PUT", "PATCH", "DELETE"})

    @pytest.mark.parametrize("datastore", [
        "startup", "operational", "ietf-datastores:startup",
        "ietf-datastores%3Aoperational",
    ])
    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_read_only_datastores_allow_only_get(
        self, app: TestClient, datastore: str, method: str,
    ) -> None:
        # PUT, PATCH and DELETE reach the write handler, which refuses them;
        # POST is refused by routing. Either way the resource serves GET only.
        for url in (f"/transparent/ds/{datastore}", f"/transparent/ds/{datastore}/Config"):
            _assert_405(_send(app, method, url), {"GET"})

    @pytest.mark.parametrize("datastore", ["running", "candidate"])
    def test_writable_datastores_allow_writes(self, app: TestClient, datastore: str) -> None:
        _assert_405(_send(app, "POST", f"/transparent/ds/{datastore}"),
                    {"GET", "PUT", "PATCH", "DELETE"})

    def test_an_unknown_datastore_is_still_404(self, app: TestClient) -> None:
        assert _send(app, "PUT", "/transparent/ds/scratch").status_code == 404
        _assert_405(_send(app, "POST", "/transparent/ds/scratch"),
                    {"GET", "PUT", "PATCH", "DELETE"})


class TestHeadAndOptions:
    """Listed only where a route serves them; FastAPI GET routes do not."""

    def test_head_and_options_are_not_listed_unless_served(self, app: TestClient) -> None:
        sips = "/restconf/data/tapi-common:context/service-interface-point"
        for method in ("HEAD", "OPTIONS"):
            resp = _send(app, method, sips)
            _assert_405(resp, {"GET"})

    def test_a_cors_preflight_is_still_answered(self, app: TestClient) -> None:
        resp = app.options("/internal/opm", headers={
            "Origin": "http://localhost:3000",  # in the default cors_origins
            "Access-Control-Request-Method": "GET",
        })
        assert resp.status_code == 200
        assert "access-control-allow-methods" in resp.headers


class TestUnchanged:
    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    @pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
    def test_unknown_paths_stay_404(
        self, twin_config: TwinConfig, root: str, method: str,
    ) -> None:
        client = _client(twin_config, root)
        for url in (f"{root}/data/tapi-common:context/nope", "/internal/nope",
                    "/transparent/nope", "/nope"):
            resp = _send(client, method, url)
            assert resp.status_code == 404, (method, url)
            assert "allow" not in resp.headers

    def test_success_responses_carry_no_allow(self, app: TestClient) -> None:
        for url in ("/health", "/internal/opm", "/restconf/data/tapi-common:context"):
            resp = app.get(url)
            assert resp.status_code == 200
            assert "allow" not in resp.headers


class TestWithAuthentication:
    @pytest.fixture
    def secured(self, twin_config: TwinConfig) -> TestClient:
        config = twin_config.model_copy(
            update={"api_tokens": parse_tokens(f"ops:{_TOKEN}")}
        )
        return TestClient(create_app(config))

    def test_401_comes_before_405(self, secured: TestClient) -> None:
        resp = _send(secured, "POST", "/internal/opm")
        assert resp.status_code == 401
        assert "allow" not in resp.headers
        assert resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "access-denied"

    def test_authenticated_405_carries_allow(self, secured: TestClient) -> None:
        headers = {"Authorization": f"Bearer {_TOKEN}"}
        _assert_405(_send(secured, "POST", "/internal/opm", headers=headers), {"GET"})
        cs = ("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/"
              "connectivity-service={uuid}")
        paths = secured.get("/openapi.json", headers=headers).json()["paths"]
        documented = {m.upper() for m in paths[cs]}
        method = next(m for m in ("POST", "OPTIONS") if m not in documented)
        _assert_405(_send(secured, method, _concrete(cs), headers=headers), documented)


class TestRoutesAddedLater:
    """The header comes from routing, so a new route needs no declaration."""

    def test_a_router_included_later_is_covered(self, twin_config: TwinConfig) -> None:
        from fastapi import APIRouter

        fastapi_app = create_app(twin_config)
        router = APIRouter(prefix="/later")

        @router.get("/thing")
        async def get_thing() -> dict:
            return {}

        @router.delete("/thing")
        async def delete_thing() -> None:
            return None

        @router.api_route("/thing", methods=["HEAD", "OPTIONS"])
        async def probe_thing() -> None:
            return None

        fastapi_app.include_router(router)
        client = TestClient(fastapi_app)
        _assert_405(_send(client, "PATCH", "/later/thing"),
                    {"GET", "HEAD", "DELETE", "OPTIONS"})
        assert client.options("/later/thing").status_code == 200


class TestTransparentRefusals:
    @pytest.mark.parametrize(("path", "refused"), [
        ("/transparent/data", {"POST"}),
        ("/transparent/data/ElementList", {"POST"}),
        ("/transparent/ds/startup", {"PUT", "PATCH", "DELETE"}),
        ("/transparent/ds/ietf-datastores:operational/x", {"PUT", "PATCH", "DELETE"}),
        ("/transparent/ds/running", set()),
        ("/transparent/ds/scratch", set()),
        ("/transparent/ds", set()),
        ("/transparent", set()),
        ("/transparentdata", set()),
        ("/internal/opm", set()),
    ])
    def test_refused_methods(self, path: str, refused: set[str]) -> None:
        from twinlight.api.transparent import refused_methods

        assert refused_methods(path) == refused
