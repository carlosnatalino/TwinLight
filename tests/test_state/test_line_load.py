"""Co-propagating lightpaths and the operational state of a lightpath.

Under the EGN backend the line carries the admitted lightpaths, not an
assumed full comb: a channel's neighbours are the lightpaths sharing a fiber
with it, each adding cross-channel NLI in the fibers both traverse. So
lighting a channel lowers the GSNR of the channels it co-propagates with,
and removing it restores them.

A lightpath whose GSNR falls below what its format needs — because of a new
neighbour, a parameter change or a failure — is reported
``operational-state: DISABLED`` (T-API: "unable to meet the SLA"), and
``ENABLED`` again once it recovers. The new neighbour itself is admitted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.models.common import OperationalState
from twinlight.models.connectivity import OTSIA_CSEP_SPEC, ConnectivityService
from twinlight.physics.egn_kernel import Neighbour, cross_channel_nli_power, dbm_to_w
from twinlight.physics.modulation import ModulationFormat, get_params
from twinlight.state.context import TapiContext

_SVC = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"
_REQUIRED_64QAM = get_params(ModulationFormat.DP_64QAM).req_gsnr_db


@pytest.fixture
def egn(tmp_path: Path) -> TapiContext:
    return TapiContext(_mesh_config(tmp_path, "egn"))


def _admit(
    ctx: TapiContext, a: str, z: str, *, fmt: str = "DP-QPSK",
    start_slot: int | None = None, power_centi_dbm: float | None = None,
) -> ConnectivityService:
    refs = MeshRefs(ctx)
    end_points = [tapi_end_point("a", refs.sip(a), fmt), tapi_end_point("z", refs.sip(z), fmt)]
    if power_centi_dbm is not None:
        for ep in end_points:
            ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0][
                "channel-output-power"] = power_centi_dbm
    svc = ConnectivityService.model_validate({"end-point": end_points})
    if start_slot is not None:
        svc.requested_spectrum_hz = ctx._slot_range_hz(
            start_slot, ctx._block_width(svc.modulation_format))
    asyncio.run(ctx.add_service(svc))
    return svc


def _gsnr(ctx: TapiContext, svc: ConnectivityService) -> float:
    baseline = asyncio.run(ctx.get_or_compute_baseline(svc))
    assert baseline is not None
    return baseline.gsnr_db


def _inverse(db: float) -> float:
    return 10 ** (-db / 10)


class TestCoPropagatingChannels:
    def test_a_co_propagating_channel_lowers_the_gsnr(self, egn: TapiContext) -> None:
        cut = _admit(egn, "trx A", "trx C")
        alone = asyncio.run(egn.get_or_compute_baseline(cut))
        assert alone is not None

        neighbour = _admit(egn, "trx A", "trx C")
        loaded = asyncio.run(egn.get_or_compute_baseline(cut))
        assert loaded is not None
        assert loaded.gsnr_db < alone.gsnr_db
        # A neighbour adds NLI, not ASE.
        assert loaded.osnr_ase_db == pytest.approx(alone.osnr_ase_db)

        egn.delete_service(neighbour.uuid)
        assert _gsnr(egn, cut) == pytest.approx(alone.gsnr_db, abs=1e-9)

    def test_each_channel_counts_only_on_the_fibers_it_shares(self, egn: TapiContext) -> None:
        """A neighbour on A→B and one on B→C each share one span with the
        A→C channel under test; their NLI adds, and a lightpath running the
        other way (C→A, the opposite fibers) is no neighbour at all."""
        cut = _admit(egn, "trx A", "trx C", start_slot=100)
        alone = _inverse(_gsnr(egn, cut))

        def increment(*routes: tuple[str, str]) -> float:
            others = [_admit(egn, a, z, start_slot=120) for a, z in routes]
            value = _inverse(_gsnr(egn, cut)) - alone
            for svc in others:
                egn.delete_service(svc.uuid)
            return value

        first, second = increment(("trx A", "trx B")), increment(("trx B", "trx C"))
        both = increment(("trx A", "trx B"), ("trx B", "trx C"))
        assert first > 0 and second > 0
        assert both == pytest.approx(first + second, rel=1e-9)
        assert increment(("trx C", "trx A")) == pytest.approx(0.0, abs=1e-15)

    def test_the_increment_is_the_cross_channel_nli_on_shared_fibers(
        self, egn: TapiContext,
    ) -> None:
        """Recompute the neighbour's NLI from the kernel over exactly the
        fibers both routes traverse: the baseline moves by that, no more."""
        cut = _admit(egn, "trx A", "trx C", start_slot=100)
        alone = _inverse(_gsnr(egn, cut))
        neighbour = _admit(egn, "trx B", "trx C", start_slot=140, power_centi_dbm=300)
        loaded = _inverse(_gsnr(egn, cut))

        be = egn._backend
        (link_id,) = be._links_for_path(egn._allocated_path_uids(neighbour.uuid))
        cut_offset_db = egn._service_launch_power[cut.uuid] - be.spectral_defaults.power_dbm
        their_offset_db = 3.0 - be.spectral_defaults.power_dbm
        df = neighbour.central_frequency_hz - cut.central_frequency_hz
        baud = get_params(cut.modulation_format).baud_rate_hz
        expected = 0.0
        for fiber in be._link_plan(link_id).fibers:
            cut_w = dbm_to_w(fiber.input_dbm + cut_offset_db)
            pump = Neighbour(df, dbm_to_w(fiber.input_dbm + their_offset_db), baud)
            expected += cross_channel_nli_power(fiber.span, cut_w, baud, [pump]) / cut_w
        assert loaded - alone == pytest.approx(expected, rel=1e-9)

    @pytest.mark.parametrize(("near", "far"), [
        ({"start_slot": 109}, {"start_slot": 300}),                    # spacing
        ({"power_centi_dbm": 400}, {"power_centi_dbm": 0}),           # power
    ])
    def test_closer_and_stronger_neighbours_hurt_more(
        self, egn: TapiContext, near: dict, far: dict,
    ) -> None:
        cut = _admit(egn, "trx A", "trx C", start_slot=100)

        def gsnr_with(options: dict) -> float:
            other = _admit(egn, "trx A", "trx C", **{"start_slot": 109, **options})
            value = _gsnr(egn, cut)
            egn.delete_service(other.uuid)
            return value

        assert gsnr_with(near) < gsnr_with(far)

    def test_admission_sees_the_lit_line(self, egn: TapiContext) -> None:
        """The newcomer is evaluated with the existing lightpaths lit, and
        its cached baseline is what a recomputation gives."""
        _admit(egn, "trx A", "trx C")
        newcomer = _admit(egn, "trx A", "trx C")
        at_admission = egn._baseline_cache[newcomer.uuid].gsnr_db
        egn.invalidate_baseline(newcomer.uuid)
        assert _gsnr(egn, newcomer) == pytest.approx(at_admission)

    def test_gnpy_assumes_a_fully_loaded_line(self, mesh_gnpy_context: TapiContext) -> None:
        """The GNPy backend propagates the SI comb on every path, so another
        lightpath does not change the result (it takes a comb slot)."""
        ctx = mesh_gnpy_context
        cut = _admit(ctx, "trx A", "trx C", start_slot=100)
        before = _gsnr(ctx, cut)
        _admit(ctx, "trx A", "trx C", start_slot=300)
        assert _gsnr(ctx, cut) == pytest.approx(before)


def _zero_margin(ctx: TapiContext) -> None:
    """Admit at the format threshold, so a small drop fails the lightpath."""
    ctx.apply_twin_overrides({"rmsa.qot_margin_db": 0.0})


class TestOperationalState:
    def test_neighbours_push_a_lightpath_below_its_threshold(self, egn: TapiContext) -> None:
        """Admit B, fail A: each high-power neighbour is admitted, and the
        DP-64QAM lightpath they degrade goes DISABLED once its GSNR is
        below 20.5 dB; removing them brings it back."""
        _zero_margin(egn)
        cut = _admit(egn, "trx A", "trx C", fmt="DP-64QAM", start_slot=100)
        assert _gsnr(egn, cut) >= _REQUIRED_64QAM
        assert cut.operational_state == OperationalState.ENABLED

        neighbours = []
        for slot in (109, 91, 118, 82, 127, 73):
            neighbours.append(_admit(egn, "trx A", "trx C", start_slot=slot,
                                     power_centi_dbm=900))
            if _gsnr(egn, cut) < _REQUIRED_64QAM:
                break
        assert _gsnr(egn, cut) < _REQUIRED_64QAM, "the neighbours never pushed it below"
        # add_service re-evaluated it, without anyone reading its OPM.
        assert cut.operational_state == OperationalState.DISABLED
        assert all(n.operational_state == OperationalState.ENABLED for n in neighbours)

        for neighbour in neighbours:
            egn.delete_service(neighbour.uuid)
        asyncio.run(egn.refresh_operational_states())
        assert cut.operational_state == OperationalState.ENABLED

    def test_the_admission_margin_is_not_the_failure_threshold(self, egn: TapiContext) -> None:
        """Eating into rmsa.qot_margin_db is not a failure: the lightpath
        still meets its format's requirement."""
        _zero_margin(egn)
        cut = _admit(egn, "trx A", "trx C", fmt="DP-64QAM")
        egn.apply_twin_overrides({"rmsa.qot_margin_db": 5.0})
        asyncio.run(egn.refresh_operational_states())
        assert cut.operational_state == OperationalState.ENABLED

    def test_a_parameter_change_below_threshold_disables(self, mesh_context: TapiContext) -> None:
        ctx = mesh_context
        _zero_margin(ctx)
        cut = _admit(ctx, "trx A", "trx C", fmt="DP-64QAM")
        ctx.apply_element_overrides({"fiber (A → B)": {"loss_coef": 0.3}})
        asyncio.run(ctx.refresh_operational_states())
        assert _gsnr(ctx, cut) < _REQUIRED_64QAM
        assert cut.operational_state == OperationalState.DISABLED

        ctx.apply_element_overrides({"fiber (A → B)": {"loss_coef": 0.2}})
        asyncio.run(ctx.refresh_operational_states())
        assert cut.operational_state == OperationalState.ENABLED

    def test_a_failed_fiber_disables(self, mesh_context: TapiContext) -> None:
        ctx = mesh_context
        cut = _admit(ctx, "trx A", "trx C")
        ctx.set_link_failed("fiber (B → C)", True)
        asyncio.run(ctx.refresh_operational_states())
        assert cut.operational_state == OperationalState.DISABLED
        ctx.set_link_failed("fiber (B → C)", False)
        asyncio.run(ctx.refresh_operational_states())
        assert cut.operational_state == OperationalState.ENABLED

    def test_t_api_reports_it(self, tmp_path: Path) -> None:
        """A GET shows the state the current QoT implies, after a write
        nobody has read the OPM of."""
        client = TestClient(create_app(_mesh_config(tmp_path, "egn")))
        ctx = client.app.state.context
        _zero_margin(ctx)
        refs = MeshRefs(ctx)
        created = client.post(_SVC, json={"tapi-connectivity:connectivity-service": [{
            "end-point": [tapi_end_point("a", refs.sip("trx A"), "DP-64QAM"),
                          tapi_end_point("z", refs.sip("trx C"), "DP-64QAM")],
        }]})
        assert created.status_code == 201, created.text
        uuid = created.json()["tapi-connectivity:connectivity-service"]["uuid"]

        ctx.apply_element_overrides({"fiber (A → B)": {"loss_coef": 0.3}})
        one = client.get(f"{_SVC}={uuid}").json()["tapi-connectivity:connectivity-service"]
        assert one["operational-state"] == "DISABLED"

        ctx.apply_element_overrides({"fiber (A → B)": {"loss_coef": 0.2}})
        listed = client.get(_SVC).json()["tapi-connectivity:connectivity-service"]
        assert listed[0]["operational-state"] == "ENABLED"


