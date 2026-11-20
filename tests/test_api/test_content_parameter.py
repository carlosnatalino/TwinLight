"""The ``content`` query parameter (RFC 8040 §4.8.1, "MUST be supported").

- ``content=config`` returns the target's configuration descendants and
  ``content=nonconfig`` its non-configuration ones, with the configuration
  ancestors and list keys that place them; together they are the default
  ``content=all`` body.
- A ``config false`` target has no configuration: ``content=config`` is a
  404.
- The classification table (``api/yang_config.py``, from the v2.6.0 YANG)
  classifies every member the twin serves: the sweep fails on one it does
  not, so the table cannot fall behind the bodies.
- ``content`` is refused where §4.8.1 does not allow it.

The sweep is driven by the app's OpenAPI schema (every GET under the
datastore, keyed ones resolved against a twin holding services, a
path-comp-service and their OAM jobs), under three RESTCONF roots and both
physics backends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_api.test_response_bodies import _resolve, _twin
from twinlight.api.restconf_content import SchemaNode, Target, target_of, unclassified

_CONTEXT = "/data/tapi-common:context"
_CONNECTIVITY = f"{_CONTEXT}/tapi-connectivity:connectivity-context"


@pytest.fixture(params=[("gnpy", "/restconf"), ("egn", "/api"), ("gnpy", "")],
                ids=["gnpy-restconf", "egn-api", "gnpy-empty-root"])
def twin(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[TestClient, str]:
    backend, root = request.param
    return _twin(tmp_path, backend, root), root


def _data_urls(client: TestClient, root: str) -> list[str]:
    """Every GET data resource the OpenAPI schema lists, made concrete."""
    full = client.get(f"{root}{_CONTEXT}").json()
    prefix = f"{root}/data"
    urls = []
    for path, ops in client.get("/openapi.json").json()["paths"].items():
        if "get" not in ops or not (path == prefix or path.startswith(prefix + "/")):
            continue
        if "{" in path:
            tail, _member, _entry = _resolve(full, path[len(prefix) + 1:])
            path = f"{prefix}/{tail}"
        urls.append(path)
    assert len(urls) >= 30, urls
    return urls


_MISSING = object()


def _merge(node: SchemaNode, a: Any, b: Any) -> Any:
    """The config view ``a`` and the nonconfig view ``b`` of one node, joined."""
    if a is _MISSING:
        return b
    if b is _MISSING:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for member, value in b.items():
            child = node.child(member) or SchemaNode("x:x", "state")
            out[member] = _merge(child, a.get(member, _MISSING), value)
        return out
    if isinstance(a, list) and isinstance(b, list) and node.kind == "list":
        def key(entry: dict) -> tuple:
            return tuple(entry.get(k) for k in node.keys)
        out_list = [dict(e) for e in a]
        index = {key(e): i for i, e in enumerate(out_list) if all(k in e for k in node.keys)}
        for entry in b:
            i = index.get(key(entry))
            if i is None or not all(k in entry for k in node.keys):
                out_list.append(entry)
            else:
                out_list[i] = _merge(node, out_list[i], entry)
        return out_list
    assert a == b, (node.name, a, b)
    return a


def _canonical(value: Any) -> Any:
    """Lists compared as multisets: the two views order entries differently."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted((_canonical(v) for v in value), key=json.dumps)
    return value


def _leaves(node: SchemaNode, value: Any) -> list[tuple[SchemaNode, Any]]:
    """Every (schema node, value) pair down to leaves and config-false roots."""
    if not node.config or node.kind == "leaf":
        return [(node, value)]
    found = []
    for item in value if isinstance(value, list) else [value]:
        for member, child_value in (item.items() if isinstance(item, dict) else []):
            child = node.child(member)
            assert child is not None, (node.name, member)
            found += _leaves(child, child_value)
    return found


class TestTheSweep:
    def test_every_member_served_is_classified(self, twin) -> None:
        client, root = twin
        for url in _data_urls(client, root):
            target = target_of(url, f"{root}/data")
            assert target is not None, url
            assert unclassified(target, client.get(url).json()) == [], url

    def test_config_and_nonconfig_are_the_two_halves_of_all(self, twin) -> None:
        client, root = twin
        for url in _data_urls(client, root):
            target = target_of(url, f"{root}/data")
            assert target is not None
            whole = client.get(url).json()
            config = client.get(f"{url}?content=config")
            nonconfig = client.get(f"{url}?content=nonconfig")
            assert nonconfig.status_code == 200, (url, nonconfig.text)
            if not target.node.config:
                assert config.status_code == 404, url
                assert config.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == (
                    "invalid-value")
                assert nonconfig.json() == whole, url
                continue
            assert config.status_code == 200, (url, config.text)
            (member,) = whole
            assert list(config.json()) == list(nonconfig.json()) == [member], url
            merged = _merge(target.node, config.json()[member], nonconfig.json()[member])
            assert _canonical(merged) == _canonical(whole[member]), url

    def test_each_view_holds_only_its_kind(self, twin) -> None:
        client, root = twin
        for url in _data_urls(client, root):
            target = target_of(url, f"{root}/data")
            assert target is not None
            if target.node.config:
                (value,) = client.get(f"{url}?content=config").json().values()
                for node, _value in _leaves(target.node, value):
                    assert node.config, (url, node.name)
            (value,) = client.get(f"{url}?content=nonconfig").json().values()
            for node, _value in _leaves(target.node, value):
                # Keys of configuration lists place the state they carry.
                assert not node.config or node.kind == "leaf", (url, node.name)


