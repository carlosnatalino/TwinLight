"""Tests for the PDL penalty formula (Lichtman 1995)."""

import math

import pytest

from tapi_twin.config import PolarizationConfig
from tapi_twin.physics.transients.polarization import (
    delta_gsnr_from_pdl_db,
    delta_pmd_ps,
)


def _fixed_phase(_uid: str, _metric: str) -> float:
    return 0.0


class TestPdlPenalty:
    """Verify the corrected PDL penalty formula."""

    def test_zero_pdl_gives_zero_penalty(self):
        """No PDL elements → no penalty."""
        cfg = PolarizationConfig(
            pdl_per_element_db=0.0,
            pdl_variation_amplitude=0.0,
        )
        result = delta_gsnr_from_pdl_db(
            t=0.0, all_uids=["edfa1", "edfa2"],
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert result == 0.0

    def test_empty_uids_gives_zero(self):
        """No elements on path → no penalty."""
        cfg = PolarizationConfig()
        result = delta_gsnr_from_pdl_db(
            t=0.0, all_uids=[],
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert result == 0.0

    def test_penalty_is_negative(self):
        """PDL penalty should always degrade GSNR (negative)."""
        cfg = PolarizationConfig(
            pdl_per_element_db=0.1,
            pdl_variation_amplitude=0.0,
        )
        result = delta_gsnr_from_pdl_db(
            t=0.0, all_uids=["e1", "e2", "e3", "e4", "e5"],
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert result < 0.0

    def test_penalty_uses_linear_conversion(self):
        """Verify the penalty uses Lichtman's linear PDL ratio formula.

        For total_pdl_db ≈ 0.224 dB (sqrt(5) × 0.1 dB):
        pdl_lin_power = 10^(0.224/10) ≈ 1.0528
        gamma_lin = (1.0528 - 1) / (1.0528 + 1) ≈ 0.0257
        penalty = -10·log10(1 / (1 - 0.0257²/3)) ≈ -0.00096 dB
        """
        cfg = PolarizationConfig(
            pdl_per_element_db=0.1,
            pdl_variation_amplitude=0.0,
        )
        result = delta_gsnr_from_pdl_db(
            t=0.0, all_uids=["e1", "e2", "e3", "e4", "e5"],
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        # Small PDL: penalty should be very small
        assert -0.01 < result < 0.0

    def test_penalty_grows_with_more_elements(self):
        """More elements → larger PDL → bigger penalty."""
        cfg = PolarizationConfig(
            pdl_per_element_db=0.2,
            pdl_variation_amplitude=0.0,
        )
        small = delta_gsnr_from_pdl_db(
            t=0.0, all_uids=["e1", "e2"],
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        large = delta_gsnr_from_pdl_db(
            t=0.0, all_uids=[f"e{i}" for i in range(20)],
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert large < small  # more negative

    def test_large_pdl_penalty(self):
        """With high PDL (e.g. 2 dB total), penalty should be non-trivial."""
        cfg = PolarizationConfig(
            pdl_per_element_db=2.0,
            pdl_variation_amplitude=0.0,
        )
        result = delta_gsnr_from_pdl_db(
            t=0.0, all_uids=["e1"],
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        # 2 dB PDL: gamma_lin ≈ 0.227, penalty ≈ -0.075 dB
        assert -0.5 < result < -0.01


class TestPmdDrift:
    """Verify PMD drift model."""

    def test_zero_amplitude_gives_zero(self):
        cfg = PolarizationConfig(pmd_drift_amplitude=0.0)
        result = delta_pmd_ps(
            t=0.0, fiber_uids=["f1", "f2"],
            baseline_pmd_ps=1.0, cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert result == 0.0

    def test_positive_result(self):
        """PMD drift should always add to baseline (positive)."""
        cfg = PolarizationConfig(pmd_drift_amplitude=0.1)
        result = delta_pmd_ps(
            t=10.0, fiber_uids=["f1", "f2"],
            baseline_pmd_ps=1.0, cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert result >= 0.0
