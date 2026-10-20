"""The ``gnpy`` backend run by a gnpy-api service instead of in-process.

The service runs inside the test process: every gnpy-api client is routed to
gnpy-api's FastAPI app through a ``TestClient``, so these tests need the
gnpy-api package and are skipped without it (see CONTRIBUTING.md). Each check
compares against the in-process ``GnpyBackend``: moving GNPy into the service
must not change a single number the twin reports.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.app import create_app
from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig
from twinlight.example_data import REQUIRED_FILES, find_example_file
from twinlight.example_data import main as fetch_examples
from twinlight.physics import element_params, gnpy_api_client
from twinlight.physics.backend import ParamValidationError, build_backend, element_kind_name
from twinlight.physics.egn_backend import _designed_topology_json
from twinlight.physics.gnpy_api_backend import GnpyApiBackend
from twinlight.physics.gnpy_backend import GnpyBackend
from twinlight.state.context import TapiContext

gnpyapi_core = pytest.importorskip("gnpyapi.core", reason="needs the gnpy-api package")

GNPY_API_URL = "http://gnpy-api.test"
SESSION = "/api/v1.0/session"
FIXTURES = Path(__file__).parent.parent / "fixtures"
_CORONET = find_example_file("CORONET_CONUS_Topology.json")
_EQUIPMENT = find_example_file("eqpt_config.json")
# Elements of tests/fixtures/edfa_example_network.json
SRC, DST = "trx Brest_1", "trx Morlaix_1"
FIBER, EDFA = "fiber (Brest → Morlaix)-F", "east edfa in Brest to Morlaix"


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """gnpy-api, in-process: every GnpyApiClient talks to this app."""
    client = TestClient(gnpyapi_core.app)
    monkeypatch.setattr(gnpy_api_client, "new_http_client", lambda base_url: client)
    return client


def _remote(config: TwinConfig) -> TwinConfig:
    return config.model_copy(update={"physics": PhysicsConfig(gnpy_api_url=GNPY_API_URL)})


@pytest.fixture
def remote_config(gnpy_twin_config: TwinConfig, service: TestClient) -> TwinConfig:
    return _remote(gnpy_twin_config)


@pytest.fixture
def coronet_config(tmp_path: Path) -> TwinConfig:
    if _CORONET is None or _EQUIPMENT is None:
        pytest.skip("gnpy is not installed with its CORONET example data")
    return TwinConfig(
        gnpy=GnpyConfig(topology=_CORONET, equipment=_EQUIPMENT),
        simulation={"snapshot_dir": str(tmp_path / "snapshots")},
    )


def _baseline(backend, path: list[str], fmt: str = "DP-QPSK"):
    return asyncio.run(backend.compute_baseline(path, fmt))


def _assert_same_baseline(remote, local) -> None:
    """Every QoT figure bit-identical. The fiber-length total only to float
    precision: the service adds the spans with sum(), which is compensated
    since Python 3.12, while gnpy_adapter keeps a running total."""
    assert remote.total_fiber_km == pytest.approx(local.total_fiber_km, rel=1e-12)
    assert dataclasses.replace(remote, total_fiber_km=0.0) == \
        dataclasses.replace(local, total_fiber_km=0.0)


def _create_service(client: TestClient) -> str:
    sips = client.get(
        "/data/tapi-common:context/service-interface-point"
    ).json()["tapi-common:context"]["service-interface-point"]
    body = {
        "tapi-connectivity:connectivity-service": {
            "name": [{"value-name": "service-name", "value": "gnpy-api"}],
            "end-point": [
                tapi_end_point("a", sips[0]["uuid"]),
                tapi_end_point("z", sips[1]["uuid"]),
            ],
        }
    }
    r = client.post(
        "/data/tapi-connectivity:connectivity-context/connectivity-service", json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()["tapi-connectivity:connectivity-service"]["uuid"]


def _service_baseline(client: TestClient, svc_uuid: str):
    ctx = client.app.state.context  # type: ignore[attr-defined]
    return asyncio.run(ctx.get_or_compute_baseline(ctx.get_service(svc_uuid)))


class TestSelection:
    def test_gnpy_api_url_selects_the_remote_backend(self, remote_config: TwinConfig) -> None:
        backend = build_backend(remote_config)

        assert isinstance(backend, GnpyApiBackend)
        assert backend.available
        # same physics as GnpyBackend, so snapshots stay interchangeable
        assert backend.name == "gnpy"

    def test_unreachable_service_falls_back_to_mock_data(
        self, gnpy_twin_config: TwinConfig,
    ) -> None:
        config = gnpy_twin_config.model_copy(
            update={"physics": PhysicsConfig(gnpy_api_url="http://127.0.0.1:9")},
        )
        backend = build_backend(config)

        assert not backend.available
        assert _baseline(backend, [SRC, DST]) is None
        assert backend.compute_path(SRC, DST) is None

    def test_cli_flag(self, edfa_topology_path: Path) -> None:
        from twinlight.cli import load_config

        config = load_config([
            "--topology", str(edfa_topology_path),
            "--gnpy-api-url", GNPY_API_URL, "--reset",
        ])
        assert config.physics.gnpy_api_url == GNPY_API_URL


class TestParityWithInProcessGnpy:
    def test_designed_elements_and_attributes(self, gnpy_twin_config, remote_config) -> None:
        local = GnpyBackend(gnpy_twin_config)
        remote = GnpyApiBackend(remote_config)

        kinds = {uid: element_kind_name(el) for uid, el in local.uid_map.items()}
        assert {uid: element_kind_name(el) for uid, el in remote.uid_map.items()} == kinds
        for uid, kind in kinds.items():
            if kind == "Fiber":
                assert remote.uid_map[uid].params.length == local.uid_map[uid].params.length
            assert remote.read_all_attributes(uid) == local.read_all_attributes(uid)
            assert remote.is_fiber(uid) == local.is_fiber(uid)
        assert remote.supported_attributes() == element_params.schema()

    @pytest.mark.parametrize("fmt", ["DP-QPSK", "DP-16QAM"])
    def test_path_and_baseline(self, gnpy_twin_config, remote_config, fmt: str) -> None:
        local = GnpyBackend(gnpy_twin_config)
        remote = GnpyApiBackend(remote_config)

        path = remote.compute_path(SRC, DST)
        assert path == local.compute_path(SRC, DST)
        _assert_same_baseline(_baseline(remote, path, fmt), _baseline(local, path, fmt))

    @pytest.mark.parametrize("src, dst", [
        ("trx Abilene", "trx Dallas"),
        ("trx Seattle", "trx Miami"),
        ("trx Boston", "trx Los_Angeles"),
    ])
    def test_coronet_paths_and_baselines(self, coronet_config, service, src, dst) -> None:
        local = GnpyBackend(coronet_config)
        remote = GnpyApiBackend(_remote(coronet_config))

        path = remote.compute_path(src, dst)
        assert path == local.compute_path(src, dst)
        _assert_same_baseline(_baseline(remote, path), _baseline(local, path))

    def test_parameter_writes(self, gnpy_twin_config, remote_config) -> None:
        local = GnpyBackend(gnpy_twin_config)
        remote = GnpyApiBackend(remote_config)
        path = local.compute_path(SRC, DST)

        for backend in (local, remote):
            backend.write_attribute(FIBER, "loss_coef", 0.25)
            backend.write_attribute(EDFA, "tilt_target", 0.5)

        assert remote.read_attribute(FIBER, "loss_coef") == pytest.approx(0.25)
        _assert_same_baseline(_baseline(remote, path), _baseline(local, path))

    def test_validation_errors(self, remote_config) -> None:
        remote = GnpyApiBackend(remote_config)

        with pytest.raises(ParamValidationError):
            remote.write_attribute(FIBER, "loss_coef", 5.0)
        with pytest.raises(ParamValidationError):
            remote.read_attribute(FIBER, "gain_target")
        with pytest.raises(KeyError):
            remote.write_attribute("no such element", "loss_coef", 0.2)
        assert remote.read_attribute(FIBER, "loss_coef") == pytest.approx(0.2)


class TestFailuresAndRedesign:
    def test_failed_fiber_is_routed_around(self, coronet_config, service) -> None:
        remote = GnpyApiBackend(_remote(coronet_config))
        path = remote.compute_path("trx Abilene", "trx Dallas")
        fiber = next(uid for uid in path if remote.is_fiber(uid))

        remote.notify_fiber_failed(fiber, True)
        detour = remote.compute_path("trx Abilene", "trx Dallas")
        assert detour is not None and fiber not in detour
        assert _baseline(remote, path).status == "link-failed"

        remote.notify_fiber_failed(fiber, False)
        assert remote.compute_path("trx Abilene", "trx Dallas") == path

    def test_redesign_reequalises_after_a_loss_change(self, remote_config) -> None:
        remote = GnpyApiBackend(remote_config)
        path = remote.compute_path(SRC, DST)
        nominal = _baseline(remote, path)

        remote.write_attribute(FIBER, "loss_coef", 0.3)
        degraded = _baseline(remote, path)
        remote.redesign()

        assert degraded.gsnr_db < _baseline(remote, path).gsnr_db < nominal.gsnr_db


class TestSession:
    def test_close_ends_the_session(self, remote_config, service) -> None:
        remote = GnpyApiBackend(remote_config)
        token = remote._client.token

        remote.close()

        assert service.get(SESSION, headers={"X-Session-Token": token}).status_code == 401

    def test_lost_session_is_rebuilt_from_the_journal(self, remote_config, service) -> None:
        remote = GnpyApiBackend(remote_config)
        path = remote.compute_path(SRC, DST)
        remote.write_attribute(FIBER, "loss_coef", 0.3)
        remote.redesign()
        remote.write_attribute(EDFA, "tilt_target", 0.5)
        before = _baseline(remote, path)
        # e.g. the service restarted: its in-memory sessions are gone
        service.delete(SESSION, headers={"X-Session-Token": remote._client.token})

        assert _baseline(remote, path) == before
        assert remote.read_attribute(FIBER, "loss_coef") == pytest.approx(0.3)


class TestTwin:
    """The whole twin on the remote backend, against the in-process one."""

    def test_service_baseline_matches_in_process(self, gnpy_app, remote_config) -> None:
        remote_app = TestClient(create_app(remote_config))

        local_svc = _create_service(gnpy_app)
        remote_svc = _create_service(remote_app)

        _assert_same_baseline(
            _service_baseline(remote_app, remote_svc), _service_baseline(gnpy_app, local_svc),
        )
        local_devices = gnpy_app.get("/config/get").json()["devices"]
        assert remote_app.get("/config/get").json()["devices"] == local_devices

    def test_config_plane_and_link_failure(self, remote_config) -> None:
        app = TestClient(create_app(remote_config))
        svc = _create_service(app)
        nominal = _service_baseline(app, svc)

        r = app.post("/config/set", json={"devices": {FIBER: {"loss_coef": 0.3}}})
        assert r.status_code == 200, r.text
        assert _service_baseline(app, svc).gsnr_db < nominal.gsnr_db
        assert app.post(
            "/config/set", json={"devices": {FIBER: {"loss_coef": 9.0}}},
        ).status_code == 400

        app.post("/config/set", json={"devices": {FIBER: {"failed": True}}})
        assert _service_baseline(app, svc).status == "link-failed"

    def test_snapshot_moves_between_in_process_and_remote(
        self, gnpy_context, remote_config, tmp_path,
    ) -> None:
        remote = TapiContext(remote_config)
        remote.apply_element_overrides({FIBER: {"loss_coef": 0.25}})
        snapshot = remote.snapshot(tmp_path / "remote.json")

        gnpy_context.restore_from(snapshot)

        assert gnpy_context._backend.read_attribute(FIBER, "loss_coef") == pytest.approx(0.25)


class TestEgnDesign:
    def test_egn_gets_the_same_amplifier_design_from_the_service(
        self, coronet_config, service,
    ) -> None:
        topology, equipment = coronet_config.gnpy.topology, coronet_config.gnpy.equipment

        remote = _designed_topology_json(topology, equipment, GNPY_API_URL)
        local = _designed_topology_json(topology, equipment)

        assert {el["uid"] for el in remote["elements"]} == {el["uid"] for el in local["elements"]}
        assert len(remote["connections"]) == len(local["connections"])


class TestFetchExamples:
    def test_from_url(self, service, tmp_path: Path) -> None:
        assert fetch_examples(["--from-url", GNPY_API_URL, "--dest", str(tmp_path)]) == 0

        for name in REQUIRED_FILES:
            installed = find_example_file(name)
            assert installed is not None
            assert json.loads((tmp_path / name).read_text(encoding="utf-8")) == \
                json.loads(installed.read_text(encoding="utf-8"))

    def test_unreachable_service(self, tmp_path: Path) -> None:
        assert fetch_examples(["--from-url", "http://127.0.0.1:9", "--dest", str(tmp_path)]) == 1


def test_the_twin_runs_without_gnpy_installed(edfa_topology_path: Path, tmp_path: Path) -> None:
    """With GNPy in a service, nothing on the twin's import path needs gnpy."""
    code = f"""
import sys
sys.modules["gnpy"] = None  # any import of gnpy now raises ImportError
from fastapi.testclient import TestClient
from twinlight.app import create_app
from twinlight.config import TwinConfig
import twinlight.main, twinlight.example_data

config = TwinConfig(
    gnpy={{"topology": {str(edfa_topology_path)!r}}},
    physics={{"gnpy_api_url": "http://127.0.0.1:9"}},
    simulation={{"snapshot_dir": {str(tmp_path)!r}}},
)
assert TestClient(create_app(config)).get("/health").status_code == 200
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
