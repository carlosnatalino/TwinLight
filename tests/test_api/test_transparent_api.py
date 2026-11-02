"""HTTP tests for the transparent optical domain controller (/transparent)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from tests.test_api.test_config_api import _create_service_via_api
from twinlight.app import create_app
from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig

FIXTURES = Path(__file__).parent.parent / "fixtures"
FWD_FIBER = "fiber (Brest → Morlaix)-F"
REV_FIBER = "fiber (Morlaix → Brest)-F"
EAST_AMP = "east edfa in Brest to Morlaix"
DATA = "/transparent/data"
CANDIDATE = "/transparent/ds/candidate"
OPS = "/transparent/operations"
REPR = {"Prefer": "return=representation"}
YANG_PATCH = {"Content-Type": "application/yang-patch+json"}


def _data(r: Any) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    return r.json()["twinlight-transparent:data"]


def _value(client: TestClient, path: str, ds: str = DATA) -> Any:
    node: Any = _data(client.get(ds + path))
    for part in path.strip("/").split("/"):
        node = node[part]
    return node


def _patch(client: TestClient, edits: list[dict[str, Any]], **headers: str) -> Any:
    body = {"ietf-yang-patch:yang-patch": {"patch-id": "t", "edit": edits}}
    return client.patch(DATA, content=json.dumps(body), headers={**YANG_PATCH, **headers})


class TestDiscovery:
    def test_root_resource(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get("/transparent")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/yang-data+json")
        root = r.json()["twinlight-transparent:restconf"]
        assert set(root["operations"]) == {
            "commit", "discard-changes", "validate", "compare", "redesign",
        }
        assert "ietf-datastores:candidate" in root["datastores"]
        assert r.headers["etag"] == '"r0"'

    def test_schema_describes_kinds_and_config(self, gnpy_app: TestClient) -> None:
        tree = gnpy_app.get("/transparent/schema").json()[
            "twinlight-transparent:schema"]["tree"]
        fiber = tree["ElementList"]["entry-by-kind"]["Fiber"]
        assert fiber["loss_coef"] == {
            "type": "number", "config": True, "unit": "dB/km", "min": 0.0, "max": 2.0,
            "description": "Attenuation coefficient at the reference frequency",
        }
        assert fiber["failed"]["type"] == "boolean"
        assert tree["Config"]["rmsa"]["qot_margin_db"]["config"] is True
        assert tree["Config"]["spectrum"]["num_slots"]["config"] is False

    def test_operations_list(self, gnpy_app: TestClient) -> None:
        ops = gnpy_app.get(OPS).json()["twinlight-transparent:operations"]
        assert "commit" in ops


class TestRead:
    def test_tree_is_keyed_by_path_segments(self, gnpy_app: TestClient) -> None:
        tree = _data(gnpy_app.get(f"{DATA}/LinkList/*/*/ElementList/*/$Edfa/gain_target"))
        fwd = tree["LinkList"]["roadm Brest"]["roadm Morlaix"]["ElementList"]
        assert fwd[EAST_AMP] == {"gain_target": 18.0}

    def test_leaves_format(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get(f"{DATA}/ElementList/*/$Fiber/con_in", params={"format": "leaves"})
        leaves = r.json()["twinlight-transparent:leaves"]
        assert [(x["path"], x["value"]) for x in leaves] == [
            (f"/ElementList/{FWD_FIBER}/con_in", 0.5),
            (f"/ElementList/{REV_FIBER}/con_in", 0.5),
        ]

    def test_depth_and_content(self, gnpy_app: TestClient) -> None:
        keys = _data(gnpy_app.get(DATA, params={"depth": 2}))
        assert set(keys) >= {"ElementList", "Config"} and keys["Config"] == {}
        cfg = _data(gnpy_app.get(f"{DATA}/Config/rmsa", params={"content": "nonconfig"}))
        assert cfg == {"Config": {"rmsa": {}}}

    def test_glob_and_regex_in_the_url(self, gnpy_app: TestClient) -> None:
        url = f"{DATA}/ElementList/{quote('~\"^fiber.*Brest →\"')}/uid"
        assert list(_data(gnpy_app.get(url))["ElementList"]) == [FWD_FIBER]

    def test_index_term(self, gnpy_app: TestClient) -> None:
        tree = _data(gnpy_app.get(f"{DATA}/LinkList/roadm Brest/roadm Morlaix/ElementList/[2]/uid"))
        assert list(tree["LinkList"]["roadm Brest"]["roadm Morlaix"]["ElementList"]) == [EAST_AMP]

    def test_matches_nothing_is_404(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.get(f"{DATA}/ElementList/no-such/uid")
        assert r.status_code == 404
        assert r.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "invalid-value"

    @pytest.mark.parametrize("path", [
        "/ElementList/*/$Edfa/loss_coef", "/Nope", '/ElementList/"open',
    ])
    def test_bad_path_is_400(self, gnpy_app: TestClient, path: str) -> None:
        r = gnpy_app.get(DATA + quote(path))
        assert r.status_code == 400
        assert "ietf-restconf:errors" in r.json()

    @pytest.mark.parametrize("params", [
        {"depth": 0}, {"content": "some"}, {"with-defaults": "never"}, {"format": "xml"},
    ])
    def test_bad_query_is_400(self, gnpy_app: TestClient, params: dict[str, Any]) -> None:
        assert gnpy_app.get(DATA, params=params).status_code == 400

    def test_if_none_match(self, gnpy_app: TestClient) -> None:
        etag = gnpy_app.get(f"{DATA}/Config/rmsa").headers["etag"]
        assert gnpy_app.get(f"{DATA}/Config/rmsa",
                            headers={"If-None-Match": etag}).status_code == 304

    def test_service_qot(self, gnpy_app: TestClient) -> None:
        uuid = _create_service_via_api(gnpy_app)
        qot = _data(gnpy_app.get(f"{DATA}/ServiceList/config-api-test/qot"))
        assert qot["ServiceList"][uuid]["qot"]["status"] == "ok"


class TestWriteRunning:
    def test_patch_is_204_and_bumps_the_etag(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.patch(f"{DATA}/ElementList/*/$Fiber", json={"con_in": 1.0})
        assert r.status_code == 204 and r.headers["etag"] == '"r2"'
        assert _value(gnpy_app, f"/ElementList/{FWD_FIBER}/con_in") == 1.0

    def test_prefer_representation_reports_changes(self, gnpy_app: TestClient) -> None:
        uuid = _create_service_via_api(gnpy_app)
        r = gnpy_app.patch(f"{DATA}/ElementList/{FWD_FIBER}/loss_coef",
                           json={"loss_coef": 0.25}, headers=REPR)
        assert r.status_code == 200
        assert r.headers["preference-applied"] == "return=representation"
        result = r.json()["twinlight-transparent:edit-result"]
        assert result["changes"] == [{
            "path": f"/ElementList/{FWD_FIBER}/loss_coef", "operation": "replace",
            "previous": pytest.approx(0.2), "value": 0.25, "unit": "dB/km",
        }]
        assert result["invalidated-services"] == [uuid]

    def test_put_replaces_and_reverts_the_rest(self, gnpy_app: TestClient) -> None:
        gnpy_app.patch(f"{DATA}/ElementList/{FWD_FIBER}", json={"con_in": 2.0})
        r = gnpy_app.put(f"{DATA}/ElementList/{FWD_FIBER}",
                         json={FWD_FIBER: {"loss_coef": 0.3}})
        assert r.status_code == 204
        assert _value(gnpy_app, f"/ElementList/{FWD_FIBER}/con_in") == 0.5
        assert _value(gnpy_app, f"/ElementList/{FWD_FIBER}/loss_coef") == 0.3

    def test_delete_reverts_to_startup(self, gnpy_app: TestClient) -> None:
        gnpy_app.patch(f"{DATA}/Config/transients", json={
            "polarization": {"enabled": False}, "phase_noise": {"lo_linewidth_hz": 5e5},
        })
        assert gnpy_app.get("/config/get").json()["twin"]["transients"]["polarization"] == {
            "enabled": False,
        }
        assert gnpy_app.delete(f"{DATA}/Config").status_code == 204
        assert _value(gnpy_app, "/Config/transients/polarization/enabled") is True
        assert _data(gnpy_app.get("/transparent/ds/running",
                                  params={"with-defaults": "explicit"})) == {}

    def test_fiber_cut_shows_on_every_interface(self, gnpy_app: TestClient) -> None:
        uuid = _create_service_via_api(gnpy_app)
        r = gnpy_app.put(f"{DATA}/ElementList/{FWD_FIBER}/failed", json={"failed": True})
        assert r.status_code == 204
        assert gnpy_app.get(f"/internal/opm/{uuid}").json()["status"] == "link-failed"
        assert FWD_FIBER in gnpy_app.get("/config/get").json()["failed_links"]

    def test_write_through_config_set_moves_the_etag(self, gnpy_app: TestClient) -> None:
        etag = gnpy_app.get(DATA + "/Config/rmsa").headers["etag"]
        gnpy_app.post("/config/set", json={"twin": {"rmsa": {"qot_margin_db": 2.0}}})
        r = gnpy_app.patch(f"{DATA}/Config/rmsa", json={"qot_margin_db": 3.0},
                           headers={"If-Match": etag})
        assert r.status_code == 412
        assert _value(gnpy_app, "/Config/rmsa/qot_margin_db") == 2.0

    def test_validation_refuses_the_whole_edit(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.patch(f"{DATA}/ElementList/*/$Edfa", json={"p_max": 99})
        assert r.status_code == 400
        assert len(r.json()["ietf-restconf:errors"]["error"]) == 4
        assert _value(gnpy_app, f"/ElementList/{EAST_AMP}/p_max") == 23.0

    def test_read_only_leaf_is_403(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.put(f"{DATA}/ElementList/{FWD_FIBER}/length_km", json=80)
        assert r.status_code == 403
        assert r.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "access-denied"

    def test_post_creates_nothing(self, gnpy_app: TestClient) -> None:
        assert gnpy_app.post(f"{DATA}/ElementList", json={}).status_code == 405

    def test_body_required_and_json(self, gnpy_app: TestClient) -> None:
        assert gnpy_app.patch(f"{DATA}/Config/rmsa").status_code == 400
        r = gnpy_app.patch(f"{DATA}/Config/rmsa", content=b"{nope",
                           headers={"Content-Type": "application/json"})
        assert r.status_code == 400


class TestYangPatch:
    def test_ordered_edits_in_one_transaction(self, gnpy_app: TestClient) -> None:
        r = _patch(gnpy_app, [
            {"edit-id": "1", "operation": "merge", "target": "/ElementList/*/$Fiber",
             "value": {"loss_coef": 0.3}},
            {"edit-id": "2", "operation": "replace",
             "target": f"/ElementList/{FWD_FIBER}/loss_coef", "value": {"loss_coef": 0.25}},
            {"edit-id": "3", "operation": "merge", "target": "/Config/rmsa",
             "value": {"qot_margin_db": 2.5}},
        ])
        assert r.status_code == 200, r.text
        assert r.json() == {"ietf-yang-patch:yang-patch-status": {"patch-id": "t", "ok": [None]}}
        assert _value(gnpy_app, f"/ElementList/{FWD_FIBER}/loss_coef") == 0.25  # later wins
        assert _value(gnpy_app, f"/ElementList/{REV_FIBER}/loss_coef") == 0.3

    def test_errors_are_reported_per_edit_and_nothing_lands(
        self, gnpy_app: TestClient,
    ) -> None:
        r = _patch(gnpy_app, [
            {"edit-id": "ok", "operation": "merge", "target": "/Config/rmsa",
             "value": {"qot_margin_db": 2.5}},
            {"edit-id": "range", "operation": "merge", "target": "/ElementList/*/$Edfa",
             "value": {"in_voa": 50}},
            {"edit-id": "path", "operation": "remove", "target": "/ElementList/x/$Nope"},
            {"edit-id": "op", "operation": "create", "target": "/Config/rmsa", "value": {}},
        ])
        assert r.status_code == 501
        status = r.json()["ietf-yang-patch:yang-patch-status"]
        failed = {e["edit-id"] for e in status["edit-status"]["edit"]}
        assert failed == {"path", "op"}  # structural errors stop before validation
        assert _value(gnpy_app, "/Config/rmsa/qot_margin_db") == 1.5

    def test_value_errors_name_their_edit(self, gnpy_app: TestClient) -> None:
        r = _patch(gnpy_app, [
            {"edit-id": "a", "operation": "merge", "target": "/Config/rmsa",
             "value": {"qot_margin_db": 2.5}},
            {"edit-id": "b", "operation": "merge", "target": "/ElementList/*/$Edfa",
             "value": {"in_voa": 50}},
        ])
        assert r.status_code == 400
        [edit] = r.json()["ietf-yang-patch:yang-patch-status"]["edit-status"]["edit"]
        assert edit["edit-id"] == "b" and len(edit["errors"]["error"]) == 4

    def test_malformed(self, gnpy_app: TestClient) -> None:
        r = gnpy_app.patch(DATA, content=b'{"x": 1}', headers=YANG_PATCH)
        assert r.status_code == 400


class TestCandidate:
    def test_stage_validate_compare_commit(self, gnpy_app: TestClient) -> None:
        uuid = _create_service_via_api(gnpy_app)
        r = gnpy_app.patch(f"{CANDIDATE}/ElementList/*/$Fiber",
                           json={"loss_coef": 0.3}, headers=REPR)
        assert r.status_code == 200 and r.headers["etag"].startswith('"c1-')
        # Staged, not applied.
        assert _value(gnpy_app, f"/ElementList/{FWD_FIBER}/loss_coef", CANDIDATE) == 0.3
        assert _value(gnpy_app, f"/ElementList/{FWD_FIBER}/loss_coef") == pytest.approx(0.2)

        out = gnpy_app.post(f"{OPS}/validate", json={"input": {"with-qot-impact": True}})
        result = out.json()["twinlight-transparent:output"]
        [impact] = result["qot-impact"]
        assert impact["service"] == uuid and impact["delta_gsnr_db"] < 0

        diff = gnpy_app.post(f"{OPS}/compare", json={"input": {
            "source": "ietf-datastores:running", "target": "candidate"}})
        edits = diff.json()["twinlight-transparent:output"]["differences"][
            "ietf-yang-patch:yang-patch"]["edit"]
        assert len(edits) == 2

        etag = gnpy_app.get(DATA).headers["etag"]
        assert gnpy_app.post(f"{OPS}/commit", headers={"If-Match": '"r42"'}).status_code == 412
        done = gnpy_app.post(f"{OPS}/commit", headers={"If-Match": etag})
        output = done.json()["twinlight-transparent:output"]
        assert output["committed-edits"] == 2 and output["invalidated-services"] == [uuid]
        assert output["running-changed-since-staged"] is False
        assert _value(gnpy_app, f"/ElementList/{FWD_FIBER}/loss_coef") == 0.3
        assert gnpy_app.get("/transparent").json()[
            "twinlight-transparent:restconf"]["candidate-pending-edits"] == 0

    def test_compare_output_is_a_patch_for_running(self, gnpy_app: TestClient) -> None:
        gnpy_app.patch(f"{CANDIDATE}/Config/rmsa", json={"qot_margin_db": 3.0})
        diff = gnpy_app.post(f"{OPS}/compare", json={}).json()[
            "twinlight-transparent:output"]["differences"]
        r = gnpy_app.patch(DATA, content=json.dumps(diff), headers=YANG_PATCH)
        assert r.status_code == 200
        assert _value(gnpy_app, "/Config/rmsa/qot_margin_db") == 3.0

    def test_commit_reports_running_moved(self, gnpy_app: TestClient) -> None:
        gnpy_app.patch(f"{CANDIDATE}/Config/rmsa", json={"qot_margin_db": 3.0})
        gnpy_app.patch(f"{DATA}/Config/rmsa", json={"k_shortest_paths": 2})
        out = gnpy_app.post(f"{OPS}/commit").json()["twinlight-transparent:output"]
        assert out["running-changed-since-staged"] is True

    def test_candidate_cross_field_rules_see_staged_edits(
        self, gnpy_app: TestClient,
    ) -> None:
        assert gnpy_app.patch(f"{CANDIDATE}/Config/fec/hard_decision",
                              json={"k": 200}).status_code == 204
        # t=20 is only legal because k=200 is already staged.
        assert gnpy_app.patch(f"{CANDIDATE}/Config/fec/hard_decision",
                              json={"t": 20}).status_code == 204
        assert gnpy_app.patch(f"{DATA}/Config/fec/hard_decision",
                              json={"t": 20}).status_code == 400

    def test_discard(self, gnpy_app: TestClient) -> None:
        gnpy_app.patch(f"{CANDIDATE}/Config/rmsa", json={"qot_margin_db": 3.0})
        out = gnpy_app.post(f"{OPS}/discard-changes").json()["twinlight-transparent:output"]
        assert out == {"discarded-edits": 1}
        assert _value(gnpy_app, "/Config/rmsa/qot_margin_db", CANDIDATE) == 1.5

    def test_validate_inline_patch_against_running(self, gnpy_app: TestClient) -> None:
        _create_service_via_api(gnpy_app)
        out = gnpy_app.post(f"{OPS}/validate", json={"input": {
            "with-qot-impact": True,
            "yang-patch": {"edit": [{"edit-id": "1", "operation": "merge",
                                     "target": f"/ElementList/{FWD_FIBER}",
                                     "value": {"failed": True}}]},
        }}).json()["twinlight-transparent:output"]
        assert out["source"] == "inline yang-patch"
        assert out["qot-impact"][0]["predicted"]["status"] == "link-failed"
        assert gnpy_app.get("/config/get").json()["failed_links"] == []

    def test_startup_is_read_only_and_names_are_checked(self, gnpy_app: TestClient) -> None:
        assert gnpy_app.patch("/transparent/ds/startup/Config/rmsa",
                              json={"qot_margin_db": 3}).status_code == 405
        assert gnpy_app.get("/transparent/ds/scratch").status_code == 404
        assert gnpy_app.get("/transparent/ds/ietf-datastores:startup/Config/rmsa").status_code == 200

    def test_unknown_operation(self, gnpy_app: TestClient) -> None:
        assert gnpy_app.post(f"{OPS}/reboot").status_code == 404


def test_redesign_runs_under_gnpy(gnpy_app: TestClient) -> None:
    r = gnpy_app.post(f"{OPS}/redesign")
    assert r.status_code == 200 and r.headers["etag"] == '"r1"'


@pytest.fixture
def split_span_app(gnpy_twin_config: TwinConfig, tmp_path: Path) -> TestClient:
    data = json.loads(Path(gnpy_twin_config.gnpy.topology).read_text())
    for el in data["elements"]:
        if el["type"] == "Fiber":
            el["params"]["length"] = 200
    topo = tmp_path / "long.json"
    topo.write_text(json.dumps(data))
    config = gnpy_twin_config.model_copy(deep=True)
    config.gnpy.topology = topo
    return TestClient(create_app(config))


def test_encoded_slash_addresses_a_sub_span(split_span_app: TestClient) -> None:
    sub = f"{FWD_FIBER}_(1/2)"
    url = f"{DATA}/ElementList/{quote(sub, safe='')}/loss_coef"
    assert "%2F" in url
    assert split_span_app.patch(url, json=0.25).status_code == 204
    assert _data(split_span_app.get(url)) == {"ElementList": {sub: {"loss_coef": 0.25}}}
    other = _data(split_span_app.get(f"{DATA}/ElementList/{quote(FWD_FIBER)}*/loss_coef"))
    assert other["ElementList"][f"{FWD_FIBER}_(2/2)"]["loss_coef"] == pytest.approx(0.2)


class TestOtherBackends:
    @pytest.fixture
    def egn_app(self, tmp_path: Path) -> TestClient:
        return TestClient(create_app(TwinConfig(
            gnpy=GnpyConfig(topology=FIXTURES / "edfa_example_network.json"),
            physics=PhysicsConfig(backend="egn"),
            simulation={"snapshot_dir": str(tmp_path / "snapshots")},
        )))

    def test_egn_tree_and_write(self, egn_app: TestClient) -> None:
        edfa = _data(egn_app.get(f"{DATA}/ElementList/*/$Edfa"))["ElementList"]
        assert all("nf0" in v and "gain_target" not in v for v in edfa.values())
        assert egn_app.patch(f"{DATA}/ElementList/*/$Edfa", json={"nf0": 6.0}).status_code == 204
        assert egn_app.delete(f"{DATA}/ElementList").status_code == 204
        assert egn_app.post(f"{OPS}/redesign").status_code == 501

    def test_no_backend_still_serves_config(self, app: TestClient) -> None:
        assert _data(app.get(f"{DATA}/ElementList")) == {"ElementList": {}}
        assert app.patch(f"{DATA}/Config/rmsa", json={"qot_margin_db": 2.0}).status_code == 204
        assert app.post(f"{OPS}/redesign").status_code == 503
