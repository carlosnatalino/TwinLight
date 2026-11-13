"""The transparent controller's tree, change sets and datastores, against a
real GNPy network — plus the TapiContext support they rely on (running
revision, startup values, revert, link index, QoT what-if)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from twinlight.models.common import NameAndValue
from twinlight.models.connectivity import (
    ConnectivityService,
    ConnectivityServiceEndPoint,
    SipRef,
)
from twinlight.physics.modulation import ModulationFormat
from twinlight.state.context import TapiContext
from twinlight.transparent.datastore import (
    REVERT,
    ChangeError,
    RenderOptions,
    apply_changes,
    build_edit,
    compare,
    preview_changes,
    render_leaves,
    render_tree,
    source_for,
    validate_changes,
)
from twinlight.transparent.path import parse_path
from twinlight.transparent.tree import LeafKey, TreeBuilder, TreeError, resolve

FWD_FIBER = "fiber (Brest → Morlaix)-F"
REV_FIBER = "fiber (Morlaix → Brest)-F"
EAST_AMP = "east edfa in Brest to Morlaix"
FWD_BOOSTER = "Edfa_booster_roadm Brest_to_fiber (Brest → Morlaix)-F"


def _nodes(ctx: TapiContext, path: str) -> list[Any]:
    return resolve(TreeBuilder(ctx).root(), parse_path(path))


def _paths(ctx: TapiContext, path: str) -> list[str]:
    return [n.path_text for n in _nodes(ctx, path)]


async def _service(ctx: TapiContext, name: str = "svc-a") -> str:
    by_uid = {uid: sip for sip, uid in ctx._sip_to_gnpy_uid.items()}
    svc = ConnectivityService(
        name=[NameAndValue(value_name="service-name", value=name)],
        modulation_format=ModulationFormat.DP_QPSK,
        end_point=[
            ConnectivityServiceEndPoint(local_id="a", service_interface_point=SipRef(
                service_interface_point_uuid=by_uid["trx Brest_1"])),
            ConnectivityServiceEndPoint(local_id="z", service_interface_point=SipRef(
                service_interface_point_uuid=by_uid["trx Morlaix_1"])),
        ],
    )
    await ctx.add_service(svc)
    return svc.uuid


def _key(uid: str, attr: str) -> LeafKey:
    return LeafKey("element", uid, attr)


# -- Tree --------------------------------------------------------------------------


class TestTree:
    def test_root_branches(self, gnpy_context: TapiContext) -> None:
        root = TreeBuilder(gnpy_context).root()
        assert list(root.children()) == [
            "ElementList", "NodeList", "LinkList", "ServiceList",
            "ModulationList", "Config",
        ]

    def test_element_list_includes_designed_boosters(self, gnpy_context: TapiContext) -> None:
        uids = [p.split("/", 2)[2] for p in _paths(gnpy_context, "/ElementList/*")]
        assert uids == sorted(gnpy_context._backend.uid_map)
        assert FWD_BOOSTER in uids

    def test_node_list_is_the_terminals(self, gnpy_context: TapiContext) -> None:
        kinds = {n.element_kind for n in _nodes(gnpy_context, "/NodeList/*")}
        assert kinds == {"Roadm", "Transceiver"}

    def test_link_is_directional_and_in_propagation_order(
        self, gnpy_context: TapiContext,
    ) -> None:
        links = _paths(gnpy_context, "/LinkList/*/*")
        assert links == [
            "/LinkList/roadm Brest/roadm Morlaix", "/LinkList/roadm Morlaix/roadm Brest",
        ]
        order = [
            n.path[-1] for n in
            _nodes(gnpy_context, "/LinkList/roadm Brest/roadm Morlaix/ElementList/*")
        ]
        assert order == [FWD_BOOSTER, FWD_FIBER, EAST_AMP]
        # ns-3 index terms follow that order.
        first = _nodes(gnpy_context, "/LinkList/roadm Brest/roadm Morlaix/ElementList/[0]")
        assert [n.path[-1] for n in first] == [FWD_BOOSTER]

    def test_cast_filters_and_scopes_attributes(self, gnpy_context: TapiContext) -> None:
        gains = _nodes(gnpy_context, "/ElementList/*/$Edfa/gain_target")
        assert len(gains) == 4 and all(n.key.name == "gain_target" for n in gains)
        assert _paths(gnpy_context, "/ElementList/*/$Roadm|Transceiver/uid")

    def test_attribute_without_cast_matches_where_it_exists(
        self, gnpy_context: TapiContext,
    ) -> None:
        assert len(_nodes(gnpy_context, "/ElementList/*/loss_coef")) == 2

    @pytest.mark.parametrize(("path", "message"), [
        ("/ElementList/*/$Fibre", "unknown element kind"),
        ("/ElementList/*/$Edfa/loss_coef", "Edfa has no 'loss_coef'"),
        ('/ElementList/"roadm Brest"/loss_coef', "no child named 'loss_coef'"),
        ("/Config/rmsa/qot_margin_db/x", "is a leaf"),
        ("/LinkList/$Fiber", "follows an element entry"),
        ("/Nope", "no child named 'Nope'"),
    ])
    def test_errors_name_the_problem(
        self, gnpy_context: TapiContext, path: str, message: str,
    ) -> None:
        with pytest.raises(TreeError, match=message):
            _nodes(gnpy_context, path)

    def test_well_formed_path_matching_nothing_is_empty(
        self, gnpy_context: TapiContext,
    ) -> None:
        assert _nodes(gnpy_context, "/ElementList/no-such-element") == []
        assert _nodes(gnpy_context, "/ElementList/*/loss*") != []

    def test_config_leaf_flags(self, gnpy_context: TapiContext) -> None:
        margin = _nodes(gnpy_context, "/Config/rmsa/qot_margin_db")[0]
        slots = _nodes(gnpy_context, "/Config/spectrum/num_slots")[0]
        inert = _nodes(gnpy_context, "/Config/transients/edfa_reservoir/solver_method")[0]
        assert margin.spec.config and margin.spec.minimum == 0
        assert not slots.spec.config and "restart-only" in slots.spec.note
        assert not inert.spec.config and "not read" in inert.spec.note

    async def test_service_selected_by_name_and_reports_qot(
        self, gnpy_context: TapiContext,
    ) -> None:
        uuid = await _service(gnpy_context, "brest-morlaix")
        nodes = _nodes(gnpy_context, "/ServiceList/brest-morlaix/qot")
        assert [n.path for n in nodes] == [("ServiceList", uuid, "qot")]
        tree = await render_tree(nodes, RenderOptions(source_for(gnpy_context, "operational")))
        qot = tree["ServiceList"][uuid]["qot"]
        assert qot["status"] == "ok" and qot["gsnr_db"] > 8.5
        assert qot["margin_db"] == pytest.approx(qot["gsnr_db"] - 8.5, abs=1e-5)
        path = _nodes(gnpy_context, "/ServiceList/brest-morlaix/ElementList/*")
        assert path[0].path[-1] == "trx Brest_1"


# -- Rendering ---------------------------------------------------------------------


class TestRendering:
    async def test_depth_truncates_to_empty_containers(
        self, gnpy_context: TapiContext,
    ) -> None:
        opts = RenderOptions(source_for(gnpy_context, "operational"), depth=2)
        tree = await render_tree(_nodes(gnpy_context, "/ElementList"), opts)
        assert all(v == {} for v in tree["ElementList"].values())

    async def test_content_filters(self, gnpy_context: TapiContext) -> None:
        nodes = _nodes(gnpy_context, f'/ElementList/"{FWD_FIBER}"')
        config = await render_tree(nodes, RenderOptions(
            source_for(gnpy_context, "operational"), content="config"))
        state = await render_tree(nodes, RenderOptions(
            source_for(gnpy_context, "operational"), content="nonconfig"))
        assert "loss_coef" in config["ElementList"][FWD_FIBER]
        assert "uid" not in config["ElementList"][FWD_FIBER]
        assert "uid" in state["ElementList"][FWD_FIBER]
        assert "loss_coef" not in state["ElementList"][FWD_FIBER]

    async def test_with_defaults_explicit_and_trim(self, gnpy_context: TapiContext) -> None:
        apply_changes(gnpy_context, {_key(FWD_FIBER, "loss_coef"): 0.25,
                                     _key(EAST_AMP, "gain_target"): 18.0})
        running = source_for(gnpy_context, "running")
        root = _nodes(gnpy_context, "/")
        explicit = await render_tree(root, RenderOptions(running, with_defaults="explicit"))
        trim = await render_tree(root, RenderOptions(running, with_defaults="trim"))
        # Conventional datastores show each leaf once, at its canonical place.
        assert set(explicit) == {"ElementList"}
        assert explicit["ElementList"] == {
            FWD_FIBER: {"loss_coef": 0.25}, EAST_AMP: {"gain_target": 18.0},
        }
        # 18 dB is the amplifier's startup gain: set, but not different.
        assert trim["ElementList"] == {FWD_FIBER: {"loss_coef": 0.25}}

    async def test_leaves_format_carries_metadata(self, gnpy_context: TapiContext) -> None:
        leaves = await render_leaves(
            _nodes(gnpy_context, "/LinkList/*/*/ElementList/*/$Fiber/loss_coef"),
            RenderOptions(source_for(gnpy_context, "operational")),
        )
        assert len(leaves) == 2
        leaf = leaves[0]
        assert leaf["canonical"] == f"/ElementList/{FWD_FIBER}/loss_coef"
        assert leaf["unit"] == "dB/km" and leaf["config"] is True
        assert leaf["startup"] == pytest.approx(0.2) and leaf["explicit"] is False


# -- Change sets ---------------------------------------------------------------------


class TestEdits:
    def test_merge_applies_relative_body_to_each_match(
        self, gnpy_context: TapiContext,
    ) -> None:
        changes = build_edit(
            _nodes(gnpy_context, "/ElementList/*/$Edfa"), "merge", {"p_max": 21},
        )
        assert len(changes) == 4 and set(changes.values()) == {21}

    def test_replace_reverts_what_the_body_omits(self, gnpy_context: TapiContext) -> None:
        changes = build_edit(
            _nodes(gnpy_context, f'/ElementList/"{FWD_FIBER}"'), "replace",
            {"loss_coef": 0.3},
        )
        assert changes[_key(FWD_FIBER, "loss_coef")] == 0.3
        assert changes[_key(FWD_FIBER, "con_in")] is REVERT
        assert changes[_key(FWD_FIBER, "failed")] is REVERT

    def test_wrapped_restconf_body_is_unwrapped(self, gnpy_context: TapiContext) -> None:
        nodes = _nodes(gnpy_context, f'/ElementList/"{FWD_FIBER}"/loss_coef')
        assert build_edit(nodes, "merge", {"twinlight-transparent:loss_coef": 0.3}) == {
            _key(FWD_FIBER, "loss_coef"): 0.3,
        }
        assert build_edit(nodes, "merge", 0.3) == {_key(FWD_FIBER, "loss_coef"): 0.3}

    def test_state_leaves_are_read_only(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(TreeError, match="read-only") as exc:
            build_edit(_nodes(gnpy_context, f'/ElementList/"{FWD_FIBER}"'),
                       "merge", {"length_km": 100})
        assert exc.value.status == 403

    def test_unknown_child_in_body(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(TreeError, match="no child named 'loss'"):
            build_edit(_nodes(gnpy_context, "/ElementList/*/$Fiber"), "merge", {"loss": 1})

    def test_validation_collects_every_error(self, gnpy_context: TapiContext) -> None:
        errors = validate_changes(gnpy_context, {
            _key(FWD_FIBER, "loss_coef"): 5.0,
            _key(EAST_AMP, "gain_target"): True,
            _key(FWD_FIBER, "failed"): "yes",
            LeafKey("config", "", "rmsa.k_shortest_paths"): 0,
        })
        assert len(errors) == 4
        assert {e.path for e in errors} == {
            f"/ElementList/{FWD_FIBER}/loss_coef", f"/ElementList/{EAST_AMP}/gain_target",
            f"/ElementList/{FWD_FIBER}/failed", "/Config/rmsa/k_shortest_paths",
        }

    def test_refused_change_set_writes_nothing(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(ChangeError):
            apply_changes(gnpy_context, {
                _key(FWD_FIBER, "loss_coef"): 0.3, _key(REV_FIBER, "loss_coef"): 9.0,
            })
        assert gnpy_context._backend.read_attribute(FWD_FIBER, "loss_coef") == pytest.approx(0.2)
        assert gnpy_context.revision == 0


# -- Running revision, startup values and revert --------------------------------------


class TestRevisionAndRevert:
    async def test_revision_counts_changes_from_any_interface(
        self, gnpy_context: TapiContext,
    ) -> None:
        r0 = gnpy_context.revision
        await _service(gnpy_context)  # services are not configuration
        assert gnpy_context.revision == r0
        gnpy_context.apply_element_overrides({FWD_FIBER: {"loss_coef": 0.3}})  # /config/set
        gnpy_context.apply_twin_overrides({"rmsa.qot_margin_db": 2.0})
        gnpy_context.set_link_failed(FWD_FIBER, True)
        assert gnpy_context.revision == r0 + 3

    def test_revert_restores_startup_and_drops_the_record(
        self, gnpy_context: TapiContext,
    ) -> None:
        apply_changes(gnpy_context, {_key(FWD_FIBER, "con_in"): 2.0,
                                     _key(FWD_FIBER, "failed"): True,
                                     LeafKey("config", "", "rmsa.qot_margin_db"): 3.0})
        apply_changes(gnpy_context, build_edit(_nodes(gnpy_context, "/"), "remove"))
        assert gnpy_context._backend.read_attribute(FWD_FIBER, "con_in") == pytest.approx(0.5)
        assert gnpy_context.get_element_overrides() == {}
        assert gnpy_context.get_failed_links() == set()
        assert gnpy_context._config.rmsa.qot_margin_db == 1.5
        assert gnpy_context.get_twin_overrides() == {}

    def test_revert_puts_back_an_unset_value(self, gnpy_context: TapiContext) -> None:
        amp = gnpy_context._backend.uid_map[EAST_AMP]
        amp.tilt_target = amp.operational.tilt_target = None
        apply_changes(gnpy_context, {_key(EAST_AMP, "tilt_target"): 0.5})
        apply_changes(gnpy_context, {_key(EAST_AMP, "tilt_target"): REVERT})
        assert amp.tilt_target is None and amp.operational.tilt_target is None

    def test_restore_reverts_overrides_it_does_not_reapply(
        self, gnpy_twin_config: Any, tmp_path: Path,
    ) -> None:
        """Regression: restore used to forget overrides without undoing them."""
        clean = tmp_path / "clean.json"
        TapiContext(gnpy_twin_config).snapshot(clean)
        ctx = TapiContext(gnpy_twin_config)
        ctx.apply_element_overrides({FWD_FIBER: {"loss_coef": 0.3}})
        ctx.apply_twin_overrides({"rmsa.qot_margin_db": 4.0})
        ctx.restore_from(clean)
        assert ctx._backend.read_attribute(FWD_FIBER, "loss_coef") == pytest.approx(0.2)
        assert ctx._config.rmsa.qot_margin_db == 1.5


# -- Candidate datastore and compare ---------------------------------------------------


class TestCandidate:
    async def test_candidate_view_and_compare(self, gnpy_context: TapiContext) -> None:
        apply_changes(gnpy_context, {_key(REV_FIBER, "loss_coef"): 0.22})
        gnpy_context.candidate.stage({
            _key(FWD_FIBER, "loss_coef"): 0.3,
            _key(REV_FIBER, "loss_coef"): REVERT,
        }, gnpy_context.revision)
        leaf = _nodes(gnpy_context, f'/ElementList/"{FWD_FIBER}"/loss_coef')[0]
        assert await source_for(gnpy_context, "candidate").value(leaf) == 0.3
        assert await source_for(gnpy_context, "running").value(leaf) == pytest.approx(0.2)

        edits = await compare(gnpy_context, "running", "candidate")
        assert [(e["operation"], e["target"]) for e in edits] == [
            ("replace", f"/ElementList/{FWD_FIBER}/loss_coef"),
            ("remove", f"/ElementList/{REV_FIBER}/loss_coef"),
        ]
        assert edits[0]["value"] == {"loss_coef": 0.3}
        assert edits[1]["source-value"] == {"loss_coef": 0.22}

    async def test_compare_running_to_startup(self, gnpy_context: TapiContext) -> None:
        assert await compare(gnpy_context, "running", "startup") == []
        apply_changes(gnpy_context, {_key(FWD_FIBER, "failed"): True})
        edits = await compare(gnpy_context, "running", "startup")
        assert edits == [{
            "edit-id": "1", "operation": "remove",
            "target": f"/ElementList/{FWD_FIBER}/failed",
            "source-value": {"failed": True},
        }]

    def test_later_stage_wins_and_moves_last(self, gnpy_context: TapiContext) -> None:
        cand = gnpy_context.candidate
        cand.stage({_key(FWD_FIBER, "loss_coef"): 0.3, _key(EAST_AMP, "p_max"): 20}, 0)
        cand.stage({_key(FWD_FIBER, "loss_coef"): 0.4}, 0)
        assert list(cand.edits.values()) == [20, 0.4]
        assert cand.revision == 2 and cand.base_revision == 0
        cand.discard()
        assert cand.edits == {} and cand.base_revision is None


# -- QoT what-if ------------------------------------------------------------------------


class TestQotPreview:
    async def test_prediction_matches_the_committed_result_and_changes_nothing(
        self, gnpy_context: TapiContext,
    ) -> None:
        uuid = await _service(gnpy_context)
        before = (await gnpy_context.get_or_compute_baseline(
            gnpy_context.get_service(uuid))).gsnr_db
        revision = gnpy_context.revision
        changes = {_key(FWD_FIBER, "loss_coef"): 0.3, _key(EAST_AMP, "p_max"): 15.0}

        [report] = await preview_changes(gnpy_context, changes)

        assert report["service"] == uuid
        assert report["current"]["gsnr_db"] == pytest.approx(before, abs=1e-3)
        assert report["delta_gsnr_db"] < 0
        # Nothing moved: values, cache, revision.
        assert gnpy_context._backend.read_attribute(FWD_FIBER, "loss_coef") == pytest.approx(0.2)
        assert gnpy_context._backend.read_attribute(EAST_AMP, "p_max") == 23.0
        assert uuid in gnpy_context._baseline_cache
        assert gnpy_context.revision == revision

        apply_changes(gnpy_context, changes)
        after = (await gnpy_context.get_or_compute_baseline(
            gnpy_context.get_service(uuid))).gsnr_db
        assert report["predicted"]["gsnr_db"] == pytest.approx(after, abs=1e-3)

    async def test_failure_and_margin_flags(self, gnpy_context: TapiContext) -> None:
        await _service(gnpy_context)
        [cut] = await preview_changes(gnpy_context, {_key(FWD_FIBER, "failed"): True})
        assert cut["predicted"]["status"] == "link-failed"
        assert cut["above_required"] is False
        assert gnpy_context.get_failed_links() == set()

        margin = LeafKey("config", "", "rmsa.qot_margin_db")
        [tight] = await preview_changes(gnpy_context, {
            _key(FWD_FIBER, "con_in"): 0.6, margin: 20.0,
        })
        assert tight["qot_margin_db"] == 20.0
        assert tight["above_required"] is True and tight["meets_admission_margin"] is False

    async def test_untouched_services_are_not_reported(
        self, gnpy_context: TapiContext,
    ) -> None:
        await _service(gnpy_context)
        # Brest → Morlaix traffic never crosses the reverse span.
        assert await preview_changes(gnpy_context, {_key(REV_FIBER, "loss_coef"): 0.5}) == []


# -- A topology whose UIDs contain "/" --------------------------------------------------


@pytest.fixture
def split_span_context(gnpy_twin_config: Any, tmp_path: Path) -> TapiContext:
    """200 km spans, which GNPy's design splits into ``…_(1/2)`` sub-spans."""
    data = json.loads(Path(gnpy_twin_config.gnpy.topology).read_text())
    for el in data["elements"]:
        if el["type"] == "Fiber":
            el["params"]["length"] = 200
    topo = tmp_path / "long.json"
    topo.write_text(json.dumps(data))
    config = gnpy_twin_config.model_copy(deep=True)
    config.gnpy.topology = topo
    return TapiContext(config)


def test_sub_span_uids_are_addressable(split_span_context: TapiContext) -> None:
    ctx = split_span_context
    sub = f"{FWD_FIBER}_(1/2)"
    assert sub in ctx._backend.uid_map
    assert [n.path[-2] for n in _nodes(ctx, f'/ElementList/"{sub}"/loss_coef')] == [sub]
    link = _nodes(ctx, "/LinkList/roadm Brest/roadm Morlaix/ElementList/*/$Fiber")
    assert [n.path[-1] for n in link] == [f"{FWD_FIBER}_(1/2)", f"{FWD_FIBER}_(2/2)"]
