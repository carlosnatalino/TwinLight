"""HEAD and OPTIONS on every resource (RFC 8040 §4.2, §4.1).

- HEAD answers exactly what GET would, header fields included, with no
  body; where GET is not served it is a 405 too.
- OPTIONS answers 200 with ``Allow`` naming every method the resource
  supports — the same set a 405 there names — and ``Accept-Patch`` on a
  RESTCONF resource that takes PATCH.
- Both need a token when authentication is on; a CORS preflight still
  needs none, and a route added later gets both without declaring them.

The sweeps are driven by the app's OpenAPI schema, under the default
RESTCONF root, a non-default one and an empty one.
"""

from __future__ import annotations

import re

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.app import create_app
from twinlight.auth import parse_tokens
from twinlight.config import TwinConfig

_TOKEN = "head-options-token-0123456789"
_CONNECTIVITY = "/data/tapi-common:context/tapi-connectivity:connectivity-context"
_SVC_KEY = "tapi-connectivity:connectivity-service"
# Header fields that legitimately differ between two responses.
_VOLATILE = {"date", "server"}


def _client(twin_config: TwinConfig, root: str) -> TestClient:
    twin_config.server.restconf_root = root
    return TestClient(create_app(twin_config))


def _allow(resp) -> set[str]:
    return {m.strip() for m in resp.headers["allow"].split(",")}


def _concrete(path: str, key: str = "x") -> str:
    return re.sub(r"\{[^}]+\}", key, path)


def _headers(resp) -> dict[str, str]:
    return {k: v for k, v in resp.headers.items() if k not in _VOLATILE}


def _admit_one(client: TestClient, root: str) -> str:
    ctx = client.app.state.context
    a, z = (s.uuid for s in ctx.get_sips()[:2])
    resp = client.post(f"{root}{_CONNECTIVITY}", json={_SVC_KEY: [
        {"uuid": "head-options-svc",
         "end-point": [tapi_end_point("a", a), tapi_end_point("z", z)]}]})
    assert resp.status_code == 201, resp.text
    return "head-options-svc"


class TestHead:
    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    def test_head_mirrors_get_on_every_documented_path(
        self, twin_config: TwinConfig, root: str,
    ) -> None:
        client = _client(twin_config, root)
        uuid = _admit_one(client, root)
        paths = client.get("/openapi.json").json()["paths"]
        checked = 0
        for path, operations in paths.items():
            if "get" not in operations:
                continue
            # A real key where there is one to find, a dummy elsewhere: both
            # the 200 and the 404 must match their GET.
            for url in {_concrete(path), _concrete(path, uuid)}:
                get, head, again = client.get(url), client.head(url), client.get(url)
                assert head.status_code == get.status_code, url
                assert head.content == b"", url
                # Live measurements (/internal/opm) change between two calls,
                # and with them the length; elsewhere it must be the GET's.
                live = get.content != again.content
                ignore = {"content-length"} if live else set()
                assert {k: v for k, v in _headers(head).items() if k not in ignore} == {
                    k: v for k, v in _headers(get).items() if k not in ignore}, url
                assert "content-length" in head.headers, url
                checked += 1
        assert checked > 50

    def test_head_on_a_resource_without_get_is_405(self, app: TestClient) -> None:
        resp = app.head("/admin/snapshot")
        assert resp.status_code == 405
        assert _allow(resp) == {"POST", "OPTIONS"}
        assert resp.content == b""


class TestOptions:
    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    def test_options_names_what_a_405_names(self, twin_config: TwinConfig, root: str) -> None:
        client = _client(twin_config, root)
        paths = client.get("/openapi.json").json()["paths"]
        for path, operations in paths.items():
            url = _concrete(path)
            resp = client.options(url)
            assert resp.status_code == 200, (url, resp.text)
            assert resp.content == b""
            assert resp.headers["content-length"] == "0"
            allow = _allow(resp)
            documented = {m.upper() for m in operations}
            assert {"OPTIONS"} <= allow, url
            assert ("HEAD" in allow) == ("GET" in allow), url
            assert allow - {"HEAD", "OPTIONS"} <= documented, url
            # A method the resource lacks gets a 405 naming the same set.
            missing = next((m for m in ("TRACE", "POST", "PUT") if m not in allow), None)
            if missing:
                refused = client.request(missing, url, json={} if missing != "TRACE" else None)
                assert refused.status_code == 405, (missing, url)
                assert _allow(refused) == allow, (missing, url)

    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    def test_accept_patch_where_patch_is_allowed(self, twin_config: TwinConfig, root: str) -> None:
        client = _client(twin_config, root)
        entry = client.options(f"{root}{_CONNECTIVITY}/connectivity-service=x")
        assert _allow(entry) == {"GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"}
        assert entry.headers["accept-patch"] == "application/yang-data+json"
        container = client.options(f"{root}{_CONNECTIVITY}")
        assert _allow(container) == {"GET", "HEAD", "POST", "PUT", "OPTIONS"}
        assert "accept-patch" not in container.headers

    def test_an_unknown_path_is_404(self, app: TestClient) -> None:
        for url in ("/nope", "/restconf/data/tapi-common:context/nope"):
            assert app.options(url).status_code == 404

    def test_a_cors_preflight_is_still_answered_by_cors(self, app: TestClient) -> None:
        resp = app.options("/restconf/data/tapi-common:context", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        })
        assert resp.status_code == 200
        assert "access-control-allow-methods" in resp.headers
        assert "allow" not in resp.headers


class TestWithTokens:
    @pytest.fixture
    def secured(self, twin_config: TwinConfig) -> TestClient:
        config = twin_config.model_copy(update={"api_tokens": parse_tokens(f"ops:{_TOKEN}")})
        return TestClient(create_app(config))

    @pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
    def test_a_token_is_needed(self, secured: TestClient, method: str) -> None:
        url = "/restconf/data/tapi-common:context"
        refused = secured.request(method, url)
        assert refused.status_code == 401
        assert "www-authenticate" in refused.headers
        if method == "HEAD":
            assert refused.content == b""
        ok = secured.request(method, url, headers={"Authorization": f"Bearer {_TOKEN}"})
        assert ok.status_code == 200

    def test_a_preflight_needs_none(self, secured: TestClient) -> None:
        resp = secured.options("/restconf/data/tapi-common:context", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET",
        })
        assert resp.status_code == 200


class TestRoutesAddedLater:
    def test_a_route_added_later_gets_both(self, twin_config: TwinConfig) -> None:
        app = create_app(twin_config)
        router = APIRouter(prefix="/later")

        @router.get("/thing")
        async def get_thing() -> dict:
            return {"thing": 1}

        @router.patch("/thing")
        async def patch_thing() -> dict:
            return {}

        app.include_router(router)
        client = TestClient(app)
        head = client.head("/later/thing")
        assert (head.status_code, head.content) == (200, b"")
        assert head.headers["content-length"] == client.get("/later/thing").headers["content-length"]
        options = client.options("/later/thing")
        assert _allow(options) == {"GET", "HEAD", "PATCH", "OPTIONS"}
        assert "accept-patch" not in options.headers  # not a RESTCONF resource
