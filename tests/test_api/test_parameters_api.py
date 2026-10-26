"""HTTP tests for /config/parameters and /config/parameters/changes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_api.test_config_api import _create_service_via_api
from twinlight.app import create_app
from twinlight.config import GnpyConfig, PhysicsConfig, TransientsConfig, TwinConfig

FIXTURES = Path(__file__).parent.parent / "fixtures"
FWD_FIBER = "fiber (Brest → Morlaix)-F"
EAST_AMP = "east edfa in Brest to Morlaix"
SET = "/config/parameters"
CHANGES = "/config/parameters/changes"


def _attr(client: TestClient, uid: str, attr: str) -> float:
    return float(client.get(f"/config/devices/{uid}").json()["attributes"][attr])


class TestGetParameters:
    def test_default_lists_everything(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get(SET)
        assert r.status_code == 200, r.text
        params = r.json()["parameters"]
        assert {p["type"] for p in params} == {"Fiber", "Edfa", "Roadm"}
        fiber = next(p for p in params if p["attribute"] == "loss_coef")
        assert fiber["unit"] == "dB/km" and fiber["max"] == 2.0
        assert fiber["value"] == pytest.approx(0.2)

    def test_path_is_parsed_and_echoed(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get(
            SET, params={"path": "/Link/roadm Brest/roadm Morlaix/*/*/loss_coef"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["path"]["link"] == {"from": "roadm Brest", "to": "roadm Morlaix"}
        assert [p["uid"] for p in body["parameters"]] == [FWD_FIBER]

    def test_bad_path_400(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get(SET, params={"path": "/Fiber/*"})
        assert r.status_code == 400
        assert "ietf-restconf:errors" in r.json()

    def test_503_without_backend(self, app: TestClient) -> None:
        assert app.get(SET).status_code == 503
        assert app.post(SET, json={"set": {"/Fiber/*/loss_coef": 0.3}}).status_code == 503
        assert app.post(CHANGES, json=[{"path": "/Fiber/*/loss_coef", "by": 1}]).status_code == 503


class TestSet:
    def test_sets_many_parameters_in_one_call(self, gnpy_app: TestClient) -> None:
        svc = _create_service_via_api(gnpy_app)
        r = gnpy_app.post(SET, json={"set": {
            "/Fiber/*/loss_coef": 0.25,
            f'/Edfa/"{EAST_AMP}"/tilt_target': 0.5,
        }})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] is True
        assert len(body["parameters"]) == 3
        assert body["invalidated_services"] == [svc]
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.25)
        assert _attr(gnpy_app, EAST_AMP, "tilt_target") == 0.5
        # Recorded as overrides, exactly like POST /config/set.
        overrides = gnpy_app.get("/config/get").json()["element_overrides"]
        assert overrides[FWD_FIBER] == {"loss_coef": 0.25}

    def test_dry_run_writes_nothing(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(SET, params={"dry_run": True},
                          json={"set": {"/Fiber/*/loss_coef": 0.25}})
        assert r.status_code == 200
        assert r.json()["applied"] is False
        assert r.json()["parameters"][0]["previous"] == pytest.approx(0.2)
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.2)

    def test_one_bad_value_refuses_the_whole_batch(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(SET, json={"set": {
            f'/Edfa/"{EAST_AMP}"/tilt_target': 0.5,
            "/Fiber/*/loss_coef": 9.0,
        }})
        assert r.status_code == 400
        assert "outside" in r.json()["ietf-restconf:errors"]["error"][0]["error-message"]
        assert _attr(gnpy_app, EAST_AMP, "tilt_target") == 0.0

    @pytest.mark.parametrize("body", [
        {"set": {}},
        {"set": []},
        {"values": {"/Fiber/*/loss_coef": 0.3}},
        {"set": {"/Fiber/*/loss_coef": 0.3}, "extra": 1},
        {"set": {"/Fiber/*/loss_coef": True}},
    ])
    def test_malformed_body_400(self, gnpy_app: TestClient, body: dict) -> None:
        assert gnpy_app.post(SET, json=body).status_code == 400


class TestChanges:
    def test_list_of_changes_applies_together(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(CHANGES, json=[
            {"path": "/Fiber/*/loss_coef", "by_percent": 50},
            {"path": f'/Edfa/"{EAST_AMP}"/gain_target', "by": 2, "direction": "decrease"},
        ])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] is True
        assert [len(c["parameters"]) for c in body["changes"]] == [2, 1]
        amp = body["changes"][1]["parameters"][0]
        assert amp["value"] == pytest.approx(amp["previous"] - 2)
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.3)

    def test_dry_run_is_the_plan(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(CHANGES, params={"dry_run": True},
                          json=[{"path": "/Fiber/*/loss_coef", "to": 0.3}])
        assert r.status_code == 200
        [change] = r.json()["changes"]
        assert change["path"]["canonical"] == "/Fiber/*/loss_coef"
        assert change["change"] == {
            "path": "/Fiber/*/loss_coef", "to": 0.3, "out_of_bounds": "reject",
        }
        assert {(p["previous"], p["value"]) for p in change["parameters"]} == {(0.2, 0.3)}
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.2)

    @pytest.mark.parametrize(("body", "code"), [
        ([{"path": "/Fiber/*/loss_coef"}], 422),
        ([{"path": "/Fiber/*/loss_coef", "by": 1, "duration_s": 1}], 422),
        ({"path": "/Fiber/*/loss_coef", "by": 1}, 422),
        ([], 400),
        ([{"path": "/Fiber/*/loss_coef", "to": 9.0}], 400),
        ([{"path": "/Edfa/*/loss_coef", "by": 1}], 400),
        ([{"path": "/Fiber/*/loss_coef", "by": 0.1},
          {"path": f'/Fiber/"{FWD_FIBER}"/loss_coef', "by": 0.1}], 400),
    ])
    def test_validation_errors(self, gnpy_app: TestClient, body: object, code: int) -> None:
        r = gnpy_app.post(CHANGES, json=body)
        assert r.status_code == code, r.text
        assert "ietf-restconf:errors" in r.json()
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.2)


class TestMonitoringFollowsChanges:
    """A write is visible in OPM on the very next read, and only where it should be.

    Transients are off so OPM equals the baseline and a delta is exact.
    """

    WEST_AMP = "west edfa in Morlaix to Brest"

    @pytest.fixture
    def quiet_app(self, gnpy_twin_config: TwinConfig) -> TestClient:
        off = {"enabled": False}
        cfg = gnpy_twin_config.model_copy(update={"transients": TransientsConfig(
            edfa_reservoir=off, polarization=off, phase_noise=off, environmental=off,
        )})
        return TestClient(create_app(cfg))

    @staticmethod
    def _gsnr(client: TestClient, svc: str) -> float:
        return float(client.get(f"/internal/opm/{svc}").json()["measurements"]["gsnr-db"])

    def test_nf_offset_on_a_traversed_amp(self, quiet_app: TestClient) -> None:
        svc = _create_service_via_api(quiet_app)
        before = self._gsnr(quiet_app, svc)
        r = quiet_app.post(SET, json={"set": {f'/Edfa/"{EAST_AMP}"/nf_offset': 6.0}})
        assert r.json()["invalidated_services"] == [svc]
        assert self._gsnr(quiet_app, svc) < before - 1.0
        quiet_app.post(CHANGES, json=[
            {"path": f'/Edfa/"{EAST_AMP}"/nf_offset', "by": 6, "direction": "decrease"},
        ])
        assert self._gsnr(quiet_app, svc) == pytest.approx(before, abs=1e-9)

    def test_amp_off_the_path_changes_nothing(self, quiet_app: TestClient) -> None:
        svc = _create_service_via_api(quiet_app)
        before = self._gsnr(quiet_app, svc)
        r = quiet_app.post(SET, json={"set": {
            f'/Edfa/"{self.WEST_AMP}"/nf_offset': 6.0,
            f'/Edfa/"{self.WEST_AMP}"/gain_target': 20.0,
        }})
        assert r.status_code == 200, r.text
        assert r.json()["invalidated_services"] == []
        assert self._gsnr(quiet_app, svc) == before

    def test_listed_values_written_back_change_nothing(
        self, quiet_app: TestClient,
    ) -> None:
        """The listing reports what the physics uses, for every parameter.

        A parameter read from a different field than propagation uses (as
        EDFA gain_target once was) fails here: writing its listed value back
        would move the service.
        """
        svc = _create_service_via_api(quiet_app)
        before = self._gsnr(quiet_app, svc)
        listed = quiet_app.get(SET, params={"path": f"/Service/{svc}/*/*/*"}).json()
        values = {p["path"]: p["value"] for p in listed["parameters"]
                  if p["value"] is not None}
        assert {p.split("/")[-1] for p in values} >= {
            "gain_target", "tilt_target", "out_voa", "nf_offset", "loss_coef",
        }
        r = quiet_app.post(SET, json={"set": values})
        assert r.status_code == 200, r.text
        assert r.json()["invalidated_services"] == [svc]
        assert self._gsnr(quiet_app, svc) == pytest.approx(before, abs=1e-9)


class TestEgnBackend:
    def test_shared_knobs_under_egn(self) -> None:
        cfg = TwinConfig(
            gnpy=GnpyConfig(topology=FIXTURES / "edfa_example_network.json"),
            physics=PhysicsConfig(backend="egn"),
        )
        client = TestClient(create_app(cfg))
        params = client.get(SET).json()["parameters"]
        assert {p["attribute"] for p in params} <= {"loss_coef", "att_in", "nf0"}
        # GNPy-only knob: the EGN schema has no gain_target.
        r = client.post(CHANGES, json=[{"path": "/Edfa/*/gain_target", "by": 1}])
        assert r.status_code == 400
        r = client.post(SET, json={"set": {"/Fiber/*/loss_coef": 0.25}})
        assert r.status_code == 200, r.text
        assert r.json()["applied"] is True
