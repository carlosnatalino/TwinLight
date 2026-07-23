"""Tests for the EEPN phase noise model (Shieh-Ho 2008)."""



from twinlight.config import PhaseNoiseConfig
from twinlight.physics.transients.phase_noise import delta_gsnr_db


def _fixed_phase(_uid: str, _metric: str) -> float:
    return 0.0


class TestEepnModel:
    """Verify the Shieh-Ho EEPN model correctness."""

    def test_zero_cd_gives_zero_penalty(self):
        """With zero accumulated CD, EEPN should be negligible."""
        cfg = PhaseNoiseConfig(
            tx_linewidth_hz=100_000,
            lo_linewidth_hz=100_000,
            linewidth_variation_amplitude=0.0,
        )
        result = delta_gsnr_db(
            t=0.0, service_uuid="test", baud_rate_hz=32e9,
            cfg=cfg, accumulated_cd_ps_nm=0.0, gsnr_db=25.0,
            _phase_fn=_fixed_phase,
        )
        assert result == 0.0

    def test_penalty_grows_with_cd(self):
        """Longer link (more CD) should give larger EEPN penalty."""
        cfg = PhaseNoiseConfig(
            tx_linewidth_hz=100_000,
            lo_linewidth_hz=100_000,
            linewidth_variation_amplitude=0.0,
        )
        short_link = delta_gsnr_db(
            t=0.0, service_uuid="test", baud_rate_hz=32e9,
            cfg=cfg, accumulated_cd_ps_nm=100.0, gsnr_db=25.0,
            _phase_fn=_fixed_phase,
        )
        long_link = delta_gsnr_db(
            t=0.0, service_uuid="test", baud_rate_hz=32e9,
            cfg=cfg, accumulated_cd_ps_nm=1000.0, gsnr_db=25.0,
            _phase_fn=_fixed_phase,
        )
        # Both negative (penalties)
        assert short_link < 0
        assert long_link < 0
        # Long link has larger penalty (more negative)
        assert long_link < short_link

    def test_penalty_depends_on_lo_linewidth_only(self):
        """EEPN depends on LO linewidth, not Tx linewidth."""
        cfg_wide_lo = PhaseNoiseConfig(
            tx_linewidth_hz=100_000,
            lo_linewidth_hz=500_000,
            linewidth_variation_amplitude=0.0,
        )
        cfg_wide_tx = PhaseNoiseConfig(
            tx_linewidth_hz=500_000,
            lo_linewidth_hz=100_000,
            linewidth_variation_amplitude=0.0,
        )
        penalty_wide_lo = delta_gsnr_db(
            t=0.0, service_uuid="test", baud_rate_hz=32e9,
            cfg=cfg_wide_lo, accumulated_cd_ps_nm=500.0, gsnr_db=25.0,
            _phase_fn=_fixed_phase,
        )
        penalty_wide_tx = delta_gsnr_db(
            t=0.0, service_uuid="test", baud_rate_hz=32e9,
            cfg=cfg_wide_tx, accumulated_cd_ps_nm=500.0, gsnr_db=25.0,
            _phase_fn=_fixed_phase,
        )
        # Wider LO linewidth → bigger penalty (more negative)
        assert penalty_wide_lo < penalty_wide_tx

    def test_realistic_penalty_magnitude(self):
        """1000 km link at 100 kHz LO should give ~0.5-2 dB penalty."""
        cfg = PhaseNoiseConfig(
            tx_linewidth_hz=100_000,
            lo_linewidth_hz=100_000,
            linewidth_variation_amplitude=0.0,
        )
        # ~1000 km SSMF: CD ≈ 17 ps/(nm·km) × 1000 km = 17000 ps/nm
        penalty = delta_gsnr_db(
            t=0.0, service_uuid="test", baud_rate_hz=32e9,
            cfg=cfg, accumulated_cd_ps_nm=17000.0, gsnr_db=25.0,
            _phase_fn=_fixed_phase,
        )
        assert -5.0 < penalty < -0.1, (
            f"Expected penalty ~0.5-2 dB for 1000 km, got {-penalty:.2f} dB"
        )

    def test_negative_always(self):
        """Phase noise penalty should always be negative (degrades GSNR)."""
        cfg = PhaseNoiseConfig(linewidth_variation_amplitude=0.0)
        for cd in [10.0, 100.0, 1000.0, 10000.0]:
            result = delta_gsnr_db(
                t=0.0, service_uuid="test", baud_rate_hz=32e9,
                cfg=cfg, accumulated_cd_ps_nm=cd, gsnr_db=20.0,
                _phase_fn=_fixed_phase,
            )
            assert result <= 0.0

    def test_time_variation(self):
        """Linewidth variation should cause time-dependent penalty."""
        cfg = PhaseNoiseConfig(
            lo_linewidth_hz=100_000,
            linewidth_variation_amplitude=0.3,
            linewidth_variation_period_s=120.0,
        )
        p1 = delta_gsnr_db(
            t=0.0, service_uuid="test", baud_rate_hz=32e9,
            cfg=cfg, accumulated_cd_ps_nm=5000.0, gsnr_db=25.0,
            _phase_fn=_fixed_phase,
        )
        p2 = delta_gsnr_db(
            t=30.0, service_uuid="test", baud_rate_hz=32e9,
            cfg=cfg, accumulated_cd_ps_nm=5000.0, gsnr_db=25.0,
            _phase_fn=_fixed_phase,
        )
        assert p1 != p2
