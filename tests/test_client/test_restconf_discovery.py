"""Clients find the twin's RESTCONF root instead of assuming /restconf.

RFC 8040 §3.1: the root is read from ``/.well-known/host-meta``. The twin's
root is configurable (``server.restconf_root``), so a client that hard-coded
``/restconf`` would break on any other; these tests run the client library
against twins with the default root, a custom one, an empty one, an older
twin that serves no host-meta, and authentication on.
"""

from __future__ import annotations

import httpx
import pytest

from twinlight.app import create_app
from twinlight.auth import parse_tokens
from twinlight.config import TwinConfig
from twinlight_client.client import TapiClient
from twinlight_client.credentials import bearer_headers
from twinlight_client.restconf import (
    HOST_META,
    discover_root,
    discover_root_sync,
    parse_host_meta,
)

TOKEN = "ui-token-0123456789abcdef"


def _xrd(href: str) -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<XRD xmlns='http://docs.oasis-open.org/ns/xri/xrd-1.0'>\n"
        f"    <Link rel='restconf' href='{href}'/>\n</XRD>\n"
    )


class TestParseHostMeta:
    def test_reads_the_restconf_link(self) -> None:
        assert parse_host_meta(_xrd("/restconf")) == "/restconf"

    def test_strips_a_trailing_slash(self) -> None:
        assert parse_host_meta(_xrd("/api/")) == "/api"
        assert parse_host_meta(_xrd("/")) == ""

    def test_reduces_an_absolute_href_to_its_path(self) -> None:
        assert parse_host_meta(_xrd("http://twin:8080/top/restconf")) == "/top/restconf"

    def test_an_absolute_href_with_an_empty_path_is_the_empty_root(self) -> None:
        """What a twin with ``restconf_root: ""`` advertises (RFC 3986 §3.3)."""
        assert parse_host_meta(_xrd("http://twin:8080")) == ""
        assert parse_host_meta(_xrd("https://twin.example")) == ""

    def test_no_restconf_link_is_the_server_root(self) -> None:
        xrd = _xrd("/x").replace("rel='restconf'", "rel='lrdd'")
        assert parse_host_meta(xrd) == ""


def _client(twin_config: TwinConfig, token: str | None = None) -> tuple[TapiClient, list[str]]:
    """A TapiClient wired to an in-process twin, and the paths it requests."""
    app = create_app(twin_config)
    seen: list[str] = []

    async def record(request: httpx.Request) -> None:
        seen.append(request.url.path)

    rest = TapiClient("http://twin", token=token)
    rest._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://twin",
        headers=bearer_headers(token),
        event_hooks={"request": [record]},
    )
    return rest, seen


async def _sips(rest: TapiClient) -> list[dict]:
    return await rest.get_sips()


class TestTapiClient:
    async def test_default_root(self, twin_config: TwinConfig) -> None:
        rest, seen = _client(twin_config)
        assert len(await _sips(rest)) == 2
        assert await rest.restconf_root() == "/restconf"
        assert seen == [HOST_META, "/restconf/data/tapi-common:context/service-interface-point"]

    @pytest.mark.parametrize("root", ["/api", "/a/b"])
    async def test_custom_root(self, twin_config: TwinConfig, root: str) -> None:
        twin_config.server.restconf_root = root
        rest, seen = _client(twin_config)
        assert len(await _sips(rest)) == 2
        assert await rest.get_topologies()
        assert await rest.get_connectivity_services() == []
        assert (await rest.get_stream_context())["supported-stream-type"]
        assert await rest.get_oam_jobs() == []
        tapi = [p for p in seen if p != HOST_META]
        assert tapi and all(p.startswith(f"{root}/data/") for p in tapi)

    async def test_discovers_once(self, twin_config: TwinConfig) -> None:
        rest, seen = _client(twin_config)
        await rest.get_context()
        await rest.get_sips()
        await rest.get_topology_context()
        await rest.get_connectivity_services()
        assert seen.count(HOST_META) == 1

    async def test_empty_root_is_discovered(self, twin_config: TwinConfig) -> None:
        # host-meta names the empty root as http://twin, whose path is empty.
        twin_config.server.restconf_root = ""
        rest, seen = _client(twin_config)
        assert len(await _sips(rest)) == 2
        assert await rest.restconf_root() == ""
        assert seen == [HOST_META, "/data/tapi-common:context/service-interface-point"]

    async def test_empty_root_with_a_token(self, twin_config: TwinConfig) -> None:
        twin_config.server.restconf_root = ""
        secured = twin_config.model_copy(update={"api_tokens": parse_tokens(f"ui:{TOKEN}")})
        rest, seen = _client(secured, token=TOKEN)
        assert len(await _sips(rest)) == 2
        assert seen == [HOST_META, "/data/tapi-common:context/service-interface-point"]

    async def test_explicit_root_skips_discovery(self, twin_config: TwinConfig) -> None:
        twin_config.server.restconf_root = "/api"
        rest, seen = _client(twin_config)
        rest._root = "/api"  # what TapiClient(restconf_root="/api") sets
        assert len(await _sips(rest)) == 2
        assert HOST_META not in seen

    def test_explicit_root_is_normalised(self) -> None:
        assert TapiClient(restconf_root="/api/")._root == "/api"
        assert TapiClient()._root is None

    async def test_with_a_token(self, twin_config: TwinConfig) -> None:
        secured = twin_config.model_copy(update={"api_tokens": parse_tokens(f"ui:{TOKEN}")})
        rest, seen = _client(secured, token=TOKEN)
        assert len(await _sips(rest)) == 2
        assert seen[0] == HOST_META

    async def test_without_a_token_discovery_is_refused(self, twin_config: TwinConfig) -> None:
        # host-meta needs a token too; the 401 is raised, not read as "no root".
        secured = twin_config.model_copy(update={"api_tokens": parse_tokens(f"ui:{TOKEN}")})
        rest, _ = _client(secured)
        with pytest.raises(httpx.HTTPStatusError) as err:
            await rest.get_sips()
        assert err.value.response.status_code == 401
        assert rest._root is None  # not cached: a later call retries


class TestDiscoverFunctions:
    async def test_async(self, twin_config: TwinConfig) -> None:
        twin_config.server.restconf_root = "/api"
        transport = httpx.ASGITransport(app=create_app(twin_config))
        async with httpx.AsyncClient(transport=transport, base_url="http://twin") as http:
            assert await discover_root(http) == "/api"

    def test_sync(self, twin_config: TwinConfig) -> None:
        from fastapi.testclient import TestClient

        with TestClient(create_app(twin_config)) as http:
            assert discover_root_sync(http) == "/restconf"

    def test_sync_with_an_empty_root(self, twin_config: TwinConfig) -> None:
        from fastapi.testclient import TestClient

        twin_config.server.restconf_root = ""
        with TestClient(create_app(twin_config)) as http:
            assert http.get(HOST_META).status_code == 200
            assert discover_root_sync(http) == ""

    def test_an_older_twin_without_host_meta(self) -> None:
        """A twin from before host-meta answers 404 and serves T-API at /data."""
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        with httpx.Client(transport=transport, base_url="http://old") as http:
            assert discover_root_sync(http) == ""

    async def test_an_older_twin_without_host_meta_async(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport, base_url="http://old") as http:
            assert await discover_root(http) == ""
