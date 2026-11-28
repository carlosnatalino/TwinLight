"""Tests for the EDFA reservoir model: Sun et al.'s fractional swing, the
bounded cascade and its speed-up, relaxing to the designed operating point."""

import math

import pytest

from twinlight.config import EdfaReservoirConfig
from twinlight.physics.transients.edfa_reservoir import (
    EdfaStateTracker,
    delta_gsnr_db,
    load_step_swing_db,
)

# τ_e = τ · factor: 10 µs for an add, 100 µs for a drop (the defaults).
_CFG = EdfaReservoirConfig(tau_ms=10.0, tau_add_factor=0.001, tau_drop_factor=0.01)
_TAU_ADD = 10e-6
_TAU_DROP = 100e-6


def _loaded(uids: list[str], channels: int, *, t: float = 0.0) -> EdfaStateTracker:
    """A tracker whose ``uids`` carry ``channels`` lightpaths, long settled."""
    tracker = EdfaStateTracker()
    for _ in range(channels):
        tracker.notify_channel_change(uids, +1, _CFG, t=t)
    return tracker


class TestLoadStepSwing:
    """Sun, Zyskind & Srivastava, IEEE JSTQE 3(4):991 (1997), § Single EDFA:
    the surviving-channel swing follows N_before / N_after."""

    def test_one_channel_onto_a_full_comb(self):
        # 87 → 88: the ≈0.05 dB the old 0.3 dB/channel overstated sixfold.
        assert load_step_swing_db(87, 88) == pytest.approx(10 * math.log10(87 / 88))
        assert abs(load_step_swing_db(87, 88)) < 0.05

    def test_add_is_negative_and_drop_positive(self):
        assert load_step_swing_db(1, 2) == pytest.approx(-10 * math.log10(2))
        assert load_step_swing_db(2, 1) == pytest.approx(10 * math.log10(2))

    def test_no_surviving_channel_no_swing(self):
        assert load_step_swing_db(0, 1) == 0.0
        assert load_step_swing_db(1, 0) == 0.0

    def test_the_same_fraction_is_the_same_swing(self):
        assert load_step_swing_db(10, 20) == pytest.approx(load_step_swing_db(40, 80))


class TestSingleAmplifier:
    def test_no_events_gives_zero(self):
        assert EdfaStateTracker().get_total_delta_gsnr_db(["edfa1", "edfa2"], t=100.0) == 0.0

    def test_the_first_channel_perturbs_nothing(self):
        tracker = _loaded(["edfa1"], 1)
        assert tracker.get_total_delta_gsnr_db(["edfa1"], t=1e-9) == 0.0

    def test_an_add_compresses_and_relaxes_to_the_operating_point(self):
        """Deviation decays back to zero, not to a standing offset.

        The QoT baseline already represents the amplifier's designed
        operating point, so a settled EDFA must contribute 0 dB. A
        non-zero steady state would double-count the loading and, summed
        over a long cascade, swamp the baseline.
        """
        tracker = _loaded(["edfa1"], 3)
        tracker.notify_channel_change(["edfa1"], +1, _CFG, t=10.0)
        peak = tracker.get_total_delta_gsnr_db(["edfa1"], t=10.0)
        assert peak == pytest.approx(10 * math.log10(3 / 4))
        one_tau = tracker.get_total_delta_gsnr_db(["edfa1"], t=10.0 + _TAU_ADD)
        assert one_tau == pytest.approx(peak / math.e)
        assert abs(tracker.get_total_delta_gsnr_db(["edfa1"], t=11.0)) < 1e-12

    def test_a_drop_overshoots_more_slowly(self):
        tracker = _loaded(["edfa1"], 4)
        tracker.notify_channel_change(["edfa1"], -1, _CFG, t=10.0)
        peak = tracker.get_total_delta_gsnr_db(["edfa1"], t=10.0)
        assert peak == pytest.approx(10 * math.log10(4 / 3))
        # Bononi-Rusch Eq. 29: τ_e of a drop is ten times that of an add here.
        assert tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=10.0 + _TAU_DROP
        ) == pytest.approx(peak / math.e)

    def test_a_multi_channel_step_follows_the_ratio(self):
        tracker = _loaded(["edfa1"], 4)
        tracker.notify_channel_change(["edfa1"], +4, _CFG, t=10.0)
        assert tracker.get_total_delta_gsnr_db(["edfa1"], t=10.0) == pytest.approx(
            10 * math.log10(4 / 8)
        )

    def test_back_to_back_events_compose(self):
        tracker = _loaded(["edfa1"], 2)
        tracker.notify_channel_change(["edfa1"], +1, _CFG, t=10.0)
        tracker.notify_channel_change(["edfa1"], +1, _CFG, t=10.0 + _TAU_ADD)
        expected = 10 * math.log10(2 / 3) / math.e + 10 * math.log10(3 / 4)
        assert tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=10.0 + _TAU_ADD
        ) == pytest.approx(expected)

    def test_repeated_adds_do_not_accumulate_a_standing_offset(self):
        tracker = EdfaStateTracker()
        for i in range(10):
            tracker.notify_channel_change(["edfa1"], +1, _CFG, t=float(i))
        assert abs(tracker.get_total_delta_gsnr_db(["edfa1"], t=100.0)) < 1e-12

    def test_drop_below_zero_channels_is_not_an_event(self):
        tracker = EdfaStateTracker()
        tracker.notify_channel_change(["edfa1"], -1, _CFG, t=0.0)
        assert tracker.get_total_delta_gsnr_db(["edfa1"], t=1e-9) == 0.0

    def test_before_the_event_there_is_no_excursion(self):
        tracker = _loaded(["edfa1"], 2)
        tracker.notify_channel_change(["edfa1"], +1, _CFG, t=10.0)
        assert tracker.get_total_delta_gsnr_db(["edfa1"], t=9.0) == 0.0


