"""Response bodies of the T-API resources (RFC 8040 §4.3–§4.6, RFC 7951 §4, §5.4).

- A GET returns the target resource as the body's one member, named with
  its module (RFC 7951 §4: "A namespace-qualified member name MUST be used
  for all members of a top-level JSON object").
- A list entry is an array of one (RFC 7951 §5.4: "A list instance is
  encoded as a name/array pair"), whatever the list.
- The body of a creating POST or PUT (201) and of a PATCH (200) is the
  entry's GET body; a PUT that replaces is 204 with no body (RFC 8040 §4.5).

The entry sweep is driven by the app's OpenAPI schema: every keyed GET,
resolved against the full context of a twin holding services, a
path-comp-service and their OAM jobs, under three RESTCONF roots and both
physics backends.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app

_CONTEXT = "/data/tapi-common:context"
_CONNECTIVITY = f"{_CONTEXT}/tapi-connectivity:connectivity-context"
_PATH_COMPUTATION = f"{_CONTEXT}/tapi-path-computation:path-computation-context"
_SVC_KEY = "tapi-connectivity:connectivity-service"
_PCS_KEY = "tapi-path-computation:path-comp-service"
_SEGMENT = re.compile(r"^(?:(?P<module>[\w-]+):)?(?P<name>[\w-]+)(?:=\{(?P<param>\w+)\})?$")


def _twin(tmp_path: Path, backend: str, root: str) -> TestClient:
    config = _mesh_config(tmp_path, backend)
    config.server.restconf_root = root
    client = TestClient(create_app(config))
    refs = MeshRefs(client.app.state.context)
    for a, z in (("trx A", "trx C"), ("trx B", "trx C")):
        resp = client.post(f"{root}{_CONNECTIVITY}", json={_SVC_KEY: [{"end-point": [
            tapi_end_point("a", refs.sip(a)), tapi_end_point("z", refs.sip(z))]}]})
        assert resp.status_code == 201, resp.text
    resp = client.post(f"{root}{_PATH_COMPUTATION}", json={_PCS_KEY: [{"end-point": [
        {"local-id": "a", "service-interface-point": {"service-interface-point-uuid": refs.sip("trx A")}},
        {"local-id": "z", "service-interface-point": {"service-interface-point-uuid": refs.sip("trx B")}},
    ]}]})
    assert resp.status_code == 201, resp.text
    return client


@pytest.fixture(params=[("gnpy", "/restconf"), ("egn", "/api"), ("gnpy", "")],
                ids=["gnpy-restconf", "egn-api", "gnpy-empty-root"])
def twin(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[TestClient, str]:
    backend, root = request.param
    return _twin(tmp_path, backend, root), root


def _resolve(full: dict, template: str) -> tuple[str, str, dict]:
    """(concrete URL tail, member name, expected entry) for a keyed GET.

    Walks the full context along the path, taking the first entry of each
    list, so the expected entry is what the full-context GET says.
    """
    segments = template.split("/")
    assert segments[0] == "tapi-common:context"
    node: dict = full["tapi-common:context"]
    module = "tapi-common"
    url = ["tapi-common:context"]
    entry: dict = {}
    for segment in segments[1:]:
        match = _SEGMENT.match(segment)
        assert match, segment
        name = match["name"]
        key = f"{match['module']}:{name}" if match["module"] else name
        module = match["module"] or module
        if match["param"] is None:
            node = node[key]
            url.append(segment)
            continue
        entry = node[key][0]
        node = entry
        url.append(f"{key}={entry['uuid']}")
    return "/".join(url), f"{module}:{template.split('/')[-1].split('=')[0].split(':')[-1]}", entry


class TestEntryGets:
    def test_every_keyed_get_is_an_array_of_one(self, twin) -> None:
        client, root = twin
        full = client.get(f"{root}{_CONTEXT}").json()
        prefix = f"{root}/data/"
        paths = client.get("/openapi.json").json()["paths"]
        templates = [p[len(prefix):] for p, ops in paths.items()
                     if p.startswith(prefix) and "{" in p and "get" in ops]
        assert len(templates) == 14, templates
        for template in templates:
            tail, member, expected = _resolve(full, template)
            body = client.get(f"{prefix}{tail}").json()
            assert list(body) == [member], (template, list(body))
            assert isinstance(body[member], list) and len(body[member]) == 1, template
            assert body[member][0] == expected, template

    def test_the_sip_list_is_its_own_member(self, twin) -> None:
        client, root = twin
        full = client.get(f"{root}{_CONTEXT}").json()["tapi-common:context"]
        body = client.get(f"{root}{_CONTEXT}/service-interface-point").json()
        assert body == {"tapi-common:service-interface-point": full["service-interface-point"]}

    @pytest.mark.parametrize("entry", ["supported-stream-type", "available-stream"])
    def test_a_stream_entry_is_that_entry_and_another_uuid_is_404(self, twin, entry: str) -> None:
        client, root = twin
        base = f"{root}{_CONTEXT}/tapi-streaming:stream-context"
        (listed,) = client.get(f"{base}/{entry}").json()[f"tapi-streaming:{entry}"]
        assert client.get(f"{base}/{entry}={listed['uuid']}").json() == {
            f"tapi-streaming:{entry}": [listed]}
        missing = client.get(f"{base}/{entry}=00000000-0000-0000-0000-000000000000")
        assert missing.status_code == 404
        assert missing.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "invalid-value"


class TestWriteBodies:
    def test_post_bodies_are_the_get_bodies(self, twin) -> None:
        client, root = twin
        refs = MeshRefs(client.app.state.context)
        created = client.post(f"{root}{_CONNECTIVITY}", json={_SVC_KEY: {"end-point": [
            tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx B"))]}})
        assert created.status_code == 201, created.text
        assert client.get(created.headers["location"]).json() == created.json()
        (entry,) = created.json()[_SVC_KEY]
        assert entry["uuid"]

    def test_put_replaces_with_204_and_creates_with_201(self, twin) -> None:
        client, root = twin
        refs = MeshRefs(client.app.state.context)
        url = f"{root}{_CONNECTIVITY}/connectivity-service=put-made"
        body = {_SVC_KEY: [{"uuid": "put-made", "end-point": [
            tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx B"))]}]}
        created = client.put(url, json=body)
        assert created.status_code == 201, created.text
        assert created.headers["location"] == url
        assert created.json() == client.get(url).json()
        name = [{"value-name": "service-name", "value": "replaced"}]
        body[_SVC_KEY][0]["name"] = name
        replaced = client.put(url, json=body)
        assert (replaced.status_code, replaced.content) == (204, b"")
        assert client.get(url).json()[_SVC_KEY][0]["name"] == name

    @pytest.mark.parametrize("spelling", ["array", "object", "bare"])
    def test_patch_merges_the_entry_and_answers_its_get_body(self, twin, spelling: str) -> None:
        client, root = twin
        (svc,) = client.get(f"{root}{_CONNECTIVITY}/connectivity-service").json()[_SVC_KEY][:1]
        url = f"{root}{_CONNECTIVITY}/connectivity-service={svc['uuid']}"
        fields = {"lifecycle-state": "PLANNED", "administrative-state": "LOCKED"}
        body = {"array": {_SVC_KEY: [fields]}, "object": {_SVC_KEY: fields}, "bare": fields}[spelling]
        resp = client.patch(url, json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json() == client.get(url).json()
        (patched,) = resp.json()[_SVC_KEY]
        assert (patched["lifecycle-state"], patched["administrative-state"]) == ("PLANNED", "LOCKED")
        assert patched["end-point"] == svc["end-point"]

    def test_patch_refuses_what_it_cannot_merge(self, twin) -> None:
        client, root = twin
        (svc,) = client.get(f"{root}{_CONNECTIVITY}/connectivity-service").json()[_SVC_KEY][:1]
        url = f"{root}{_CONNECTIVITY}/connectivity-service={svc['uuid']}"
        for body, message in [
            ({_SVC_KEY: [{"uuid": "another"}]}, "Body UUID must match path UUID"),
            ({_SVC_KEY: [{"end-point": []}]}, "PATCH cannot change end-point"),
            ({_SVC_KEY: [{"lifecycle-state": "NOPE"}]}, "lifecycle-state"),
        ]:
            resp = client.patch(url, json=body)
            assert resp.status_code == 400, resp.text
            error = resp.json()["ietf-restconf:errors"]["error"][0]
            assert error["error-tag"] == "invalid-value"
            assert message in error["error-message"]
        assert client.get(url).json()[_SVC_KEY][0] == svc
        assert client.patch(f"{url}x", json={}).status_code == 404

    def test_path_comp_service_post_body_is_the_get_body(self, twin) -> None:
        client, root = twin
        refs = MeshRefs(client.app.state.context)
        created = client.post(f"{root}{_PATH_COMPUTATION}", json={_PCS_KEY: {"end-point": [
            {"local-id": "a", "service-interface-point": {"service-interface-point-uuid": refs.sip("trx B")}},
            {"local-id": "z", "service-interface-point": {"service-interface-point-uuid": refs.sip("trx C")}},
        ]}})
        assert created.status_code == 201, created.text
        assert client.get(created.headers["location"]).json() == created.json()
        (entry,) = created.json()[_PCS_KEY]
        assert entry["path"]
