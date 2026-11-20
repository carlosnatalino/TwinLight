"""PUT on the configuration containers and the datastore (RFC 8040 §4.5).

"The PUT method MUST be supported for data and datastore resources. A PUT on
the datastore resource is used to replace the entire contents of the
datastore. A PUT on a data resource only replaces that data resource within
the datastore." The body is the complete new representation: entries absent
from it are deleted, new ones admitted, changed ones replaced, and the whole
replacement is one all-or-nothing change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.config import TwinConfig
from twinlight.models.connectivity import MCG_CSEP_SPEC

_R = "/restconf/data/tapi-common:context"
_CC = f"{_R}/tapi-connectivity:connectivity-context"
_PC = f"{_R}/tapi-path-computation:path-computation-context"
_SVC = "tapi-connectivity:connectivity-service"
_PCS = "tapi-path-computation:path-comp-service"

GRID_LOWER = 190_696_875_000_000
SLOT = 6_250_000_000
WIDTH = 9  # DP-QPSK: 8 slots and a guard


def _block(start: int) -> dict:
    return {"lower-frequency": GRID_LOWER + start * SLOT,
            "upper-frequency": GRID_LOWER + (start + WIDTH) * SLOT}


def _service(refs: MeshRefs, uuid: str, a: str, z: str, block: int | None = None) -> dict:
    ends = [tapi_end_point("a", refs.sip(a)), tapi_end_point("z", refs.sip(z))]
    if block is not None:
        for ep in ends:
            ep["layer-protocol-constraint"][0][MCG_CSEP_SPEC] = {
                "mc-spectrum-config-pac": [{"local-id": "1", "spectrum": _block(block)}],
            }
    return {"uuid": uuid, "end-point": ends}


def _pcs(refs: MeshRefs, uuid: str, a: str, z: str) -> dict:
    return {"uuid": uuid, "end-point": [
        {"local-id": "a", "service-interface-point": {"service-interface-point-uuid": refs.sip(a)}},
        {"local-id": "z", "service-interface-point": {"service-interface-point-uuid": refs.sip(z)}},
    ]}


def _error(resp) -> dict:
    (error,) = resp.json()["ietf-restconf:errors"]["error"]
    return error


def _uuids(app: TestClient) -> list[str]:
    return [s["uuid"] for s in app.get(f"{_CC}/connectivity-service").json()[_SVC]]


@pytest.fixture(params=["gnpy", "egn"])
def mesh(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    """The mesh twin, once per physics backend: admission is involved."""
    return TestClient(create_app(_mesh_config(tmp_path, request.param)))


def _populate(app: TestClient) -> MeshRefs:
    """Three services and a path-comp-service, each with its OAM job."""
    refs = MeshRefs(app.app.state.context)
    for uuid, a, z in (("old-1", "trx A", "trx C"), ("old-2", "trx B", "trx A"),
                       ("old-3", "trx C", "trx B")):
        resp = app.post(_CC, json={_SVC: [_service(refs, uuid, a, z)]})
        assert resp.status_code == 201, resp.text
    resp = app.post(_PC, json={_PCS: [_pcs(refs, "pcs-1", "trx A", "trx B")]})
    assert resp.status_code == 201, resp.text
    return refs


def _everything(app: TestClient) -> dict:
    """Every GET body that shows services, spectrum, jobs or path results.

    The OPM views are left out: they move with the wall clock by design (the
    transient models), so two reads never agree. What they are computed from
    — the cached baselines and the EDFA tracker — is compared by
    :func:`_internals` instead.
    """
    urls = ["/restconf/data", "/internal/links", "/internal/spectrum-grid",
            "/internal/spectrum-context", "/config/get"]
    for uuid in ("old-1", "old-2", "old-3"):
        urls += [f"/internal/services/{uuid}"]
    bodies = {}
    for url in urls:
        resp = app.get(url)
        assert resp.status_code == 200, (url, resp.text)
        bodies[url] = resp.json()
    return bodies


def _internals(app: TestClient) -> dict:
    """What a GET does not show but a later admission depends on."""
    ctx = app.app.state.context
    return {
        "spectrum": {e: a.tolist() for e, a in ctx._spectrum_state._slots.items()},
        "allocation": dict(ctx._service_allocation),
        "baselines": {u: id(b) for u, b in ctx._baseline_cache.items()},
        "element_to_services": {u: set(s) for u, s in ctx._element_to_services.items() if s},
        "edfa_counts": dict(ctx._edfa_tracker._channel_count),
        "edfa_events": dict(ctx._edfa_tracker._last_event),
        "oam_jobs": dict(ctx._oam_jobs),
        "paths": dict(ctx._paths),
    }


class TestAllOrNothing:
    """A PUT that deletes three services and creates two leaves the twin
    exactly as it was when the last creation is refused."""

    def test_connectivity_context(self, mesh: TestClient) -> None:
        refs = _populate(mesh)
        assert _everything(mesh) == _everything(mesh)  # nothing drifts by itself
        before, internals = _everything(mesh), _internals(mesh)
        # new-2 asks for the block new-1 was just given on the same
        # transceiver: admission refuses it, after new-1 was admitted.
        body = {"tapi-connectivity:connectivity-context": {"connectivity-service": [
            _service(refs, "new-1", "trx A", "trx C", block=200),
            _service(refs, "new-2", "trx A", "trx B", block=200),
        ]}}
        resp = mesh.put(_CC, json=body)
        assert resp.status_code == 409, resp.text
        error = _error(resp)
        assert error["error-tag"] == "resource-denied"
        assert error["error-path"] == (
            "/tapi-common:context/tapi-connectivity:connectivity-context"
            "/connectivity-service[uuid='new-2']")
        assert "nothing was changed" in error["error-message"]
        assert _everything(mesh) == before
        assert _internals(mesh) == internals
        # And the twin still admits into what it holds: the old blocks are
        # taken, new-1's is free.
        retry = mesh.post(_CC, json={_SVC: [_service(refs, "new-1", "trx A", "trx C", 200)]})
        assert retry.status_code == 201, retry.text

    def test_context_with_both_lists(self, mesh: TestClient) -> None:
        refs = _populate(mesh)
        before, internals = _everything(mesh), _internals(mesh)
        context = mesh.get(_R).json()
        body = context["tapi-common:context"]
        body["tapi-path-computation:path-computation-context"]["path-comp-service"] = [
            _pcs(refs, "pcs-2", "trx B", "trx C")]
        body["tapi-connectivity:connectivity-context"]["connectivity-service"] = [
            _service(refs, "new-1", "trx A", "trx C", block=200),
            _service(refs, "new-2", "trx A", "trx B", block=200),
        ]
        resp = mesh.put(_R, json=context)
        assert resp.status_code == 409, resp.text
        assert _everything(mesh) == before
        assert _internals(mesh) == internals


class TestConnectivityContext:
    def test_replace_keep_create_delete(self, mesh: TestClient) -> None:
        refs = _populate(mesh)
        held = {s["uuid"]: s for s in mesh.get(f"{_CC}/connectivity-service").json()[_SVC]}
        kept_object = mesh.app.state.context.get_service("old-1")
        changed = _service(refs, "old-2", "trx B", "trx C")
        body = {"tapi-connectivity:connectivity-context": {"connectivity-service": [
            held["old-1"],  # as its GET returned it: kept, not re-admitted
            changed,  # replaced
            _service(refs, "new-1", "trx C", "trx A"),  # created
        ]}}  # old-3 absent: deleted
        resp = mesh.put(_CC, json=body)
        assert resp.status_code == 204, resp.text
        assert resp.content == b""
        assert sorted(_uuids(mesh)) == ["new-1", "old-1", "old-2"]
        ctx = mesh.app.state.context
        assert ctx.get_service("old-1") is kept_object
        after = {s["uuid"]: s for s in mesh.get(f"{_CC}/connectivity-service").json()[_SVC]}
        assert after["old-1"] == held["old-1"]
        z = after["old-2"]["end-point"][1]["service-interface-point"]
        assert z["service-interface-point-uuid"] == refs.sip("trx C")
        jobs = mesh.get(f"{_R}/tapi-oam:oam-context/oam-job").json()["tapi-oam:oam-job"]
        assert sorted(j["connectivity-service-end-point"]["connectivity-service-uuid"]
                      for j in jobs) == ["new-1", "old-1", "old-2"]

    def test_an_empty_list_deletes_every_service(self, mesh: TestClient) -> None:
        _populate(mesh)
        resp = mesh.put(_CC, json={"tapi-connectivity:connectivity-context": {}})
        assert resp.status_code == 204
        assert _uuids(mesh) == []
        ctx = mesh.app.state.context
        assert ctx._service_allocation == {}
        assert not any(slots.any() for slots in ctx._spectrum_state._slots.values())
        # The path-comp-services are another list: untouched.
        assert mesh.get(f"{_PC}/path-comp-service").json()[_PCS][0]["uuid"] == "pcs-1"

    def test_the_body_is_the_container(self, app: TestClient) -> None:
        for body in ({}, {_SVC: []},
                     {"tapi-connectivity:connectivity-context": {}, "foo": 1}):
            resp = app.put(_CC, json=body)
            assert resp.status_code == 400, body
            assert _error(resp)["error-tag"] == "invalid-value"

    def test_entries_need_their_key_once(self, app: TestClient) -> None:
        refs_free = {"end-point": []}
        resp = app.put(_CC, json={"tapi-connectivity:connectivity-context": {
            "connectivity-service": [refs_free]}})
        assert resp.status_code == 400
        assert (_error(resp)["error-tag"], _error(resp)["error-info"]) == (
            "missing-element", {"bad-element": "uuid"})
        resp = app.put(_CC, json={"tapi-connectivity:connectivity-context": {
            "connectivity-service": [{"uuid": "x"}, {"uuid": "x"}]}})
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "invalid-value"

    def test_unknown_and_unimplemented_members(self, app: TestClient) -> None:
        resp = app.put(_CC, json={"tapi-connectivity:connectivity-context": {"foo": []}})
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "unknown-element"
        assert _error(resp)["error-path"] == (
            "/tapi-common:context/tapi-connectivity:connectivity-context")
        resp = app.put(_CC, json={"tapi-connectivity:connectivity-context": {
            "connection": [{"uuid": "c"}]}})
        assert resp.status_code == 501
        assert _error(resp)["error-tag"] == "operation-not-supported"

    def test_a_missing_sip_changes_nothing(self, mesh: TestClient) -> None:
        refs = _populate(mesh)
        bad = _service(refs, "new-1", "trx A", "trx C")
        bad["end-point"][1]["service-interface-point"]["service-interface-point-uuid"] = "nope"
        resp = mesh.put(_CC, json={"tapi-connectivity:connectivity-context": {
            "connectivity-service": [bad]}})
        assert resp.status_code == 409
        assert _error(resp)["error-app-tag"] == "instance-required"
        assert sorted(_uuids(mesh)) == ["old-1", "old-2", "old-3"]


class TestPathComputation:
    def test_the_entry_is_created_then_replaced(self, mesh: TestClient) -> None:
        refs = MeshRefs(mesh.app.state.context)
        url = f"{_PC}/path-comp-service=p1"
        created = mesh.put(url, json={_PCS: [_pcs(refs, "p1", "trx A", "trx C")]})
        assert created.status_code == 201, created.text
        assert created.headers["location"] == url
        (entry,) = created.json()[_PCS]
        assert entry["uuid"] == "p1" and entry["path"]
        replaced = mesh.put(url, json={_PCS: [_pcs(refs, "p1", "trx B", "trx C")]})
        assert replaced.status_code == 204
        (now,) = mesh.get(url).json()[_PCS]
        assert now["end-point"][0]["service-interface-point"]["service-interface-point-uuid"] == (
            refs.sip("trx B"))
        paths = {p["uuid"] for p in mesh.get(f"{_PC}/path").json()["tapi-path-computation:path"]}
        assert paths == {ref["path-uuid"] for ref in now["path"]}  # the old ones are gone

    def test_a_refused_replacement_keeps_the_entry(self, mesh: TestClient) -> None:
        refs = MeshRefs(mesh.app.state.context)
        url = f"{_PC}/path-comp-service=p1"
        assert mesh.put(url, json={_PCS: [_pcs(refs, "p1", "trx A", "trx C")]}).status_code == 201
        before = mesh.get(_PC).json()
        bad = _pcs(refs, "p1", "trx A", "trx C")
        bad["end-point"][1]["service-interface-point"]["service-interface-point-uuid"] = "nope"
        resp = mesh.put(url, json={_PCS: [bad]})
        assert resp.status_code == 409
        assert mesh.get(_PC).json() == before

    def test_the_key_must_be_the_uri_s(self, mesh: TestClient) -> None:
        """RFC 8040 §4.5: the key in the body "MUST be the same as the key
        leaf values in the request URI"."""
        refs = MeshRefs(mesh.app.state.context)
        resp = mesh.put(f"{_PC}/path-comp-service=p1",
                        json={_PCS: [_pcs(refs, "p2", "trx A", "trx C")]})
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "invalid-value"
        assert mesh.get(f"{_PC}/path-comp-service").json() == {_PCS: []}

    def test_the_container(self, mesh: TestClient) -> None:
        refs = _populate(mesh)
        held = mesh.get(_PC).json()["tapi-path-computation:path-computation-context"]
        body = {"tapi-path-computation:path-computation-context": {
            "path-comp-service": [*held["path-comp-service"],
                                  _pcs(refs, "pcs-2", "trx B", "trx C")],
            "path": held["path"],  # config false: ignored
        }}
        assert mesh.put(_PC, json=body).status_code == 204
        after = mesh.get(_PC).json()["tapi-path-computation:path-computation-context"]
        assert [p["uuid"] for p in after["path-comp-service"]] == ["pcs-1", "pcs-2"]
        assert after["path-comp-service"][0] == held["path-comp-service"][0]  # kept
        resp = mesh.put(_PC, json={"tapi-path-computation:path-computation-context": {
            "path-set": [{"uuid": "s"}]}})
        assert resp.status_code == 501
        # The services are another list: untouched.
        assert sorted(_uuids(mesh)) == ["old-1", "old-2", "old-3"]


