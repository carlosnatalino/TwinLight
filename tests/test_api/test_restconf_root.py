"""The RFC 8040 RESTCONF root and its discovery document.

RFC 8040 §3.1 locates the API under a root resource that a client finds via
``/.well-known/host-meta`` rather than by assuming a path. TwinLight serves
the T-API modules under that root *and* at the bare ``/data/`` they have
always been at, so clients written against earlier releases keep working.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.config import GnpyConfig, ServerConfig, TwinConfig

_RESOURCE = "/tapi-common:context/service-interface-point"


class TestBothMountsAgree:
    def test_same_resource_at_both_paths(self, app: TestClient) -> None:
        bare = app.get(f"/data{_RESOURCE}")
        rooted = app.get(f"/restconf/data{_RESOURCE}")
        assert bare.status_code == rooted.status_code == 200
        assert bare.json() == rooted.json()

    def test_restconf_media_type_on_both(self, app: TestClient) -> None:
        """A rooted response with application/json would be the gap itself.

        The content-type middleware originally matched only "/data/", so
        this is the assertion that catches it regressing.
        """
        for path in (f"/data{_RESOURCE}", f"/restconf/data{_RESOURCE}"):
            resp = app.get(path)
            assert resp.headers["content-type"].startswith(
                "application/yang-data+json"
            ), path

    def test_percent_encoded_colon_works_under_the_root(
        self, app: TestClient
    ) -> None:
        """PathDecodeMiddleware runs before routing, so it covers both."""
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
    def test_custom_root(self, twin_config: TwinConfig) -> None:
        twin_config.server.restconf_root = "/rc"
        client = TestClient(create_app(twin_config))
        assert client.get(f"/rc/data{_RESOURCE}").status_code == 200
        assert client.get("/rc/yang-library-version").status_code == 200
        assert "href='/rc'" in client.get("/.well-known/host-meta").text
        # The bare mount is unconditional.
        assert client.get(f"/data{_RESOURCE}").status_code == 200

    def test_empty_root_serves_only_bare_paths(
        self, twin_config: TwinConfig
    ) -> None:
        twin_config.server.restconf_root = ""
        client = TestClient(create_app(twin_config))
        assert client.get(f"/data{_RESOURCE}").status_code == 200
        assert client.get(f"/restconf/data{_RESOURCE}").status_code == 404
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
