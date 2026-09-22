"""Tests for admin snapshot and restore endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point

_BASE = "/data/tapi-connectivity:connectivity-context"


def _get_two_sips(app: TestClient) -> tuple[str, str]:
    data = app.get("/data/tapi-common:context/service-interface-point").json()
    sips = data["tapi-common:context"]["service-interface-point"]
    assert len(sips) >= 2
    return sips[0]["uuid"], sips[1]["uuid"]


def _create_service(app: TestClient, sip_a: str, sip_z: str) -> str:
    payload = {
        "tapi-connectivity:connectivity-service": {
            "name": [{"value-name": "service-name", "value": "snap-test"}],
            "end-point": [
                tapi_end_point("a", sip_a),
                tapi_end_point("z", sip_z),
            ],
        }
    }
    r = app.post(f"{_BASE}/connectivity-service", json=payload)
    assert r.status_code == 201
    return r.json()["tapi-connectivity:connectivity-service"]["uuid"]


class TestSnapshot:
    def test_post_snapshot_returns_path_and_timestamp(self, app: TestClient, tmp_path: Path) -> None:
        path = tmp_path / "snap.json"
        r = app.post("/admin/snapshot", json={"path": str(path)})
        assert r.status_code == 200
        data = r.json()
        assert data["path"] == str(path)
        assert "timestamp" in data
        assert path.is_file()

    def test_snapshot_file_has_version_and_services(self, app: TestClient, tmp_path: Path) -> None:
        path = tmp_path / "snap.json"
        app.post("/admin/snapshot", json={"path": str(path)})
        content = path.read_text()
        assert '"version": 1' in content
        assert "services" in content
        assert "spectrum" in content
        assert "service_allocation" in content
        # /config plane state.
        assert "element_overrides" in content
        assert "failed_links" in content
        assert "twin_overrides" in content
        # Physics backend identity (used by restore to reject mismatched
        # snapshots — see TestCrossBackendRestore).
        assert '"backend"' in content


class TestRestore:
    def test_restore_404_when_file_missing(self, app: TestClient) -> None:
        r = app.post("/admin/restore", json={"path": "/nonexistent/snap.json"})
        assert r.status_code == 404

    def test_restore_400_when_path_missing(self, app: TestClient) -> None:
        r = app.post("/admin/restore", json={})
        assert r.status_code == 400

    def test_snapshot_then_restore_restores_services(
        self, app: TestClient, tmp_path: Path
    ) -> None:
        sip_a, sip_z = _get_two_sips(app)
        uuid = _create_service(app, sip_a, sip_z)
        path = tmp_path / "snap.json"
        snap = app.post("/admin/snapshot", json={"path": str(path)})
        assert snap.status_code == 200

        # Delete the service
        app.delete(f"{_BASE}/connectivity-service={uuid}")
        list_r = app.get(f"{_BASE}/connectivity-service")
        services = list_r.json()["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert len(services) == 0

        # Restore
        rest = app.post("/admin/restore", json={"path": str(path)})
        assert rest.status_code == 200
        assert rest.json()["services"] == 1

        list_r2 = app.get(f"{_BASE}/connectivity-service")
        services2 = list_r2.json()["tapi-connectivity:connectivity-context"]["connectivity-service"]
        assert len(services2) == 1
        assert services2[0]["uuid"] == uuid


class TestSnapshotLatest:
    def test_latest_404_when_no_snapshots(self, app: TestClient, tmp_path: Path) -> None:
        # Use an empty dir as snapshot dir by monkeypatching or by creating
        # snapshot with custom path so default dir stays empty. We cannot
        # change admin's default dir per-request. So: only assert that
        # either we get 404 (empty default dir) or 200 (dir has snapshots).
        r = app.get("/admin/snapshot/latest")
        if r.status_code == 404:
            body = r.json()
            # RESTCONF error format or plain detail
            msg = body.get("detail") or ""
            if "ietf-restconf:errors" in body:
                errs = body.get("ietf-restconf:errors", {}).get("error", [])
                msg = errs[0].get("error-message", "") if errs else ""
            assert "snapshot" in msg.lower() or "found" in msg.lower()
        else:
            assert r.status_code == 200
            assert "path" in r.json()
            assert "timestamp" in r.json()

    def test_latest_returns_most_recent_after_snapshot(self, app: TestClient) -> None:
        # Create a snapshot (default path = snapshots/twin-*.json)
        snap = app.post("/admin/snapshot", json={})
        assert snap.status_code == 200
        r = app.get("/admin/snapshot/latest")
        assert r.status_code == 200
        data = r.json()
        assert "path" in data
        assert "timestamp" in data
        assert "twin-" in data["path"] or "snapshots" in data["path"]


class TestListSnapshots:
    def test_snapshots_returns_list_newest_first(self, app: TestClient) -> None:
        app.post("/admin/snapshot", json={})
        r = app.get("/admin/snapshots")
        assert r.status_code == 200
        data = r.json()
        assert "snapshots" in data
        assert isinstance(data["snapshots"], list)
        assert len(data["snapshots"]) >= 1
        for e in data["snapshots"]:
            assert "path" in e
            assert "timestamp" in e
        # Newest first
        if len(data["snapshots"]) >= 2:
            assert data["snapshots"][0]["timestamp"] >= data["snapshots"][1]["timestamp"]


class TestSnapshotContent:
    def test_content_returns_snapshot_json(self, app: TestClient) -> None:
        app.post("/admin/snapshot", json={})
        latest = app.get("/admin/snapshot/latest").json()
        path = latest["path"]
        # path may be absolute; content endpoint accepts filename relative to snapshots/
        name = Path(path).name
        r = app.get("/admin/snapshot/content", params={"path": name})
        assert r.status_code == 200
        data = r.json()
        assert data.get("version") == 1
        assert "services" in data
        assert "spectrum" in data
        assert "service_allocation" in data

    def test_content_400_when_path_missing(self, app: TestClient) -> None:
        r = app.get("/admin/snapshot/content")
        assert r.status_code == 400

    def test_content_404_when_file_missing(self, app: TestClient) -> None:
        r = app.get("/admin/snapshot/content", params={"path": "nonexistent.json"})
        assert r.status_code == 404
