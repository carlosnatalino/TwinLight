"""T-API v2.6.0 physical inventory: physical-context, device, equipment, span."""

from __future__ import annotations

from fastapi.testclient import TestClient

_BASE = "/data/tapi-equipment:physical-context"
_DEVICE_KEYS = {"uuid", "name", "equipment"}
_EQUIPMENT_KEYS = {"uuid", "name", "actual-equipment"}


def _uid(obj: dict) -> str:
    return next(n["value"] for n in obj["name"] if n["value-name"] == "element-uid")


class TestPhysicalContext:
    def test_shape_uses_only_v2_6_leaves(self, app: TestClient) -> None:
        resp = app.get(_BASE)
        assert resp.status_code == 200
        context = resp.json()["tapi-equipment:physical-context"]
        assert set(context) == {"device", "physical-span"}
        for device in context["device"]:
            assert set(device) == _DEVICE_KEYS
            (equipment,) = device["equipment"]
            assert set(equipment) <= _EQUIPMENT_KEYS
            props = equipment["actual-equipment"]["common-equipment-properties"]
            assert set(props) <= {"equipment-type-name", "equipment-type-identifier"}
            assert props["equipment-type-name"] in {"Transceiver", "Roadm", "Edfa"}
        for span in context["physical-span"]:
            assert set(span) == {"uuid", "name"}

    def test_fibers_are_spans_and_boxes_are_devices(self, mesh_app: TestClient) -> None:
        context = mesh_app.get(_BASE).json()["tapi-equipment:physical-context"]
        spans = {_uid(s) for s in context["physical-span"]}
        devices = {_uid(d): d for d in context["device"]}
        assert "fiber (A → B)" in spans
        assert "roadm A" in devices and "trx A" in devices
        # GNPy's designed network adds boosters; they are inventory too.
        booster = devices["Edfa_booster_roadm A_to_fiber (A → B)"]
        props = booster["equipment"][0]["actual-equipment"]["common-equipment-properties"]
        assert props["equipment-type-name"] == "Edfa"
        assert props["equipment-type-identifier"]  # the GNPy type_variety

    def test_old_equipment_context_is_gone(self, app: TestClient) -> None:
        assert app.get("/data/tapi-equipment:equipment-context").status_code == 404


class TestLookups:
    def test_device_equipment_and_span(self, app: TestClient) -> None:
        context = app.get(_BASE).json()["tapi-equipment:physical-context"]
        device = context["device"][0]
        equipment = device["equipment"][0]
        got = app.get(f"{_BASE}/device={device['uuid']}").json()["tapi-equipment:device"]
        assert got == device
        got_eq = app.get(
            f"{_BASE}/device={device['uuid']}/equipment={equipment['uuid']}"
        ).json()["tapi-equipment:equipment"]
        assert got_eq == equipment
        span = context["physical-span"][0]
        assert app.get(f"{_BASE}/physical-span={span['uuid']}").json()[
            "tapi-equipment:physical-span"] == span
        assert app.get(f"{_BASE}/device").json()["tapi-equipment:device"] == context["device"]
        assert app.get(f"{_BASE}/physical-span").json()[
            "tapi-equipment:physical-span"] == context["physical-span"]

    def test_unknown_uuids_are_404(self, app: TestClient) -> None:
        device = app.get(f"{_BASE}/device").json()["tapi-equipment:device"][0]
        assert app.get(f"{_BASE}/device=nope").status_code == 404
        assert app.get(f"{_BASE}/device={device['uuid']}/equipment=nope").status_code == 404
        assert app.get(f"{_BASE}/device=nope/equipment=nope").status_code == 404
        assert app.get(f"{_BASE}/physical-span=nope").status_code == 404

    def test_uuids_are_stable_across_restarts(self, twin_config) -> None:
        from twinlight.app import create_app

        first = TestClient(create_app(twin_config)).get(_BASE).json()
        second = TestClient(create_app(twin_config)).get(_BASE).json()
        assert first == second