class TestContext:
    """The configuration the twin derives may be repeated unchanged, never
    changed or omitted (RFC 8040 §4.5 access-denied; RFC 8341 §3.4.5)."""

    def test_its_own_get_body_changes_nothing(self, mesh: TestClient) -> None:
        _populate(mesh)
        before = _everything(mesh)
        resp = mesh.put(_R, json=mesh.get(_R).json())
        assert resp.status_code == 204, resp.text
        assert _everything(mesh) == before

    def test_the_lists_are_replaced(self, mesh: TestClient) -> None:
        refs = _populate(mesh)
        context = mesh.get(_R).json()
        body = context["tapi-common:context"]
        body["tapi-connectivity:connectivity-context"]["connectivity-service"] = [
            _service(refs, "new-1", "trx A", "trx B")]
        body["tapi-path-computation:path-computation-context"] = {}
        assert mesh.put(_R, json=context).status_code == 204
        assert _uuids(mesh) == ["new-1"]
        assert mesh.get(f"{_PC}/path-comp-service").json() == {_PCS: []}

    @pytest.mark.parametrize("change", [
        "omit-topology", "change-link-name", "omit-uuid", "change-uuid", "add-name",
        "omit-oam-jobs",
    ])
    def test_derived_configuration_is_access_denied(self, app: TestClient, change: str) -> None:
        context = app.get(_R).json()
        body = context["tapi-common:context"]
        sips = [s["uuid"] for s in body["service-interface-point"]]
        created = app.post(_CC, json={_SVC: [{"uuid": "s1", "end-point": [
            tapi_end_point("a", sips[0]), tapi_end_point("z", sips[1])]}]})
        assert created.status_code == 201, created.text
        context = app.get(_R).json()
        body = context["tapi-common:context"]
        topology = body["tapi-topology:topology-context"]["topology"][0]
        if change == "omit-topology":
            del body["tapi-topology:topology-context"]
        elif change == "change-link-name":
            topology["link"][0]["name"] = [{"value-name": "link-name", "value": "renamed"}]
        elif change == "omit-uuid":
            del body["uuid"]
        elif change == "change-uuid":
            body["uuid"] = "another"
        elif change == "add-name":
            body["name"] = [{"value-name": "context-name", "value": "mine"}]
        elif change == "omit-oam-jobs":
            del body["tapi-oam:oam-context"]
        before = app.get(_R).json()
        resp = app.put(_R, json=context)
        assert resp.status_code == 403, resp.text
        assert _error(resp)["error-tag"] == "access-denied"
        assert _error(resp)["error-path"].startswith("/tapi-common:context/")
        assert app.get(_R).json() == before

    def test_state_in_the_body_is_ignored(self, app: TestClient) -> None:
        """The SIP list, states and the computed paths are config false:
        not part of the configuration a PUT replaces (RFC 7950 §7.21.1)."""
        context = app.get(_R).json()
        body = context["tapi-common:context"]
        body["service-interface-point"] = []
        body["tapi-topology:topology-context"]["topology"][0]["node"] = []
        assert app.put(_R, json=context).status_code == 204
        assert app.get(_R).json()["tapi-common:context"]["service-interface-point"]

    def test_unknown_members(self, app: TestClient) -> None:
        context = app.get(_R).json()
        context["tapi-common:context"]["tapi-notification:notification-context"] = {}
        resp = app.put(_R, json=context)
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "unknown-namespace"