class TestViews:
    def test_a_service_splits_into_its_request_and_its_state(self, twin) -> None:
        client, root = twin
        url = f"{root}{_CONNECTIVITY}/connectivity-service"
        (svc, _other) = client.get(url).json()["tapi-connectivity:connectivity-service"]
        entry = f"{url}={svc['uuid']}"
        (config,) = client.get(f"{entry}?content=config").json()[
            "tapi-connectivity:connectivity-service"]
        (state,) = client.get(f"{entry}?content=nonconfig").json()[
            "tapi-connectivity:connectivity-service"]
        assert "operational-state" not in config and "lifecycle-state" not in config
        assert config["end-point"] == svc["end-point"]
        assert state == {
            "uuid": svc["uuid"], "operational-state": svc["operational-state"],
            "lifecycle-state": svc["lifecycle-state"],
        }

    def test_the_datastore_config_view_is_the_context_configuration(self, twin) -> None:
        client, root = twin
        data = client.get(f"{root}/data?content=config").json()["ietf-restconf:data"]
        assert list(data) == ["tapi-common:context"]
        context = data["tapi-common:context"]
        assert "service-interface-point" not in context
        assert len(context["tapi-connectivity:connectivity-context"]["connectivity-service"]) == 2
        state = client.get(f"{root}/data?content=nonconfig").json()["ietf-restconf:data"]
        assert set(state) == {
            "tapi-common:context", "ietf-yang-library:yang-library",
            "ietf-yang-library:modules-state", "ietf-restconf-monitoring:restconf-state",
        }
        assert "uuid" not in state["tapi-common:context"]

    def test_topology_configuration_is_its_names_and_link_states(self, twin) -> None:
        client, root = twin
        url = f"{root}{_CONTEXT}/tapi-topology:topology-context"
        (topology,) = client.get(f"{url}?content=config").json()[
            "tapi-topology:topology-context"]["topology"]
        assert "node" not in topology  # config false
        link = topology["link"][0]
        assert "operational-state" not in link and link["administrative-state"]

    def test_an_empty_nonconfig_result_is_the_target_alone(self, twin_config) -> None:
        client = TestClient(_app(twin_config))
        resp = client.get(f"/restconf{_CONNECTIVITY}?content=nonconfig")
        assert resp.status_code == 200
        assert resp.json() == {"tapi-connectivity:connectivity-context": {}}
        resp = client.get(f"/restconf{_CONNECTIVITY}?content=config")
        assert resp.json() == {"tapi-connectivity:connectivity-context": {
            "connectivity-service": []}}

    @pytest.mark.parametrize("tail", [
        "/tapi-common:context/service-interface-point",
        "/tapi-common:context/tapi-streaming:stream-context/available-stream",
        "/ietf-yang-library:yang-library",
        "/ietf-restconf-monitoring:restconf-state/capabilities",
    ])
    def test_a_config_false_target_has_no_configuration(self, app: TestClient, tail: str) -> None:
        resp = app.get(f"/restconf/data{tail}?content=config")
        assert resp.status_code == 404
        assert "config false" in resp.json()["ietf-restconf:errors"]["error"][0][
            "error-message"]
        assert app.head(f"/restconf/data{tail}?content=config").status_code == 404


class TestWhereContentIsAllowed:
    @pytest.mark.parametrize("url", ["/restconf", "/restconf/operations",
                                     "/restconf/yang-library-version"])
    @pytest.mark.parametrize("value", ["all", "config"])
    def test_not_on_the_api_resource(self, app: TestClient, url: str, value: str) -> None:
        resp = app.get(f"{url}?content={value}")
        assert resp.status_code == 400
        error = resp.json()["ietf-restconf:errors"]["error"][0]
        assert error["error-tag"] == "invalid-value"
        assert "datastore and data resources" in error["error-message"]

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_not_on_a_write(self, app: TestClient, method: str) -> None:
        url = f"/restconf{_CONNECTIVITY}/connectivity-service=x?content=config"
        assert app.request(method, url, json={}).status_code == 400

    def test_head_takes_it(self, app: TestClient) -> None:
        url = f"/restconf{_CONTEXT}?content=config"
        get, head = app.get(url), app.head(url)
        assert head.status_code == 200 and head.content == b""
        assert head.headers["content-length"] == get.headers["content-length"]
        assert head.headers["etag"] == get.headers["etag"]

    def test_no_capability_is_added(self, app: TestClient) -> None:
        # content is mandatory and has no capability URI (RFC 8040 §9.1.1).
        caps = app.get(
            "/restconf/data/ietf-restconf-monitoring:restconf-state/capabilities").json()
        assert caps["ietf-restconf-monitoring:capabilities"]["capability"] == [
            "urn:ietf:params:restconf:capability:defaults:1.0?basic-mode=explicit"]


def _app(config):
    from twinlight.app import create_app

    return create_app(config)


def test_target_of_walks_the_schema() -> None:
    root = "/restconf/data"
    service = target_of(f"{root}{_CONNECTIVITY[len('/data'):]}/connectivity-service=a%2Fb", root)
    assert service == Target(service.node, True) and service.node.keys == ("uuid",)
    sip = target_of(f"{root}/tapi-common:context/service-interface-point=x", root)
    assert sip is not None and not sip.node.config
    assert target_of(f"{root}/tapi-common:context/nope", root) is None
    assert target_of(f"{root}/context", root) is None  # top level must be qualified
