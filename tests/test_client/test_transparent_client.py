"""TransparentClient and the ``twinlight-transparent`` CLI, in process.

FastAPI's ``TestClient`` is an ``httpx.Client``, so both run against a real
twin with no server.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_api.test_config_api import _create_service_via_api
from twinlight_client.transparent import (
    TransparentClient,
    TransparentError,
    path_url,
)
from twinlight_client.transparent_cli import _parse_assignment, main

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