class TestCascade:
    _ROUTE = tuple(f"edfa{i}" for i in range(40))

    def test_one_channel_on_a_40_amplifier_route_is_bounded_by_one_swing(self):
        # Sun, Zyskind & Srivastava, IEEE JSTQE 1997: a saturated
        # amplifier's surviving-channel swing is 10·log10(N_before/N_after),
        # and along a chain carrying the same channels the overshoot
        # "asymptotically approaches, but never exceed[s] a certain value".
        # One channel onto 87 on 40 EDFAs: −0.05 dB, not 40 × −0.3 = −12 dB.
        route = list(self._ROUTE)
        tracker = _loaded(route, 87)
        tracker.notify_channel_change(route, +1, _CFG, t=10.0)
        peak = tracker.get_total_delta_gsnr_db(route, t=10.0)
        bound = 10 * math.log10(87 / 88)
        assert peak == pytest.approx(bound)
        assert abs(peak) <= abs(bound) + 1e-12

    def test_links_whose_loads_changed_differently_add(self):
        # Two links of 20 amplifiers: the channel's share is reset where it
        # meets the second link's different companions, so the swings add.
        route = list(self._ROUTE)
        first, second = route[:20], route[20:]
        tracker = _loaded(first, 3)
        for _ in range(7):
            tracker.notify_channel_change(second, +1, _CFG, t=0.0)
        tracker.notify_channel_change(route, +1, _CFG, t=10.0)
        assert tracker.get_total_delta_gsnr_db(route, t=10.0) == pytest.approx(
            10 * math.log10(3 / 4) + 10 * math.log10(7 / 8)
        )

    def test_amplifiers_the_event_missed_do_not_swing(self):
        route = list(self._ROUTE)
        tracker = _loaded(route, 5)
        tracker.notify_channel_change(route[:10], +1, _CFG, t=10.0)
        assert tracker.get_total_delta_gsnr_db(route, t=10.0) == pytest.approx(
            10 * math.log10(5 / 6)
        )

    def test_a_longer_cascade_relaxes_faster(self):
        # Sun et al., Electron. Lett. 33(4):313 (1997): the transient's rate
        # scales linearly with the number of EDFAs, so τ = τ_e / M.
        long = list(self._ROUTE)
        tracker = _loaded(long, 9)
        tracker.notify_channel_change(long, +1, _CFG, t=10.0)
        peak = 10 * math.log10(9 / 10)
        for route in (long[:4], long):
            assert tracker.get_total_delta_gsnr_db(route, t=10.0) == pytest.approx(peak)
            assert tracker.get_total_delta_gsnr_db(
                route, t=10.0 + _TAU_ADD / len(route),
            ) == pytest.approx(peak / math.e)
        at = 10.0 + _TAU_ADD / 10
        assert abs(tracker.get_total_delta_gsnr_db(long, t=at)) < abs(
            tracker.get_total_delta_gsnr_db(long[:4], t=at)
        )

    def test_the_whole_cascade_settles(self):
        route = list(self._ROUTE)
        tracker = _loaded(route, 3)
        tracker.notify_channel_change(route, -1, _CFG, t=10.0)
        assert tracker.get_total_delta_gsnr_db(route, t=10.0) > 0
        assert abs(tracker.get_total_delta_gsnr_db(route, t=11.0)) < 1e-12


class TestDeltaGsnrDbFunction:
    """Test the delta_gsnr_db entry point."""

    def test_reads_the_tracker(self):
        tracker = _loaded(["edfa1"], 1)
        tracker.notify_channel_change(["edfa1"], +1, _CFG, t=10.0)
        assert delta_gsnr_db(10.0, ["edfa1"], tracker) == pytest.approx(-10 * math.log10(2))
        assert abs(delta_gsnr_db(100.0, ["edfa1"], tracker)) < 1e-12

    def test_an_empty_tracker_is_no_change(self):
        # There is no stateless fallback: no event recorded, no excursion.
        assert delta_gsnr_db(0.5, ["edfa1", "edfa2"], EdfaStateTracker()) == 0.0


class TestAdmissionsDriveTheTracker:
    """The context counts lit lightpaths per amplifier and notifies the
    tracker on each admission, under both backends."""

    def test_a_fourth_lightpath_on_one_route_swings_it_by_one_ratio(self, mesh_context):
        import asyncio

        from tests.conftest import MeshRefs, tapi_end_point
        from twinlight.models.connectivity import ConnectivityService

        refs = MeshRefs(mesh_context)
        services = []
        for _ in range(4):
            svc = ConnectivityService.model_validate({"end-point": [
                tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C")),
            ]})
            asyncio.run(mesh_context.add_service(svc))
            services.append(svc)
        paths = {tuple(mesh_context._allocated_path_uids(s.uuid)) for s in services}
        assert len(paths) == 1, "the four lightpaths should share one route"

        baseline = asyncio.run(mesh_context.get_or_compute_baseline(services[0]))
        tracker = mesh_context.edfa_tracker
        t_event = max(ev.event_time for ev in tracker._last_event.values())
        # Every link of the route carries the same four lightpaths: one run,
        # one swing of 10·log10(3/4), however many amplifiers (Sun et al. 1997).
        assert len(baseline.edfa_uids) > 1
        assert tracker.get_total_delta_gsnr_db(baseline.edfa_uids, t_event) == pytest.approx(
            10 * math.log10(3 / 4)
        )
