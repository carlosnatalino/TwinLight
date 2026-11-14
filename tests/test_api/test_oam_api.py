"""tapi-oam: provider-managed OAM jobs, one per admitted connectivity service."""

from __future__ import annotations

import json
import re
import uuid as uuid_lib
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.models.oam import oam_job_uuid

_CONN = "/data/tapi-connectivity:connectivity-context/connectivity-service"
_OAM = "/data/tapi-common:context/tapi-oam:oam-context"

# Every leaf of tapi-oam:oam-context/oam-job in the v2.6.0 YANG
# (TAPI v2.6.0 YANG/tapi-oam.tree). A key outside this set would be a
# non-standard field in a /data/ response.
OAM_JOB_YANG_KEYS = {
    "oam-job-type", "oam-job-state", "creation-time", "schedule", "results",
    "oam-service-point", "connection-end-point", "connectivity-service-end-point",
    "profile", "pm-data", "current-data", "uuid", "name",
    "administrative-state", "operational-state", "lifecycle-state",
}
CSEP_REF_YANG_KEYS = {
    "connectivity-service-uuid", "connectivity-service-end-point-local-id",
}
# tapi-common:date-and-time: yyyyMMddhhmmss.s followed by Z for UTC.
TAPI_DATE_AND_TIME = re.compile(r"^\d{14}\.\dZ$")


def _sips(app: TestClient) -> list[str]:
    data = app.get("/data/tapi-common:context/service-interface-point").json()
    return [s["uuid"] for s in data["tapi-common:context"]["service-interface-point"]]


def create_service(app: TestClient, sip_a: str, sip_z: str) -> str:
    resp = app.post(_CONN, json={"tapi-connectivity:connectivity-service": {
        "end-point": [tapi_end_point("a-end", sip_a), tapi_end_point("z-end", sip_z)],
    }})
    assert resp.status_code == 201, resp.text
    return resp.json()["tapi-connectivity:connectivity-service"]["uuid"]


def _jobs(app: TestClient) -> list[dict]:
    return app.get(f"{_OAM}/oam-job").json()["tapi-oam:oam-job"]


class TestLifecycle:
    def test_no_services_no_jobs(self, app: TestClient) -> None:
        assert _jobs(app) == []
        assert app.get(_OAM).json() == {"tapi-oam:oam-context": {}}

    def test_admission_creates_one_job_on_the_receiving_end_point(
        self, app: TestClient
    ) -> None:
        sip_a, sip_z = _sips(app)[:2]
        svc_uuid = create_service(app, sip_a, sip_z)
        [job] = _jobs(app)
        assert job["uuid"] == oam_job_uuid(svc_uuid)
        assert job["connectivity-service-end-point"] == {
            "connectivity-service-uuid": svc_uuid,
            "connectivity-service-end-point-local-id": "z-end",
        }
        assert job["oam-job-state"] == "tapi-oam:OAM_JOB_STATE_ACTIVE"
        assert TAPI_DATE_AND_TIME.match(job["creation-time"])

    def test_job_uuid_is_name_based(self, app: TestClient) -> None:
        sip_a, sip_z = _sips(app)[:2]
        svc_uuid = create_service(app, sip_a, sip_z)
        job_uuid = uuid_lib.UUID(_jobs(app)[0]["uuid"])
        assert job_uuid.version == 5
        assert str(job_uuid) == oam_job_uuid(svc_uuid)

    def test_deletion_removes_the_job(self, app: TestClient) -> None:
        sips = _sips(app)
        keep = create_service(app, sips[0], sips[1])
        drop = create_service(app, sips[0], sips[1])
        assert app.delete(f"{_CONN}={drop}").status_code == 204
        assert [j["uuid"] for j in _jobs(app)] == [oam_job_uuid(keep)]
        assert app.get(f"{_OAM}/oam-job={oam_job_uuid(drop)}").status_code == 404

    def test_get_single_job(self, app: TestClient) -> None:
        sip_a, sip_z = _sips(app)[:2]
        job_uuid = oam_job_uuid(create_service(app, sip_a, sip_z))
        body = app.get(f"{_OAM}/oam-job={job_uuid}").json()
        assert [j["uuid"] for j in body["tapi-oam:oam-job"]] == [job_uuid]

    def test_replace_keeps_the_job_uuid(self, app: TestClient) -> None:
        sip_a, sip_z = _sips(app)[:2]
        svc_uuid = create_service(app, sip_a, sip_z)
        resp = app.put(f"{_CONN}={svc_uuid}", json={
            "tapi-connectivity:connectivity-service": {
                "uuid": svc_uuid,
                "end-point": [
                    tapi_end_point("a", sip_a), tapi_end_point("b", sip_z),
                ],
            }
        })
        assert resp.status_code == 200, resp.text
        [job] = _jobs(app)
        assert job["uuid"] == oam_job_uuid(svc_uuid)
        assert job["connectivity-service-end-point"][
            "connectivity-service-end-point-local-id"
        ] == "b"


