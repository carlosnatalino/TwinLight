"""The requests the web UI sends are accepted by the API.

The UI builds them in twinlight-ui/src/lib/serviceRequest.ts and pins their
exact JSON in fixtures (its own tests check the builder produces them); here
the same fixtures, placeholders replaced by real UUIDs, are posted to the
backend. A change on either side that breaks the other fails a test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config
from twinlight.app import create_app
from twinlight.models.connectivity import OTSIA_CSEP_SPEC

_FIXTURES = Path(__file__).parents[2] / "twinlight-ui" / "src" / "__tests__" / "fixtures"
#: Where the UI creates a service (TapiApiClient.createConnectivityService):
#: the connectivity-context, the list's parent (RFC 8040 §4.4.1).
_CREATE = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
#: What the UI's getServices reads (twinlight-ui/src/api/client.ts).
_SERVICE_LIST = (
    "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
    "/connectivity-service"
)


def _request(name: str, ctx) -> dict:
    """A UI fixture with its placeholders resolved against the mesh."""
    refs = MeshRefs(ctx)
    text = (_FIXTURES / f"add-service-request.{name}.json").read_text()

    def resolve(match: re.Match) -> str:
        kind, uid = match.group(1), match.group(2)
        if kind == "SIP":
            return refs.sip(uid)
        return refs.node(uid)["node-uuid"]

    text = re.sub(r"<(SIP|NODE) ([^>]+)>", resolve, text)
    topology = refs.node("roadm A")["topology-uuid"]
    return json.loads(text.replace("<TOPOLOGY>", topology))


@pytest.fixture(params=["gnpy", "egn"])
def client(request, tmp_path: Path) -> TestClient:
    return TestClient(create_app(_mesh_config(tmp_path, request.param)))


def test_the_default_form_is_admitted(client: TestClient) -> None:
    ctx = client.app.state.context
    resp = client.post(_CREATE, json=_request("defaults", ctx))
    assert resp.status_code == 201, resp.text
    service = resp.json()["tapi-connectivity:connectivity-service"][0]
    assert [ep["direction"] for ep in service["end-point"]] == ["SOURCE", "SINK"]
    # Automatic format: whatever admission chose is reported.
    cfg = service["end-point"][0]["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0]
    assert cfg["modulation"]["standard-modulation-technique"].startswith("MT_DP-")


def test_every_option_of_the_form_is_honoured(client: TestClient) -> None:
    ctx = client.app.state.context
    resp = client.post(_CREATE, json=_request("full", ctx))
    assert resp.status_code == 201, resp.text
    service = resp.json()["tapi-connectivity:connectivity-service"][0]
    cfg = service["end-point"][0]["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0]
    assert cfg["modulation"]["standard-modulation-technique"] == "MT_DP-QAM16"
    assert cfg["central-frequency"] == 191_306_250_000_000
    assert cfg["channel-output-power"] == 150
    path = ctx._allocated_path_uids(service["uuid"])
    assert "roadm B" in path and "roadm D" not in path


def test_the_service_list_answers_with_the_member_the_ui_reads(client: TestClient) -> None:
    # getServices takes the list from tapi-connectivity:connectivity-service
    # and refuses a body without it, so this is the body it must get.
    assert client.get(_SERVICE_LIST).json() == {"tapi-connectivity:connectivity-service": []}
    ctx = client.app.state.context
    resp = client.post(_CREATE, json=_request("defaults", ctx))
    assert resp.status_code == 201, resp.text
    created = resp.json()["tapi-connectivity:connectivity-service"][0]
    body = client.get(_SERVICE_LIST).json()
    assert set(body) == {"tapi-connectivity:connectivity-service"}
    assert [s["uuid"] for s in body["tapi-connectivity:connectivity-service"]] == [
        created["uuid"]
    ]


def _the_created_service(resp) -> dict:
    """What createConnectivityService (singleService) returns: the one entry
    of an array of one, any other body an error."""
    entries = resp.json()["tapi-connectivity:connectivity-service"]
    assert isinstance(entries, list) and len(entries) == 1, entries
    return entries[0]


@pytest.mark.parametrize(("field", "value"), [
    ("administrative-state", "LOCKED"), ("lifecycle-state", "PLANNED"),
])
def test_the_state_change_the_ui_sends_is_applied(
    client: TestClient, field: str, value: str,
) -> None:
    """ServicesPage.handleUpdate sends {"tapi-connectivity:connectivity-service":
    {field: value}} as a PATCH; earlier releases ignored the envelope and
    answered 200 without changing anything."""
    ctx = client.app.state.context
    created = _the_created_service(client.post(_CREATE, json=_request("defaults", ctx)))
    url = f"{_SERVICE_LIST}={created['uuid']}"
    resp = client.patch(url, json={"tapi-connectivity:connectivity-service": {field: value}})
    assert resp.status_code == 200, resp.text
    patched = _the_created_service(resp)  # what the store puts in its list
    assert patched[field] == value
    assert client.get(url).json() == resp.json()

