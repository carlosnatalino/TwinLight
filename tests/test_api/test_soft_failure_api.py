"""HTTP tests for /config/parameters and /config/soft-failures."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_api.test_config_api import _create_service_via_api
from twinlight.app import create_app
from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig

FIXTURES = Path(__file__).parent.parent / "fixtures"
FWD_FIBER = "fiber (Brest → Morlaix)-F"
BASE = "/config/soft-failures"


def _loss(client: TestClient, uid: str = FWD_FIBER) -> float:
    return float(client.get(f"/config/devices/{uid}").json()["attributes"]["loss_coef"])


class TestParameters:
    def test_default_lists_everything(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get("/config/parameters")
        assert r.status_code == 200, r.text
        params = r.json()["parameters"]
        assert {p["type"] for p in params} == {"Fiber", "Edfa", "Roadm"}
        fiber = next(p for p in params if p["attribute"] == "loss_coef")
        assert fiber["unit"] == "dB/km" and fiber["max"] == 2.0
        assert fiber["value"] == pytest.approx(0.2)

    def test_path_is_parsed_and_echoed(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get(
            "/config/parameters",
            params={"path": "/Link/roadm Brest/roadm Morlaix/*/*/loss_coef"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["path"]["link"] == {"from": "roadm Brest", "to": "roadm Morlaix"}
        assert [p["uid"] for p in body["parameters"]] == [FWD_FIBER]

    def test_bad_path_400(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get("/config/parameters", params={"path": "/Fiber/*"})
        assert r.status_code == 400
        assert "ietf-restconf:errors" in r.json()

    def test_503_without_backend(self, app: TestClient) -> None:
        assert app.get("/config/parameters").status_code == 503
        assert app.get(BASE).status_code == 503


class TestLifecycle:
    def test_create_get_list_stop_delete(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(BASE, json={
            "name": "dirty connector",
            "path": f'/Fiber/"{FWD_FIBER}"/loss_coef',
            "by": 0.05,
            "duration_s": 3600,
        })
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["id"] == "sf-1"
        assert job["state"] == "running"
        assert job["targets"][0]["end"] == pytest.approx(0.25)
        assert job["spec"]["profile"] == "linear"

        assert gnpy_app.get(f"{BASE}/sf-1").json()["name"] == "dirty connector"
        assert [j["id"] for j in gnpy_app.get(BASE).json()["soft-failures"]] == ["sf-1"]

        assert gnpy_app.post(f"{BASE}/sf-1/pause").json()["state"] == "paused"
        assert gnpy_app.post(f"{BASE}/sf-1/pause").status_code == 409
        assert gnpy_app.post(f"{BASE}/sf-1/resume").json()["state"] == "running"
        assert gnpy_app.post(f"{BASE}/sf-1/stop").json()["state"] == "stopped"

        r = gnpy_app.delete(f"{BASE}/sf-1", params={"revert": True})
        assert r.status_code == 200
        assert _loss(gnpy_app) == pytest.approx(0.2)
        assert gnpy_app.get(f"{BASE}/sf-1").status_code == 404

    def test_dry_run_does_not_register(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.post(
            BASE, params={"dry_run": True},
            json={"path": "/Fiber/*/loss_coef", "to": 0.3, "rate": 0.001},
        )
        assert r.status_code == 200, r.text
        plan = r.json()
        assert plan["id"] is None
        assert plan["state"] == "planned"
        assert plan["progress"] == 0.0 and plan["elapsed_s"] == 0.0
        assert plan["remaining_s"] == pytest.approx(100.0)
        assert len(plan["targets"]) == 2
        assert plan["duration_s"] == pytest.approx(100.0)
        assert gnpy_app.get(BASE).json()["soft-failures"] == []

    def test_reports_affected_services(self, gnpy_app: TestClient) -> None:
        svc = _create_service_via_api(gnpy_app)
        r = gnpy_app.post(BASE, json={
            "path": f"/Service/{svc}/Fiber/*/loss_coef",
            "by": 0.1, "duration_s": 60,
        })
        assert r.status_code == 201, r.text
        assert r.json()["affected_services"] == [svc]

    @pytest.mark.parametrize(("body", "code"), [
        ({"path": "/Fiber/*/loss_coef", "duration_s": 1}, 422),
        ({"path": "/Fiber/*/loss_coef", "by": 1, "duration_s": 1, "x": 1}, 422),
        ({"path": "/Fiber/*/loss_coef", "to": 9.0, "duration_s": 1}, 400),
        ({"path": "/Edfa/*/loss_coef", "by": 1, "duration_s": 1}, 400),
        ({"path": "/Fiber/nope/loss_coef", "by": 1, "duration_s": 1}, 400),
    ])
    def test_validation_errors(
        self, gnpy_app: TestClient, body: dict, code: int,
    ) -> None:
        r = gnpy_app.post(BASE, json=body)
        assert r.status_code == code, r.text
        assert "ietf-restconf:errors" in r.json()

    def test_conflict_409(self, gnpy_app: TestClient) -> None:
        body = {"path": "/Fiber/*/loss_coef", "by": 0.1, "duration_s": 60}
        assert gnpy_app.post(BASE, json=body).status_code == 201
        assert gnpy_app.post(BASE, json=body).status_code == 409

    def test_unknown_job_404(self, gnpy_app: TestClient) -> None:
        assert gnpy_app.get(f"{BASE}/sf-99").status_code == 404
        assert gnpy_app.post(f"{BASE}/sf-99/stop").status_code == 404
        assert gnpy_app.delete(f"{BASE}/sf-99").status_code == 404


class TestBackgroundRunner:
    def test_lifespan_runner_drives_job_to_completion(
        self, gnpy_twin_config: TwinConfig,
    ) -> None:
        """The one wall-clock test: the app lifespan really runs the engine.

        Entering the TestClient context runs the lifespan, which starts the
        runner task on the client's event loop. Kept short; the deadline is
        generous so a slow CI runner cannot make it flaky.
        """
        with TestClient(create_app(gnpy_twin_config)) as client:
            r = client.post(BASE, json={
                "path": f'/Fiber/"{FWD_FIBER}"/loss_coef',
                "by": 0.1, "duration_s": 0.3, "interval_s": 0.1,
            })
            assert r.status_code == 201, r.text
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if client.get(f"{BASE}/sf-1").json()["state"] == "completed":
                    break
                time.sleep(0.05)
            job = client.get(f"{BASE}/sf-1").json()
            assert job["state"] == "completed"
            assert job["progress"] == 1.0
            assert _loss(client) == pytest.approx(0.3)


class TestEgnBackend:
    def test_shared_knobs_drive_under_egn(self) -> None:
        cfg = TwinConfig(
            gnpy=GnpyConfig(topology=FIXTURES / "edfa_example_network.json"),
            physics=PhysicsConfig(backend="egn"),
        )
        client = TestClient(create_app(cfg))
        params = client.get("/config/parameters").json()["parameters"]
        assert {p["attribute"] for p in params} <= {"loss_coef", "att_in", "nf0"}
        # GNPy-only knob: the EGN schema has no gain_target.
        r = client.post(BASE, json={
            "path": "/Edfa/*/gain_target", "by": 1, "duration_s": 1,
        })
        assert r.status_code == 400
        r = client.post(BASE, json={
            "path": "/Fiber/*/loss_coef", "by": 0.05, "duration_s": 60,
        })
        assert r.status_code == 201, r.text
