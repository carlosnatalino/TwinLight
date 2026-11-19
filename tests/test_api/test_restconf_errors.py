"""RFC 8040 §7 error tags and status codes on the T-API resources.

- A data resource that does not exist is 404 with ``invalid-value``
  (RFC 8040 §4.3 for GET; §4.5, §4.6, §4.7 give PUT, PATCH and DELETE the
  same 404 and tag). ``data-missing`` is a 409 tag in the §7 table.
- A request the twin cannot accept as sent is 400 with the tag that says
  why: RFC 8040 does not use 422.
- A reference to a SIP, node or link that does not exist is a leafref with
  no instance: RFC 7950 §15.5, ``data-missing`` with ``instance-required``,
  which §7 pairs with 409.
- The proprietary endpoints keep their 404 ``data-missing`` and 422.

The 404 sweep is driven by the app's own OpenAPI schema: every keyed path
under the RESTCONF root, with every method it serves, under the default
root, a non-default one and an empty one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.config import TwinConfig

_RESTCONF = "application/yang-data+json"
_CONNECTIVITY = "/data/tapi-common:context/tapi-connectivity:connectivity-context"
_PATH_COMPUTATION = "/data/tapi-common:context/tapi-path-computation:path-computation-context"
_SVC_KEY = "tapi-connectivity:connectivity-service"


def _client(twin_config: TwinConfig, root: str) -> TestClient:
    twin_config.server.restconf_root = root
    return TestClient(create_app(twin_config))


def _error(resp) -> dict:
    assert resp.headers["content-type"].startswith(_RESTCONF), resp.headers
    (error,) = resp.json()["ietf-restconf:errors"]["error"]
    return error


def _keyed_paths(client: TestClient, root: str) -> list[tuple[str, str]]:
    """(method, concrete path) for every keyed RESTCONF operation the app documents."""
    paths = client.get("/openapi.json").json()["paths"]
    data = f"{root}/data/"
    out = []
    for path, operations in paths.items():
        if not path.startswith(data) or "{" not in path:
            continue
        for method in operations:
            if method.upper() in ("GET", "PATCH", "DELETE"):
                out.append((method.upper(), re.sub(r"\{[^}]+\}", "no-such-key", path)))
    return out


class TestMissingResource:
    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    def test_every_keyed_resource_is_404_invalid_value(
        self, twin_config: TwinConfig, root: str,
    ) -> None:
        client = _client(twin_config, root)
        probes = _keyed_paths(client, root)
        assert {m for m, _ in probes} == {"GET", "PATCH", "DELETE"}, probes
        failures = []
        for method, url in probes:
            kwargs = {"json": {}} if method == "PATCH" else {}
            resp = client.request(method, url, **kwargs)
            if resp.status_code != 404:
                failures.append((method, url, resp.status_code))
                continue
            error = _error(resp)
            if error["error-tag"] != "invalid-value" or error["error-message"] == "Not Found":
                failures.append((method, url, error))
        assert not failures, failures

    def test_head_of_a_missing_resource_is_404(self, app: TestClient) -> None:
        resp = app.get(f"/restconf{_CONNECTIVITY}/connectivity-service=nope")
        assert resp.status_code == 404
        assert _error(resp)["error-type"] == "application"

    def test_an_unknown_path_under_the_root_is_a_restconf_404(self, app: TestClient) -> None:
        resp = app.get("/restconf/data/tapi-common:context/no-such-container")
        assert resp.status_code == 404
        assert _error(resp)["error-tag"] == "invalid-value"


class TestBadRequests:
    """Every T-API refusal of a request as sent is a 400 with its tag."""

    @pytest.mark.parametrize("root", ["/restconf", "/api", ""])
    def test_a_body_that_does_not_parse_is_malformed_message(
        self, twin_config: TwinConfig, root: str,
    ) -> None:
        client = _client(twin_config, root)
        for url in (f"{root}{_CONNECTIVITY}", f"{root}{_PATH_COMPUTATION}"):
            resp = client.post(url, content=b"{not json", headers={"Content-Type": _RESTCONF})
            assert resp.status_code == 400, (url, resp.text)
            error = _error(resp)
            # RFC 6241 Appendix A: malformed-message is an rpc error.
            assert (error["error-tag"], error["error-type"]) == ("malformed-message", "rpc")

    def test_a_put_without_a_body_is_invalid_value(self, app: TestClient) -> None:
        """RFC 8040 §4.5: the body MUST be present, else 400 invalid-value."""
        resp = app.put(f"/restconf{_CONNECTIVITY}/connectivity-service=x")
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "invalid-value"

    def test_a_body_of_the_wrong_type_is_bad_element(self, app: TestClient) -> None:
        resp = app.post(f"/restconf{_CONNECTIVITY}", json={_SVC_KEY: [{"end-point": "nope"}]})
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "bad-element"

    def test_a_missing_mandatory_leaf_is_missing_element(self, app: TestClient) -> None:
        resp = app.post(f"/restconf{_CONNECTIVITY}", json={_SVC_KEY: [{
            "end-point": [{"local-id": "a"}, {"local-id": "z"}],
        }]})
        assert resp.status_code == 400
        error = _error(resp)
        assert error["error-tag"] == "missing-element"
        assert "service-interface-point" in error["error-message"]

    def test_more_than_one_entry_is_bad_element(self, app: TestClient) -> None:
        resp = app.post(f"/restconf{_CONNECTIVITY}", json={_SVC_KEY: [{}, {}]})
        assert resp.status_code == 400
        assert _error(resp)["error-message"] == "Expected exactly one connectivity-service, got 2"
        assert _error(resp)["error-tag"] == "bad-element"

    def test_a_value_outside_the_yang_type_is_invalid_value(self, app: TestClient) -> None:
        ctx = app.app.state.context
        a, z = (s.uuid for s in ctx.get_sips()[:2])
        body = {"end-point": [tapi_end_point("a", a), tapi_end_point("z", z)], "direction": "x"}
        resp = app.post(f"/restconf{_CONNECTIVITY}", json={_SVC_KEY: [body]})
        assert resp.status_code == 400
        error = _error(resp)
        assert error["error-tag"] == "invalid-value"
        assert error["error-message"] == "invalid value: direction='x'"

    def test_a_mismatched_key_is_invalid_value(self, app: TestClient) -> None:
        """RFC 8040 §4.5: the key in the body MUST equal the key in the URI."""
        ctx = app.app.state.context
        a, z = (s.uuid for s in ctx.get_sips()[:2])
        body = {"uuid": "one", "end-point": [tapi_end_point("a", a), tapi_end_point("z", z)]}
        resp = app.put(f"/restconf{_CONNECTIVITY}/connectivity-service=other", json={_SVC_KEY: body})
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "invalid-value"
        assert _error(resp)["error-message"] == "Body UUID must match path UUID"

    def test_no_restconf_resource_answers_422(self, app: TestClient) -> None:
        """Every body the twin refuses on a T-API write, none with 422."""
        bodies = [b"[]", b'"x"', b"{}", b'{"end-point": 3}', b"{bad"]
        for url, method in [
            (f"/restconf{_CONNECTIVITY}", "POST"),
            (f"/restconf{_CONNECTIVITY}/connectivity-service=x", "PUT"),
            (f"/restconf{_CONNECTIVITY}/connectivity-service=x", "PATCH"),
            (f"/restconf{_PATH_COMPUTATION}", "POST"),
        ]:
            for body in bodies:
                resp = app.request(method, url, content=body, headers={"Content-Type": _RESTCONF})
                assert resp.status_code != 422, (method, url, body, resp.text)
                assert resp.status_code < 500, (method, url, body, resp.text)


@pytest.fixture(params=["gnpy", "egn"])
def backend_app(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    return TestClient(create_app(_mesh_config(tmp_path, request.param)))


class TestUnresolvedReferences:
    """RFC 7950 §15.5 on both physics backends: admission never starts."""

    def test_an_unknown_sip_on_a_connectivity_service(self, backend_app: TestClient) -> None:
        refs = MeshRefs(backend_app.app.state.context)
        body = {"end-point": [tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", "nope")]}
        for method, url in [
            ("POST", f"/restconf{_CONNECTIVITY}"),
            ("PUT", f"/restconf{_CONNECTIVITY}/connectivity-service=fresh"),
        ]:
            payload = {_SVC_KEY: [{**body, "uuid": "fresh"}]}
            resp = backend_app.request(method, url, json=payload)
            assert resp.status_code == 409, (method, resp.text)
            error = _error(resp)
            assert error["error-tag"] == "data-missing"
            assert error["error-app-tag"] == "instance-required"
            assert error["error-path"] == (
                "/tapi-common:context/tapi-connectivity:connectivity-context"
                "/connectivity-service[uuid='fresh']/end-point[local-id='z']"
                "/service-interface-point/service-interface-point-uuid"
            )
        assert backend_app.app.state.context.get_services() == []

    def test_an_unknown_node_in_a_constraint(self, backend_app: TestClient) -> None:
        refs = MeshRefs(backend_app.app.state.context)
        end_points = [tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]
        constraint = [{"local-id": "1", "exclude-node": [{"topology-uuid": "t", "node-uuid": "n"}]}]
        bare = [{"local-id": ep["local-id"], "service-interface-point": ep["service-interface-point"]}
                for ep in end_points]
        for url, key, eps in [
            (f"/restconf{_CONNECTIVITY}", _SVC_KEY, end_points),
            (f"/restconf{_PATH_COMPUTATION}", "tapi-path-computation:path-comp-service", bare),
        ]:
            resp = backend_app.post(url, json={key: [
                {"end-point": eps, "topology-constraint": constraint}]})
            assert resp.status_code == 409, resp.text
            error = _error(resp)
            assert (error["error-tag"], error["error-app-tag"]) == ("data-missing", "instance-required")
            assert "node n not found in topology t" in error["error-message"]

    def test_an_off_grid_spectrum_is_invalid_value(self, backend_app: TestClient) -> None:
        refs = MeshRefs(backend_app.app.state.context)
        end_points = [tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]
        for ep in end_points:
            ep["layer-protocol-constraint"][0][
                "tapi-photonic-media:otsia-connectivity-service-end-point-spec"
            ]["otsi-config"][0]["central-frequency"] = 193_000_000_000_001
        resp = backend_app.post(f"/restconf{_CONNECTIVITY}", json={_SVC_KEY: [{"end-point": end_points}]})
        assert resp.status_code == 400, resp.text
        assert _error(resp)["error-tag"] == "invalid-value"

    def test_an_admission_refusal_stays_409_resource_denied(self, backend_app: TestClient) -> None:
        refs = MeshRefs(backend_app.app.state.context)
        end_points = [tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]
        constraint = [{"local-id": "1", "exclude-node": [refs.node("roadm B"), refs.node("roadm D"),
                                                          refs.node("roadm E")]}]
        resp = backend_app.post(f"/restconf{_CONNECTIVITY}", json={_SVC_KEY: [
            {"end-point": end_points, "topology-constraint": constraint}]})
        assert resp.status_code == 409, resp.text
        assert _error(resp)["error-tag"] == "resource-denied"


class TestProprietaryEndpointsAreUnchanged:
    def test_a_missing_internal_resource_keeps_data_missing(self, app: TestClient) -> None:
        resp = app.get("/internal/services/nope")
        assert resp.status_code == 404
        assert _error(resp)["error-tag"] == "data-missing"

    def test_an_invalid_internal_request_keeps_422(self, app: TestClient) -> None:
        resp = app.get("/internal/path-info")
        assert resp.status_code == 422
        assert _error(resp)["error-tag"] == "invalid-value"
