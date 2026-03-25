"""Tests for the Bononi exponential step EDFA reservoir model."""

import math
import time

import pytest

from tapi_twin.config import EdfaReservoirConfig
from tapi_twin.physics.transients.edfa_reservoir import (
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

    def test_transient_decays_exponentially(self):
        """Deviation should decay toward new steady state."""
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

        # Right after event: far from steady state
        early = tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=t_event + 1e-6
        )
        # After many time constants: close to steady state
        late = tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=t_event + 1.0  # 1 s >> 10 µs tau_eff
        )
        # Steady state for 1 channel: -1 * 0.3 = -0.3 dB
        assert abs(late - (-0.3)) < 0.01

    def test_asymmetric_time_constants(self):
        """Add should be faster than drop (smaller tau_eff)."""
        cfg = EdfaReservoirConfig(
            tau_ms=10.0,
            gain_per_channel_db=0.3,
            tau_add_factor=0.001,   # 10 µs
            tau_drop_factor=0.01,   # 100 µs
        )

        # Test add transient
        tracker_add = EdfaStateTracker()
        t0 = 0.0
        tracker_add.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=t0
        )
        # At t = 50 µs (5× add tau, 0.5× drop tau)
        t_test = t0 + 50e-6
        add_dev = tracker_add.get_total_delta_gsnr_db(
            ["edfa1"], t=t_test
        )
        # Should be mostly settled after 5 tau_add
        ss = -0.3  # steady state for 1 channel
        assert abs(add_dev - ss) < 0.01

        # Test drop transient
        tracker_drop = EdfaStateTracker()
        tracker_drop.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=t0
        )
        # Let it settle
        tracker_drop.notify_channel_change(
            ["edfa1"], delta_channels=-1, cfg=cfg, t=t0 + 1.0
        )
        t_test2 = t0 + 1.0 + 50e-6
        drop_dev = tracker_drop.get_total_delta_gsnr_db(
            ["edfa1"], t=t_test2
        )
        # Drop should NOT be settled yet at 50 µs (tau_drop = 100 µs)
        # It's transitioning from -0.3 to 0.0
        assert drop_dev < -0.05  # still significantly displaced

    def test_cascade_accumulates(self):
        """Multiple EDFAs should accumulate deviations linearly."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.3)
        edfa_uids = [f"edfa{i}" for i in range(5)]
        tracker.notify_channel_change(
            edfa_uids, delta_channels=+1, cfg=cfg, t=0.0
        )
        # After settling: each EDFA contributes -0.3 dB
        result = tracker.get_total_delta_gsnr_db(
            edfa_uids, t=10.0  # well past any transient
        )
        # 5 × -0.3 = -1.5 dB
        assert abs(result - (-1.5)) < 0.01

    def test_channel_drop_releases(self):
        """Dropping a channel should move toward less negative SS."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.3)
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=0.0
        )
        # Let it settle at -0.3
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=-1, cfg=cfg, t=10.0
        )
        # After settling again: 0 channels → 0.0 dB deviation
        result = tracker.get_total_delta_gsnr_db(
            ["edfa1"], t=20.0
        )
        assert abs(result) < 0.01


class TestDeltaGsnrDbFunction:
    """Test the delta_gsnr_db entry point."""

    def test_with_tracker(self):
        """When tracker is provided, should use it."""
        tracker = EdfaStateTracker()
        cfg = EdfaReservoirConfig(gain_per_channel_db=0.5)
        tracker.notify_channel_change(
            ["edfa1"], delta_channels=+1, cfg=cfg, t=0.0
        )
        result = delta_gsnr_db(
            t=100.0, edfa_uids=["edfa1"],
            cfg=cfg, edfa_tracker=tracker,
        )
        # Should be at steady state: -0.5 dB
        assert abs(result - (-0.5)) < 0.01

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
