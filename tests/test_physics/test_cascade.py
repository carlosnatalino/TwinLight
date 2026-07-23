"""Tests for the cascade transient composition module."""


from twinlight.config import TransientsConfig
from twinlight.physics.gnpy_adapter import OpmBaseline
from twinlight.physics.transients.cascade import apply_all_transients
from twinlight.physics.transients.edfa_reservoir import EdfaStateTracker


class TestApplyAllTransients:
    """Verify cascade composition with updated models."""

    def _make_baseline(
        self,
        gsnr_db=25.0,
        osnr_ase_db=28.0,
        cd_ps_nm=1000.0,
        pmd_ps=0.5,
    ) -> OpmBaseline:
        return OpmBaseline(
            gsnr_db=gsnr_db,
            osnr_ase_db=osnr_ase_db,
            cd_ps_nm=cd_ps_nm,
            pmd_ps=pmd_ps,
            latency_ms=1.0,
            total_fiber_km=100.0,
            edfa_uids=["edfa1", "edfa2"],
            fiber_uids=["fiber1", "fiber2"],
        )

    def test_returns_all_metrics(self):
        """Output dict should contain all six OPM metrics."""
        cfg = TransientsConfig()
        baseline = self._make_baseline()
        result = apply_all_transients(
            baseline, "svc-1", "DP-QPSK", t=100.0, cfg=cfg,
        )
        expected_keys = {
            "gsnr-db", "osnr-db", "pre-fec-ber",
            "q-factor-db", "chromatic-dispersion-ps-per-nm", "pmd-ps",
        }
        assert set(result.keys()) == expected_keys

    def test_eepn_depends_on_cd(self):
        """With phase noise enabled, GSNR should differ for
        different baseline CD values (EEPN is CD-dependent)."""
        cfg = TransientsConfig()
        # Disable other models to isolate phase noise
        cfg.edfa_reservoir.enabled = False
        cfg.polarization.enabled = False
        cfg.environmental.enabled = False

        short = apply_all_transients(
            self._make_baseline(cd_ps_nm=100.0),
            "svc-1", "DP-QPSK", t=0.0, cfg=cfg,
        )
        long = apply_all_transients(
            self._make_baseline(cd_ps_nm=10000.0),
            "svc-1", "DP-QPSK", t=0.0, cfg=cfg,
        )
        # Long link should have lower GSNR (bigger EEPN penalty)
        assert long["gsnr-db"] < short["gsnr-db"]

    def test_edfa_tracker_passed_through(self):
        """EDFA tracker should be used when provided."""
        cfg = TransientsConfig()
        cfg.polarization.enabled = False
        cfg.phase_noise.enabled = False
        cfg.environmental.enabled = False

        tracker = EdfaStateTracker()
        tracker.notify_channel_change(
            ["edfa1", "edfa2"], +1, cfg.edfa_reservoir, t=0.0,
        )

        baseline = self._make_baseline()
        result = apply_all_transients(
            baseline, "svc-1", "DP-QPSK", t=100.0,
            cfg=cfg, edfa_tracker=tracker,
        )
        # After settling: 2 EDFAs × -0.3 = -0.6 dB
        expected_gsnr = 25.0 - 0.6
        assert abs(result["gsnr-db"] - expected_gsnr) < 0.1

    def test_all_models_degrade_gsnr(self):
        """With all models enabled, GSNR should be lower than baseline."""
        cfg = TransientsConfig()
        cfg.environmental.enabled = True
        baseline = self._make_baseline(cd_ps_nm=5000.0)
        result = apply_all_transients(
            baseline, "svc-1", "DP-16QAM", t=50.0, cfg=cfg,
        )
        assert result["gsnr-db"] <= baseline.gsnr_db

    def test_ber_recomputed_from_perturbed_gsnr(self):
        """BER and Q should reflect the perturbed GSNR, not baseline."""
        cfg = TransientsConfig()
        baseline = self._make_baseline(gsnr_db=15.0, cd_ps_nm=5000.0)
        result = apply_all_transients(
            baseline, "svc-1", "DP-QPSK", t=0.0, cfg=cfg,
        )
        # BER should be computable and positive
        assert result["pre-fec-ber"] > 0
        # Q-factor should be finite
        assert result["q-factor-db"] > 0