class TestDatastore:
    def test_its_own_get_body_changes_nothing(self, app: TestClient) -> None:
        before = app.get("/restconf/data").json()
        resp = app.put("/restconf/data", json=before)
        assert resp.status_code == 204, resp.text
        assert app.get("/restconf/data").json() == before

    def test_it_replaces_the_service_list(self, app: TestClient) -> None:
        data = app.get("/restconf/data").json()
        context = data["ietf-restconf:data"]["tapi-common:context"]
        sips = [s["uuid"] for s in context["service-interface-point"]]
        context["tapi-connectivity:connectivity-context"]["connectivity-service"] = [
            {"uuid": "s1", "end-point": [tapi_end_point("a", sips[0]),
                                         tapi_end_point("z", sips[1])]}]
        del data["ietf-restconf:data"]["ietf-yang-library:yang-library"]  # state: optional
        assert app.put("/restconf/data", json=data).status_code == 204
        assert _uuids(app) == ["s1"]

    def test_without_the_context_is_access_denied(self, app: TestClient) -> None:
        resp = app.put("/restconf/data", json={"ietf-restconf:data": {}})
        assert resp.status_code == 403
        assert _error(resp)["error-tag"] == "access-denied"

    @pytest.mark.parametrize(("member", "tag"), [
        ("foo", "unknown-element"),
        ("ietf-interfaces:interfaces", "unknown-namespace"),
        ("ietf-yang-library:nope", "unknown-element"),
    ])
    def test_unknown_top_level_members(self, app: TestClient, member: str, tag: str) -> None:
        data = app.get("/restconf/data").json()
        data["ietf-restconf:data"][member] = {}
        resp = app.put("/restconf/data", json=data)
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == tag


@pytest.mark.parametrize("root", ["/restconf", "/api", ""])
def test_every_root(twin_config: TwinConfig, root: str) -> None:
    """The new routes are mounted under the root like every T-API route."""
    twin_config.server.restconf_root = root
    app = TestClient(create_app(twin_config))
    context = app.get(f"{root}/data/tapi-common:context").json()
    assert app.put(f"{root}/data/tapi-common:context", json=context).status_code == 204
    data = app.get(f"{root}/data").json()
    assert app.put(f"{root}/data", json=data).status_code == 204
    cc = context["tapi-common:context"]["tapi-connectivity:connectivity-context"]
    url = f"{root}/data/tapi-common:context/tapi-connectivity:connectivity-context"
    assert app.put(url, json={"tapi-connectivity:connectivity-context": cc}).status_code == 204
    assert "PUT" in app.options(url).headers["allow"]
