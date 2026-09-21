"""Tests for the Bononi exponential step EDFA reservoir model."""

from twinlight.config import EdfaReservoirConfig
from twinlight.physics.transients.edfa_reservoir import (
    EdfaStateTracker,
    delta_gsnr_db,
)


def _fixed_phase(_uid: str, _metric: str) -> float:
    return 0.0


class TestEdfaStateTracker:
    """Verify the stateful Bononi reservoir model."""

    def test_no_events_gives_zero(self):
        """Without any channel events, deviation should be zero."""
        tracker = EdfaStateTracker()
        result = tracker.get_total_delta_gsnr_db(
            ["edfa1", "edfa2"], t=100.0
        )
        assert result == 0.0

    def test_channel_add_creates_transient(self):
        """Adding a channel should create a non-zero transient."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig()
        t_event = 1000.0
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=t_event
        )
        # Immediately after: should be in transient
        result = tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=t_event + 1e-6
        )
        assert result != 0.0

    def test_transient_decays_to_designed_operating_point(self):
        """Deviation decays back to zero, not to a standing offset.

        The QoT baseline already represents the amplifier's designed
        operating point, so a settled EDFA must contribute 0 dB. A
        non-zero steady state would double-count the loading and, summed
        over a long cascade, swamp the baseline (the -9 dB Abilene→Atlanta
        regression this test guards).
        """
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(
            tau_ms=10.0,
            gain_per_channel_db=0.3,
            tau_add_factor=0.001,
        )
        t_event = 1000.0
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=t_event
        )

        # Right after the event: the full excursion, gain compressed.
        early = tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=t_event + 1e-9
        )
        assert abs(early - (-0.3)) < 0.01

        # After many time constants: relaxed back to the baseline.
        late = tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=t_event + 1.0  # 1 s >> 10 µs tau_eff
        )
        assert abs(late) < 0.01
        assert abs(early) > abs(late)

    def test_asymmetric_time_constants(self):
        """Add should relax faster than drop (smaller tau_eff)."""
        cfg = EdfaReservoirConfig(
            tau_ms=10.0,
            gain_per_channel_db=0.3,
            tau_add_factor=0.001,   # 10 µs
            tau_drop_factor=0.01,   # 100 µs
        )

        # Add transient: settled after 5 tau_add.
        tracker_add = EdfaStateTracker()
        t0 = 0.0
        tracker_add.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=t0
        )
        add_dev = tracker_add.get_total_delta_gsnr_db(
            ["edfa1"], t=t0 + 50e-6   # 5× add tau, 0.5× drop tau
        )
        assert abs(add_dev) < 0.01

        # Drop transient from a settled amplifier: at 50 µs only half a
        # tau_drop has elapsed, so a sizeable positive excursion remains.
        tracker_drop = EdfaStateTracker()
        tracker_drop.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=t0
        )
        tracker_drop.notify_channel_change(
            ["edfa1"], delta_channels=-1, cfg=cfg, t=t0 + 1.0
        )
        drop_dev = tracker_drop.get_total_delta_gsnr_db(
            ["edfa1"], t=t0 + 1.0 + 50e-6
        )
        # Dropping a channel lets gain overshoot: positive excursion.
        assert drop_dev > 0.05

    def test_cascade_accumulates(self):
        """Multiple EDFAs accumulate excursions linearly in dB."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.3)
        edfa_uids = [f"edfa{i}" for i in range(5)]
        tracker.notify_channel_change(
            edfa_uids, delta_channels=+1, cfg=cfg, t=0.0
        )
        # At the event, each EDFA contributes -0.3 dB: 5 × -0.3 = -1.5 dB
        # (Sun 1997 cascade accumulation).
        peak = tracker.get_total_delta_gsnr_db(edfa_uids, t=1e-9)
        assert abs(peak - (-1.5)) < 0.01

        # Well past the transient the whole cascade is back at baseline.
        settled = tracker.get_total_delta_gsnr_db(edfa_uids, t=10.0)
        assert abs(settled) < 0.01

    def test_multi_channel_step_scales_excursion(self):
        """Excursion scales with the size of the load step."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.3)
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=+4, cfg=cfg, t=0.0
        )
        peak = tracker.get_total_delta_gsnr_db(["edfa1"], t=1e-9)
        assert abs(peak - (-1.2)) < 0.01

    def test_repeated_adds_do_not_accumulate_a_standing_offset(self):
        """Successive settled adds must not stack into a permanent penalty.

        This is the shape of the original defect: each admitted service
        pushed the steady state further negative, so N services on the
        same amplifiers read N × 0.3 dB low forever.
        """
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.3)
        for i in range(10):
            tracker.notify_channel_change(
                ["edfa1"], delta_channels=+1, cfg=cfg, t=float(i),
            )
        assert abs(tracker.get_total_delta_gsnr_db(["edfa1"], t=100.0)) < 0.01

    def test_channel_drop_releases(self):
        """Dropping a channel settles back to zero deviation."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.3)
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=0.0
        )
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=-1, cfg=cfg, t=10.0
        )
        result = tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=20.0
        )
        assert abs(result) < 0.01

    def test_drop_below_zero_channels_is_not_an_event(self):
        """A drop that the zero-clamp absorbs perturbs nothing."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.3)
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=-1, cfg=cfg, t=0.0
        )
        assert tracker.get_total_delta_gsnr_db(["edfa1"], t=1e-9) == 0.0


class TestDeltaGsnrDbFunction:
    """Test the delta_gsnr_db entry point."""

    def test_with_tracker(self):
        """When tracker is provided, should use it."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.5)
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=0.0
        )
        # At the event: the full -0.5 dB excursion.
        assert abs(
            delta_gsnr_db(
                t=1e-9, edfa_uids=["edfa1"],
                cfg=cfg, edfa_tracker=tracker,
            ) - (-0.5)
        ) < 0.01
        # Long after: relaxed to the designed operating point.
        assert abs(
            delta_gsnr_db(
                t=100.0, edfa_uids=["edfa1"],
                cfg=cfg, edfa_tracker=tracker,
            )
        ) < 0.01

    def test_without_tracker_uses_sinusoidal(self):
        """Without tracker, should fall back to sinusoidal model."""
        cfg = EdfaReservoirConfig(
            gain_drift_amp_db=0.3,
            drift_period_multiplier=100.0,
        )
        result = delta_gsnr_db(
            t=0.5, edfa_uids=["edfa1", "edfa2"],
            cfg=cfg, edfa_tracker=None, _phase_fn=_fixed_phase,
        )
        # Should produce a non-zero sinusoidal value
        assert isinstance(result, float)
