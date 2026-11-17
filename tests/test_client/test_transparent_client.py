"""TransparentClient and the ``twinlight-transparent`` CLI, in process.

FastAPI's ``TestClient`` is an ``httpx.Client``, so both run against a real
twin with no server.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, tapi_end_point
from tests.test_api.test_config_api import _create_service_via_api
from twinlight.models.connectivity import ConnectivityService
from twinlight_client.transparent import (
    TransparentClient,
    TransparentError,
    path_url,
)
from twinlight_client.transparent_cli import _parse_assignment, _print_qot, main

FWD_FIBER = "fiber (Brest → Morlaix)-F"


@pytest.fixture
def odc(gnpy_app: TestClient) -> TransparentClient:
    return TransparentClient(client=gnpy_app)


class TestPathUrl:
    def test_segments_are_encoded_whole(self) -> None:
        assert path_url('/ElementList/"a (1/2)"/loss_coef') == (
            "/transparent/data/ElementList/%22a%20%281%2F2%29%22/loss_coef"
        )

    def test_datastore_and_root(self) -> None:
        assert path_url("/", "candidate") == "/transparent/ds/candidate"
        assert path_url("/ElementList/*/$Fiber") == "/transparent/data/ElementList/%2A/%24Fiber"


class TestTransparentClient:
    def test_read_and_set(self, odc: TransparentClient) -> None:
        assert odc.root()["backend"] == "gnpy"
        assert odc.value(f'/ElementList/"{FWD_FIBER}"/con_in') == 0.5
        result = odc.set("/ElementList/*/$Fiber/con_in", 1.5)
        assert {c["value"] for c in result["changes"]} == {1.5}
        assert odc.value(f'/ElementList/"{FWD_FIBER}"/con_in') == 1.5
        odc.revert("/ElementList")
        assert odc.get("/", datastore="running", with_defaults="explicit") == {}

    def test_value_needs_exactly_one_leaf(self, odc: TransparentClient) -> None:
        with pytest.raises(ValueError, match="2 leaves"):
            odc.value("/ElementList/*/$Fiber/con_in")

    def test_candidate_round_trip(self, gnpy_app: TestClient, odc: TransparentClient) -> None:
        _create_service_via_api(gnpy_app)
        odc.merge("/ElementList/*/$Fiber", {"loss_coef": 0.3}, datastore="candidate")
        report = odc.validate(with_qot_impact=True)
        assert report["qot-impact"][0]["delta_gsnr_db"] < 0
        assert [e["operation"] for e in odc.compare()] == ["replace", "replace"]
        out = odc.commit(if_match=odc.etag())
        assert out["committed-edits"] == 2

    def test_yang_patch_and_errors(self, odc: TransparentClient) -> None:
        status = odc.yang_patch([
            {"operation": "merge", "target": "/Config/rmsa", "value": {"qot_margin_db": 2.0}},
        ])
        assert status["ok"] == [None]
        with pytest.raises(TransparentError) as exc:
            odc.yang_patch([
                {"operation": "merge", "target": "/ElementList/*/$Fiber",
                 "value": {"loss_coef": 7}},
            ])
        assert exc.value.status == 400
        assert {e["edit-id"] for e in exc.value.errors} == {"1"}

    def test_stale_etag_is_refused(self, odc: TransparentClient) -> None:
        etag = odc.etag()
        odc.set("/Config/rmsa/qot_margin_db", 2.0)
        with pytest.raises(TransparentError) as exc:
            odc.set("/Config/rmsa/qot_margin_db", 3.0, if_match=etag)
        assert exc.value.status == 412

    def test_validate_inline_edits(self, gnpy_app: TestClient, odc: TransparentClient) -> None:
        _create_service_via_api(gnpy_app)
        out = odc.validate(with_qot_impact=True, edits=[
            {"operation": "merge", "target": f'/ElementList/"{FWD_FIBER}"',
             "value": {"failed": True}},
        ])
        assert out["qot-impact"][0]["predicted"]["status"] == "link-failed"


class TestCli:
    def test_parse_assignment(self) -> None:
        assert _parse_assignment("/Config/rmsa/qot_margin_db=2") == (
            "/Config/rmsa/qot_margin_db", 2,
        )
        assert _parse_assignment('/ElementList/"a=b"/failed=true') == (
            '/ElementList/"a=b"/failed', True,
        )
        assert _parse_assignment("/Config/x/ase_distribution=rx")[1] == "rx"

    def test_set_validate_commit(
        self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        _create_service_via_api(gnpy_app)
        assert main(["set", "--candidate", "/ElementList/*/$Edfa/p_max=10"],
                    client=gnpy_app) == 0
        assert main(["validate", "--qot"], client=gnpy_app) == 0
        out = capsys.readouterr().out
        assert "p_max: 23.0 -> 10 dBm" in out and "config-api-test" in out
        assert main(["commit"], client=gnpy_app) == 0
        assert "committed 4 edit(s)" in capsys.readouterr().out

    def test_get_leaves_marks_explicit(
        self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        main(["set", "/Config/rmsa/qot_margin_db=2.5"], client=gnpy_app)
        capsys.readouterr()
        assert main(["get", "/Config/rmsa", "--leaves"], client=gnpy_app) == 0
        assert "/Config/rmsa/qot_margin_db = 2.5 *" in capsys.readouterr().out

    def test_error_exit_code(
        self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["set", "/Config/rmsa/qot_margin_db=-1"], client=gnpy_app) == 2
        assert "must be >= 0" in capsys.readouterr().err


class TestCliNoSignal:
    """``validate --qot`` for a service with no signal: the status as the
    verdict, never a number, on both backends."""

    @staticmethod
    def _twin(tmp_path, backend: str) -> TestClient:
        from tests.conftest import _mesh_config
        from twinlight.app import create_app

        return TestClient(create_app(_mesh_config(tmp_path, backend)))

    @staticmethod
    def _fiber_on(client: TestClient, uuid: str) -> str:
        ctx = client.app.state.context
        path = ctx._allocated_path_uids(uuid)
        fibers = set(ctx._backend.route_fibers(path))
        return next(uid for uid in path if uid in fibers)

    @staticmethod
    def _row(out: str, uuid: str) -> list[str]:
        [line] = [ln for ln in out.splitlines() if ln.startswith(uuid)]
        return line[len(uuid):].split()

    @pytest.mark.parametrize("backend", ["egn", "gnpy"])
    def test_out_of_band(
        self, backend: str, tmp_path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from tests.test_state.test_snapshot import _v1_snapshot

        client = self._twin(tmp_path, backend)
        snapshot = tmp_path / "v1.json"
        uuid = _v1_snapshot(client.app.state.context, snapshot)
        assert client.post("/admin/restore", json={"path": str(snapshot)}).status_code == 200
        fiber = self._fiber_on(client, uuid)
        assert main(["set", "--candidate", f'/ElementList/"{fiber}"/loss_coef=0.25'],
                    client=client) == 0
        capsys.readouterr()
        assert main(["validate", "--qot"], client=client) == 0
        assert self._row(capsys.readouterr().out, uuid) == [
            "no", "signal", "no", "signal", "OUT", "OF", "BAND"]

    @pytest.mark.parametrize("backend", ["egn", "gnpy"])
    def test_link_failed(
        self, backend: str, tmp_path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        client = self._twin(tmp_path, backend)
        refs = MeshRefs(client.app.state.context)
        svc = ConnectivityService.model_validate({"end-point": [
            tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]})
        asyncio.run(client.app.state.context.add_service(svc))
        fiber = self._fiber_on(client, svc.uuid)
        assert main(["set", "--candidate", f'/ElementList/"{fiber}"/failed=true'],
                    client=client) == 0
        capsys.readouterr()
        assert main(["validate", "--qot"], client=client) == 0
        current, *rest = self._row(capsys.readouterr().out, svc.uuid)
        assert float(current) > 0
        assert rest == ["cut", "LINK", "FAILED"]


class TestPrintQot:
    @staticmethod
    def _row(current: dict | None, predicted: dict | None) -> dict:
        return {"service": "s", "name": "svc", "current": current, "predicted": predicted,
                "delta_gsnr_db": None, "above_required": False,
                "meets_admission_margin": False}

    @pytest.mark.parametrize(("status", "cell", "verdict"), [
        ("link-failed", "cut", "LINK FAILED"),
        ("out-of-band", "no signal", "OUT OF BAND"),
        ("some-future-status", "no signal", "SOME FUTURE STATUS"),
    ])
    def test_no_signal_prints_no_number(
        self, status: str, cell: str, verdict: str, capsys: pytest.CaptureFixture[str],
    ) -> None:
        none = {"status": status, "gsnr_db": None, "osnr_db": None}
        _print_qot([self._row(none, none)])
        line = capsys.readouterr().out.splitlines()[1]
        assert line.split(None, 1)[1].split() == [*cell.split(), *cell.split(), *verdict.split()]
        assert not any(ch.isdigit() for ch in line)