class TestReadOnly:
    def test_clients_cannot_create_or_delete_jobs(self, app: TestClient) -> None:
        sip_a, sip_z = _sips(app)[:2]
        job_uuid = oam_job_uuid(create_service(app, sip_a, sip_z))
        assert app.post(f"{_OAM}/oam-job", json={}).status_code == 405
        assert app.delete(f"{_OAM}/oam-job={job_uuid}").status_code == 405
        assert app.put(f"{_OAM}/oam-job={job_uuid}", json={}).status_code == 405
        assert len(_jobs(app)) == 1


class TestYangConformance:
    def test_only_yang_keys(self, app: TestClient) -> None:
        sip_a, sip_z = _sips(app)[:2]
        create_service(app, sip_a, sip_z)
        [job] = _jobs(app)
        assert set(job) <= OAM_JOB_YANG_KEYS
        assert set(job["connectivity-service-end-point"]) == CSEP_REF_YANG_KEYS

    def test_oam_job_type_is_omitted(self, app: TestClient) -> None:
        """OPTICAL_POWER, the only photonic type, would mislabel QoT records."""
        sip_a, sip_z = _sips(app)[:2]
        create_service(app, sip_a, sip_z)
        assert "oam-job-type" not in _jobs(app)[0]

    def test_full_context_and_restconf_root(self, app: TestClient) -> None:
        sip_a, sip_z = _sips(app)[:2]
        svc_uuid = create_service(app, sip_a, sip_z)
        ctx = app.get("/data/tapi-common:context").json()["tapi-common:context"]
        assert [j["uuid"] for j in ctx["tapi-oam:oam-context"]["oam-job"]] == [
            oam_job_uuid(svc_uuid)
        ]
        rooted = app.get(f"/restconf{_OAM}/oam-job").json()
        assert rooted == {"tapi-oam:oam-job": _jobs(app)}


class TestSnapshotRestore:
    def test_round_trip_keeps_creation_time(
        self, app: TestClient, tmp_path: Path
    ) -> None:
        sip_a, sip_z = _sips(app)[:2]
        create_service(app, sip_a, sip_z)
        before = _jobs(app)
        snap = tmp_path / "snap.json"
        app.post("/admin/snapshot", json={"path": str(snap)})
        assert json.loads(snap.read_text())["oam_jobs"] == before

        ctx = app.app.state.context
        ctx._oam_jobs.clear()
        assert app.post("/admin/restore", json={"path": str(snap)}).status_code == 200
        assert _jobs(app) == before

    def test_restore_replaces_existing_jobs(
        self, app: TestClient, tmp_path: Path
    ) -> None:
        sips = _sips(app)
        create_service(app, sips[0], sips[1])
        snap = tmp_path / "snap.json"
        app.post("/admin/snapshot", json={"path": str(snap)})
        before = _jobs(app)
        create_service(app, sips[0], sips[1])
        assert len(_jobs(app)) == 2
        app.post("/admin/restore", json={"path": str(snap)})
        assert _jobs(app) == before

    def test_old_snapshot_without_jobs_gets_fresh_ones(
        self, app: TestClient, tmp_path: Path
    ) -> None:
        sip_a, sip_z = _sips(app)[:2]
        svc_uuid = create_service(app, sip_a, sip_z)
        snap = tmp_path / "snap.json"
        app.post("/admin/snapshot", json={"path": str(snap)})
        data = json.loads(snap.read_text())
        del data["oam_jobs"]
        snap.write_text(json.dumps(data))
        app.post("/admin/restore", json={"path": str(snap)})
        assert [j["uuid"] for j in _jobs(app)] == [oam_job_uuid(svc_uuid)]

    def test_job_for_a_missing_service_is_dropped(
        self, app: TestClient, tmp_path: Path
    ) -> None:
        sip_a, sip_z = _sips(app)[:2]
        create_service(app, sip_a, sip_z)
        snap = tmp_path / "snap.json"
        app.post("/admin/snapshot", json={"path": str(snap)})
        data = json.loads(snap.read_text())
        data["services"] = []
        snap.write_text(json.dumps(data))
        app.post("/admin/restore", json={"path": str(snap)})
        assert _jobs(app) == []
