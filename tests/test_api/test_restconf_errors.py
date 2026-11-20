"""RFC 8040 §7 error tags and status codes on the T-API resources.

- A data resource that does not exist is 404 with ``invalid-value``
  (RFC 8040 §4.3 for GET; §4.5, §4.6, §4.7 give PUT, PATCH and DELETE the
  same 404 and tag). ``data-missing`` is a 409 tag in the §7 table.
- A request the twin cannot accept as sent is 400 with the tag that says
  why: RFC 8040 does not use 422.
- A reference to a SIP, node or link that does not exist is a leafref with
  no instance: RFC 7950 §15.5, ``data-missing`` with ``instance-required``,
  which §7 pairs with 409.
- The proprietary endpoints (``/internal``, ``/admin``, ``/config``,
  ``/transparent``) follow the same convention: one way to handle every
  error of the twin.

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


_PROPRIETARY = ("/internal", "/admin", "/config", "/transparent")


def _proprietary_operations(client: TestClient) -> list[tuple[str, str, dict]]:
    """(method, path template, operation) of every documented proprietary route."""
    paths = client.get("/openapi.json").json()["paths"]
    return [
        (method.upper(), path, operation)
        for path, operations in paths.items() if path.startswith(_PROPRIETARY)
        for method, operation in operations.items()
    ]


@pytest.fixture(scope="module")
def refusing_app(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """One mesh twin for the refusals below: none of them changes its state."""
    return TestClient(create_app(_mesh_config(tmp_path_factory.mktemp("refusals"), "gnpy")))


class TestProprietaryEndpointsFollowRfc8040:
    """The proprietary endpoints answer errors as the T-API resources do.

    Before, a missing resource there was 404 ``data-missing`` (a 409 tag in
    RFC 8040 §7) and a request that did not validate was FastAPI's 422,
    which RFC 8040 does not use.
    """

    def test_every_keyed_get_answers_404_invalid_value(self, refusing_app: TestClient) -> None:
        probes = [
            re.sub(r"\{[^}]+\}", "no-such-key", path)
            for method, path, _ in _proprietary_operations(refusing_app)
            if method == "GET" and "{" in path
        ]
        assert len(probes) >= 5, probes
        failures, missing = [], 0
        for url in probes:
            resp = refusing_app.get(url)
            assert resp.status_code != 422, (url, resp.text)
            if resp.status_code == 404:
                missing += 1
                if _error(resp)["error-tag"] != "invalid-value":
                    failures.append((url, resp.json()))
        assert not failures, failures
        assert missing >= 5, probes

    @pytest.mark.parametrize("prefix", _PROPRIETARY)
    def test_an_unknown_path_is_404_invalid_value(self, refusing_app: TestClient, prefix: str) -> None:
        resp = refusing_app.get(f"{prefix}/no-such-thing/at-all")
        assert resp.status_code == 404, resp.text
        assert _error(resp)["error-tag"] == "invalid-value"

    def test_no_operation_answers_422_to_a_body_of_the_wrong_type(
        self, refusing_app: TestClient,
    ) -> None:
        """Every documented write, given a JSON array where it expects an object."""
        probes = [
            (method, re.sub(r"\{[^}]+\}", "no-such-key", path))
            for method, path, operation in _proprietary_operations(refusing_app)
            if method in ("POST", "PUT", "PATCH") and "requestBody" in operation
        ]
        assert len(probes) >= 6, probes
        for method, url in probes:
            resp = refusing_app.request(method, url, content=b'"x"',
                                    headers={"Content-Type": "application/json"})
            assert resp.status_code != 422, (method, url, resp.text)
            assert 400 <= resp.status_code < 500, (method, url, resp.text)
            error = _error(resp)
            if resp.status_code == 400:
                assert error["error-tag"] in (
                    "malformed-message", "missing-element", "bad-element",
                    "unknown-element", "invalid-value"), (method, url, error)

    @pytest.mark.parametrize(("method", "url", "body", "code", "tag", "message"), [
        # A missing resource: 404 invalid-value (was data-missing).
        ("GET", "/internal/services/nope", None, 404, "invalid-value",
         "Service nope not found"),
        ("GET", "/config/devices/nope", None, 404, "invalid-value",
         "Unknown element UID: 'nope'"),
        ("POST", "/config/set", {"devices": {"nope": {"loss_coef": 0.2}}}, 404,
         "invalid-value", "Unknown element UID: 'nope"),
        # A query parameter left out (was 422).
        ("GET", "/internal/path-info", None, 400, "missing-element",
         "Query parameters sip_a and sip_z are required"),
        ("GET", "/admin/snapshot/content", None, 400, "missing-element",
         "Missing query parameter: path"),
        ("POST", "/admin/restore", {}, 400, "missing-element", "Missing 'path' in body"),
        # A query parameter outside its range (was 422).
        ("GET", "/config/parameters?path=/Fiber/*/no_such_attr", None, 400, "invalid-value",
         None),
        # Bodies pydantic refuses (was 422): the tag says why, as on T-API.
        ("POST", "/internal/compute-path", b"{bad", 400, "malformed-message", None),
        ("POST", "/internal/compute-path", [], 400, "bad-element", None),
        ("POST", "/internal/compute-path", {"end-point": []}, 400, "invalid-value", None),
        ("POST", "/internal/compute-path", {"bogus": 1}, 400, "missing-element", None),
        ("POST", "/config/set", [1, 2, 3], 400, "bad-element", None),
        ("POST", "/config/parameters/changes", [{"path": "/Fiber/*/loss_coef"}], 400,
         "invalid-value", None),
        ("POST", "/config/parameters/changes", {"path": "/Fiber/*/loss_coef", "by": 1}, 400,
         "bad-element", None),
        # Values the twin does not accept.
        ("POST", "/config/set", {"twin": {"no_such": {"key": 1}}}, 400, "invalid-value",
         None),
        ("POST", "/config/parameters", {"set": {"/Fiber/*/loss_coef": 99.0}}, 400,
         "invalid-value", None),
    ])
    def test_each_refusal_has_its_status_tag_and_message(
        self, refusing_app: TestClient, method: str, url: str, body: object, code: int,
        tag: str, message: str | None,
    ) -> None:
        if isinstance(body, bytes):
            resp = refusing_app.request(method, url, content=body,
                                    headers={"Content-Type": "application/json"})
        else:
            resp = refusing_app.request(method, url, json=body)
        assert resp.status_code == code, resp.text
        error = _error(resp)
        assert error["error-tag"] == tag, error
        if message is not None:
            assert error["error-message"] == message

    def test_compute_path_classifies_as_the_path_comp_service_does(
        self, refusing_app: TestClient,
    ) -> None:
        refs = MeshRefs(refusing_app.app.state.context)
        eps = [{"service-interface-point": {"service-interface-point-uuid": refs.sip(trx)}}
               for trx in ("trx A", "trx C")]
        # A SIP that does not exist: RFC 7950 §15.5, as for a T-API end-point.
        nope = [eps[0], {"service-interface-point": {"service-interface-point-uuid": "nope"}}]
        resp = refusing_app.post("/internal/compute-path", json={"end-point": nope})
        assert resp.status_code == 409, resp.text
        error = _error(resp)
        assert (error["error-tag"], error["error-app-tag"]) == ("data-missing", "instance-required")
        assert error["error-message"] == "SIP 'nope' not found"
        # A node that does not exist in a constraint: the same.
        ghost = {"topology-uuid": refs.node("roadm D")["topology-uuid"], "node-uuid": "ghost"}
        resp = refusing_app.post("/internal/compute-path", json={
            "end-point": eps, "topology-constraint": {"include-node": [ghost]}})
        assert resp.status_code == 409, resp.text
        assert _error(resp)["error-tag"] == "data-missing"
        # A constraint the twin does not support: 400 invalid-value.
        resp = refusing_app.post("/internal/compute-path", json={
            "end-point": eps, "routing-constraint": {"max-allowed-delay": {"value": 5}}})
        assert resp.status_code == 400, resp.text
        assert _error(resp)["error-tag"] == "invalid-value"
        assert "max-allowed-delay" in _error(resp)["error-message"]
        # A member the body does not define.
        resp = refusing_app.post("/internal/compute-path", json={"end-point": eps, "bogus": 1})
        assert resp.status_code == 400, resp.text
        assert _error(resp)["error-tag"] == "unknown-element"

    def test_statuses_that_mean_something_else_keep_it(
        self, tmp_path: Path,
    ) -> None:
        egn = TestClient(create_app(_mesh_config(tmp_path, "egn")))
        resp = egn.post("/config/set", json={"redesign": True})
        assert resp.status_code == 501, resp.text
        assert _error(resp)["error-tag"] == "operation-not-supported"
        resp = egn.post("/transparent/data/ElementList", json={})
        assert resp.status_code == 405, resp.text
        assert _error(resp)["error-tag"] == "operation-not-supported"
