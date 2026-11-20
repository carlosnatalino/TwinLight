"""The memoised T-API equipment tree: never stale, never corruptible.

``TapiContext.physical_context()`` builds the tree once and hands the same
object to every equipment request and to the context GET. A cached answer
that is wrong still looks plausible, so this module pins three things:

- **What the tree is built from**: each element's UID, kind,
  ``type_variety`` and position (``metadata.location``), and nothing else.
  The only runtime write that can change any of them is a redesign, which
  re-runs GNPy's design and rebuilds the backend's ``uid_map``.
  ``TestARedesignIsFollowedAtOnce`` changes each of those inputs in a
  redesign and reads every equipment body back.
- **What invalidates it**: the running revision, which every configuration
  change through any interface bumps. ``TestEveryWriteLeavesTheMemoRight``
  drives each write plane the API offers, on both backends, and compares
  what is served with a tree built from scratch.
- **That no caller can change it**: frozen models and tuples, and a fresh
  ``dict`` for every body.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.api.equipment import physical_context_payload
from twinlight.app import create_app
from twinlight.models.equipment import PhysicalContext
from twinlight.models.yang_numbers import PHYSICAL_CONTEXT_PATH, encode_64bit_leaves

_CONTEXT = "/restconf/data/tapi-common:context"
_BASE = f"{_CONTEXT}/tapi-equipment:physical-context"
_CONNECTIVITY = f"{_CONTEXT}/tapi-connectivity:connectivity-context"
_PATH_COMPUTATION = f"{_CONTEXT}/tapi-path-computation:path-computation-context"
_SVC = "tapi-connectivity:connectivity-service"
_YANG_PATCH = {"Content-Type": "application/yang-patch+json"}


def _fresh(client: TestClient) -> dict[str, Any]:
    """The equipment tree built from the elements as they are right now,
    as a body carries it: geolocation leaves are decimal64 strings."""
    ctx = client.app.state.context
    # The class's method: a test may count the instance's builds.
    tree = type(ctx)._build_physical_context(ctx)
    body: dict[str, Any] = encode_64bit_leaves(
        tree.model_dump(by_alias=True, mode="json"), PHYSICAL_CONTEXT_PATH)
    return body


def _uid(entry: dict[str, Any]) -> str:
    return next(n["value"] for n in entry["name"] if n["value-name"] == "element-uid")


def _assert_served_fresh(client: TestClient) -> dict[str, Any]:
    """Every equipment body the twin serves equals a tree built from scratch."""
    fresh = _fresh(client)
    assert client.get(_BASE).json() == {"tapi-equipment:physical-context": fresh}
    assert client.get(f"{_BASE}/device").json() == {"tapi-equipment:device": fresh["device"]}
    assert client.get(f"{_BASE}/physical-span").json() == {
        "tapi-equipment:physical-span": fresh["physical-span"]}
    for device in fresh["device"]:
        url = f"{_BASE}/device={device['uuid']}"
        assert client.get(url).json() == {"tapi-equipment:device": [device]}
        for equipment in device["equipment"]:
            assert client.get(f"{url}/equipment={equipment['uuid']}").json() == {
                "tapi-equipment:equipment": [equipment]}
    for span in fresh["physical-span"]:
        assert client.get(f"{_BASE}/physical-span={span['uuid']}").json() == {
            "tapi-equipment:physical-span": [span]}
    context = client.get(_CONTEXT).json()["tapi-common:context"]
    assert context["tapi-equipment:physical-context"] == fresh
    datastore = client.get("/restconf/data").json()["ietf-restconf:data"]
    assert datastore["tapi-common:context"]["tapi-equipment:physical-context"] == fresh
    return fresh


class TestTheMemo:
    def test_reads_share_one_tree(self, gnpy_app: TestClient) -> None:
        ctx = gnpy_app.app.state.context
        first = ctx.physical_context()
        assert ctx.physical_context() is first
        # Equipment requests, the context GET and reads of other resources
        # build nothing: the tree is the same object afterwards.
        _assert_served_fresh(gnpy_app)
        assert gnpy_app.get("/internal/opm").status_code == 200
        assert ctx.physical_context() is first

    def test_it_is_built_once_however_many_requests(
        self, gnpy_app: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ctx = gnpy_app.app.state.context
        ctx._physical_context_cache = None
        builds = []
        build = ctx._build_physical_context
        monkeypatch.setattr(ctx, "_build_physical_context", lambda: builds.append(1) or build())
        device = _fresh(gnpy_app)["device"][0]
        for url in (_BASE, f"{_BASE}/device", f"{_BASE}/device={device['uuid']}",
                    f"{_BASE}/physical-span", _CONTEXT, "/restconf/data"):
            assert gnpy_app.get(url).status_code == 200
        assert builds == [1]

    def test_a_configuration_change_rebuilds_it_once(self, gnpy_app: TestClient) -> None:
        ctx = gnpy_app.app.state.context
        first = ctx.physical_context()
        fiber = next(uid for uid in ctx._backend.uid_map if ctx._backend.is_fiber(uid))
        r = gnpy_app.post("/config/set", json={"devices": {fiber: {"loss_coef": 0.21}}})
        assert r.status_code == 200, r.text
        second = ctx.physical_context()
        assert second is not first and second == first
        assert ctx.physical_context() is second


class TestNoCallerCanChangeIt:
    def test_every_model_is_frozen_and_every_list_a_tuple(self, gnpy_app: TestClient) -> None:
        tree = gnpy_app.app.state.context.physical_context()
        seen = 0

        def walk(node: Any) -> None:
            nonlocal seen
            assert not isinstance(node, (list, dict, set)), type(node)
            if isinstance(node, tuple):
                for item in node:
                    walk(item)
            elif isinstance(node, BaseModel):
                seen += 1
                assert node.model_config.get("frozen") is True, type(node)
                for name in type(node).model_fields:
                    walk(getattr(node, name))
            else:
                assert node is None or isinstance(node, (str, float)), type(node)

        walk(tree)
        assert seen > len(tree.device) + len(tree.physical_span)

    def test_assignment_is_refused_at_every_level(self, gnpy_app: TestClient) -> None:
        tree = gnpy_app.app.state.context.physical_context()
        device = tree.device[0]
        equipment = device.equipment[0]
        properties = equipment.actual_equipment.common_equipment_properties
        for target, field, value in [
            (tree, "device", ()),
            (tree, "physical_span", ()),
            (device, "uuid", "x"),
            (device, "equipment", ()),
            (device.name[0], "value", "x"),
            (equipment, "actual_equipment", None),
            (properties, "equipment_type_name", "x"),
            (equipment, "geolocation", None),
            (equipment.geolocation, "latitude", 0.0),
            (tree.physical_span[0], "uuid", "x"),
            (tree.physical_span[0].name[0], "value", "x"),
        ]:
            with pytest.raises(ValidationError, match="frozen"):
                setattr(target, field, value)

    def test_changing_a_body_does_not_change_the_next_one(self, gnpy_app: TestClient) -> None:
        ctx = gnpy_app.app.state.context
        fresh = _fresh(gnpy_app)
        body = physical_context_payload(ctx)
        # A body is plain JSON data: lists, not the tree's tuples.
        assert type(body["device"]) is list and type(body["device"][0]["equipment"]) is list
        assert type(body["device"][0]["name"]) is list
        body["device"][0]["equipment"][0]["actual-equipment"].clear()
        body["device"][0]["name"][0]["value"] = "changed"
        body["device"].clear()
        del body["physical-span"]
        assert physical_context_payload(ctx) == fresh
        _assert_served_fresh(gnpy_app)

    def test_the_tree_is_a_physical_context(self, gnpy_app: TestClient) -> None:
        tree = gnpy_app.app.state.context.physical_context()
        assert isinstance(tree, PhysicalContext)
        assert tree.device and tree.physical_span


def _config_set_redesign(client: TestClient) -> None:
    r = client.post("/config/set", json={"redesign": True})
    assert r.status_code == 200, r.text


def _transparent_redesign(client: TestClient) -> None:
    r = client.post("/transparent/operations/redesign")
    assert r.status_code == 200, r.text


class TestARedesignIsFollowedAtOnce:
    """The inputs of the tree, changed at runtime by the one write that can.

    GNPy's design of an already designed network reproduces the same
    inventory, so a stand-in plays a design that chose differently: another
    ``type_variety`` and another position for one amplifier, one element
    fewer, one more.
    """

    @pytest.mark.parametrize("trigger", [_config_set_redesign, _transparent_redesign])
    def test_every_equipment_body_follows(
        self, gnpy_app: TestClient, monkeypatch: pytest.MonkeyPatch, trigger: Any,
    ) -> None:
        backend = gnpy_app.app.state.context._backend
        amps = [uid for uid, el in backend.uid_map.items() if type(el).__name__ == "Edfa"]
        changed, removed = amps[0], amps[1]
        fiber = next(uid for uid in backend.uid_map if backend.is_fiber(uid))

        before = _assert_served_fresh(gnpy_app)  # also fills the memo
        devices = {_uid(d): d for d in before["device"]}
        old_variety = devices[changed]["equipment"][0]["actual-equipment"][
            "common-equipment-properties"]["equipment-type-identifier"]
        assert old_variety != "another_variety" and removed in devices
        assert "added amplifier" not in devices

        assert devices[changed]["equipment"][0]["geolocation"] != {
            "latitude": "45.5", "longitude": "-73.25"}

        def redesign() -> None:
            backend.uid_map[changed].type_variety = "another_variety"
            backend.uid_map[changed].metadata = {
                "location": {"latitude": 45.5, "longitude": -73.25}}
            added = type(backend.uid_map[changed]).__new__(type(backend.uid_map[changed]))
            added.uid, added.type_variety = "added amplifier", "std_low_gain"
            twin_fiber = type(backend.uid_map[fiber]).__new__(type(backend.uid_map[fiber]))
            twin_fiber.uid, twin_fiber.type_variety = "added fiber", "SSMF"
            backend.uid_map = {
                **{uid: el for uid, el in backend.uid_map.items() if uid != removed},
                "added amplifier": added, "added fiber": twin_fiber,
            }

        monkeypatch.setattr(backend, "redesign", redesign)
        trigger(gnpy_app)

        after = _assert_served_fresh(gnpy_app)
        devices_after = {_uid(d): d for d in after["device"]}
        assert devices_after[changed]["equipment"][0]["actual-equipment"][
            "common-equipment-properties"]["equipment-type-identifier"] == "another_variety"
        assert devices_after[changed]["equipment"][0]["geolocation"] == {
            "latitude": "45.5", "longitude": "-73.25"}
        assert removed not in devices_after
        assert gnpy_app.get(f"{_BASE}/device={devices[removed]['uuid']}").status_code == 404
        assert devices_after["added amplifier"]["equipment"][0]["actual-equipment"][
            "common-equipment-properties"] == {
                "equipment-type-name": "Edfa", "equipment-type-identifier": "std_low_gain"}
        # The stand-in has no metadata: no position, so no geolocation at all.
        assert "geolocation" not in devices_after["added amplifier"]["equipment"][0]
        assert "added fiber" in {_uid(s) for s in after["physical-span"]}
        assert len(after["device"]) == len(before["device"])
        assert len(after["physical-span"]) == len(before["physical-span"]) + 1

    def test_gnpys_own_redesign_reproduces_the_inventory(self, gnpy_app: TestClient) -> None:
        """What makes the memo cheap to keep: a real redesign changes nothing
        in the tree. If a GNPy release changes that, the bodies still follow
        (the test above); this only records the fact."""
        before = _assert_served_fresh(gnpy_app)
        _config_set_redesign(gnpy_app)
        assert _assert_served_fresh(gnpy_app) == before


@pytest.fixture(params=["gnpy", "egn"])
def backend_app(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    return TestClient(create_app(_mesh_config(tmp_path, request.param)))


class TestEveryWriteLeavesTheMemoRight:
    """Each write plane the API offers, then every equipment body.

    None of these writes changes an element's UID, kind or ``type_variety``,
    so the tree stays what it was; the test fails the day one of them does
    and the memo does not follow.
    """

    def test_the_whole_write_surface(self, backend_app: TestClient, tmp_path: Path) -> None:
        client = backend_app
        ctx = client.app.state.context
        backend = ctx._backend
        gnpy = backend.name == "gnpy"
        refs = MeshRefs(ctx)
        before = _assert_served_fresh(client)

        def check(response: Any, status: int) -> Any:
            assert response.status_code == status, response.text
            # The memo, and what a request is served from it, after the write.
            assert encode_64bit_leaves(
                ctx.physical_context().model_dump(by_alias=True, mode="json"),
                PHYSICAL_CONTEXT_PATH,
            ) == _fresh(client)
            assert client.get(_BASE).json() == {
                "tapi-equipment:physical-context": _fresh(client)}
            return response

        fiber = "fiber (A → B)"
        amp = "Edfa_booster_roadm A_to_fiber (A → B)"
        amp_attr, amp_value = ("gain_target", 17.0) if gnpy else ("nf0", 6.5)

        # -- T-API: services and path computation (no revision bump) ----------
        end_points = [tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]
        created = check(client.post(_CONNECTIVITY, json={_SVC: [{"end-point": end_points}]}), 201)
        service = created.json()[_SVC][0]["uuid"]
        url = f"{_CONNECTIVITY}/connectivity-service={service}"
        check(client.patch(url, json={_SVC: [{"name": [
            {"value-name": "service-name", "value": "renamed"}]}]}), 200)
        check(client.put(url, json={_SVC: [{"uuid": service, "end-point": end_points}]}), 204)
        bare = [{"local-id": ep["local-id"], "service-interface-point": ep["service-interface-point"]}
                for ep in end_points]
        pcs = check(client.post(_PATH_COMPUTATION, json={
            "tapi-path-computation:path-comp-service": [{"end-point": bare}]}), 201)
        pcs_uuid = pcs.json()["tapi-path-computation:path-comp-service"][0]["uuid"]
        check(client.post("/internal/compute-path", json={"end-point": bare}), 200)
        check(client.get("/internal/opm"), 200)

        # -- /config: element parameters, twin config, a link failure ---------
        check(client.post("/config/set", json={"devices": {fiber: {"loss_coef": 0.23}}}), 200)
        check(client.post("/config/set", json={"devices": {amp: {amp_attr: amp_value}}}), 200)
        check(client.post("/config/set", json={"twin": {"rmsa": {"qot_margin_db": 2.0}}}), 200)
        check(client.post("/config/set", json={"devices": {fiber: {"failed": True}}}), 200)
        check(client.post("/config/set", json={"devices": {fiber: {"failed": False}}}), 200)
        check(client.post("/config/parameters", json={"set": {"/Fiber/*/loss_coef": 0.22}}), 200)
        check(client.post("/config/parameters/changes", json=[
            {"path": "/Fiber/*/loss_coef", "by_percent": 5}]), 200)
        if gnpy:
            check(client.post("/config/set", json={"redesign": True}), 200)
        else:
            check(client.post("/config/set", json={"redesign": True}), 501)

        # -- /transparent: running, candidate, YANG Patch, operations ---------
        data, candidate = "/transparent/data", "/transparent/ds/candidate"
        leaf = f"/ElementList/{fiber}/loss_coef"
        check(client.patch(f"{data}{leaf}", json=0.24), 204)
        check(client.put(f"{data}/ElementList/{fiber}", json={"loss_coef": 0.25}), 204)
        check(client.patch(data, content=json.dumps({"ietf-yang-patch:yang-patch": {
            "patch-id": "p", "edit": [
                {"edit-id": "1", "operation": "merge", "target": leaf, "value": 0.26},
                {"edit-id": "2", "operation": "merge",
                 "target": f"/ElementList/{fiber}/failed", "value": True},
            ]}}), headers=_YANG_PATCH), 200)
        check(client.delete(f"{data}/ElementList/{fiber}/failed"), 204)
        check(client.post(f"{data}{leaf}", json=0.2), 405)
        check(client.patch(f"{candidate}{leaf}", json=0.27), 204)
        check(client.patch(f"{candidate}/Config/rmsa/qot_margin_db", json=2.5), 204)
        check(client.post("/transparent/operations/validate",
                          json={"input": {"with-qot-impact": True}}), 200)
        check(client.post("/transparent/operations/compare"), 200)
        check(client.post("/transparent/operations/commit"), 200)
        check(client.put(f"{candidate}/ElementList/{fiber}", json={"loss_coef": 0.28}), 204)
        check(client.delete(f"{candidate}{leaf}"), 204)
        check(client.post("/transparent/operations/discard-changes"), 200)
        check(client.post("/transparent/operations/redesign"), 200 if gnpy else 501)

        # -- /admin: a snapshot of all that, everything reverted, restored ----
        snapshot = tmp_path / "state.json"
        check(client.post("/admin/snapshot", json={"path": str(snapshot)}), 200)
        check(client.delete(data), 204)
        check(client.delete(f"{_PATH_COMPUTATION}/path-comp-service={pcs_uuid}"), 204)
        check(client.delete(url), 204)
        check(client.post("/admin/restore", json={"path": str(snapshot)}), 200)
        assert ctx.get_element_overrides()[fiber]["loss_coef"] == 0.27  # the commit

        # Nothing a client wrote reached the tree, and every body says so.
        assert _assert_served_fresh(client) == before