class TestAFailedLightpath:
    """A lightpath whose route is cut still emits: it is a neighbour on the
    fibers before the cut, and not after it."""

    def test_dark_past_the_cut(self, egn: TapiContext) -> None:
        """B→C channel under test; neighbour A→C over A-B-C. Cutting A→B
        (upstream of the shared span) takes the neighbour off B→C."""
        cut = _admit(egn, "trx B", "trx C", start_slot=100)
        alone = _gsnr(egn, cut)
        neighbour = _admit(egn, "trx A", "trx C", start_slot=109)
        assert "roadm B" in egn._allocated_path_uids(neighbour.uuid)
        loaded = _gsnr(egn, cut)
        assert loaded < alone

        egn.set_link_failed("fiber (A → B)", True)
        assert _gsnr(egn, cut) == pytest.approx(alone, abs=1e-9)
        egn.set_link_failed("fiber (A → B)", False)
        assert _gsnr(egn, cut) == pytest.approx(loaded, abs=1e-9)

    def test_lit_before_the_cut(self, egn: TapiContext) -> None:
        """A→B channel under test; the same neighbour, cut downstream on
        B→C, still lights A→B."""
        cut = _admit(egn, "trx A", "trx B", start_slot=100)
        _admit(egn, "trx A", "trx C", start_slot=109)
        loaded = _gsnr(egn, cut)
        egn.set_link_failed("fiber (B → C)", True)
        assert _gsnr(egn, cut) == pytest.approx(loaded, abs=1e-9)

    def test_repair_after_a_restore(self, egn: TapiContext, tmp_path: Path) -> None:
        """A lightpath restored on a cut route has no propagated baseline, only
        the failure sentinel; repairing the cut must still re-evaluate the
        lightpaths it lights again."""
        cut = _admit(egn, "trx B", "trx C", start_slot=100)
        neighbour = _admit(egn, "trx A", "trx C", start_slot=109)
        loaded = _gsnr(egn, cut)
        egn.set_link_failed("fiber (A → B)", True)
        dark = _gsnr(egn, cut)
        assert dark > loaded

        snapshot = egn.snapshot(tmp_path / "cut.json")
        egn.restore_from(snapshot)
        assert _gsnr(egn, egn._services[cut.uuid]) == pytest.approx(dark, abs=1e-9)
        assert asyncio.run(egn.get_or_compute_baseline(
            egn._services[neighbour.uuid])).status == "link-failed"

        egn.set_link_failed("fiber (A → B)", False)
        assert _gsnr(egn, egn._services[cut.uuid]) == pytest.approx(loaded, abs=1e-9)
