"""Tests for the PDL OSNR penalty — Zarkosvky-Shtaif hinge model (Opt. Lett.
45(5):1224, 2020, Eq. 3-5) — and the PMD drift model."""

import math
from typing import ClassVar

import numpy as np
import pytest

from twinlight.config import PolarizationConfig
from twinlight.physics.transients.polarization import (
    _hinge_period_s,
    _maxwell_pdl_db,
    _pdl_db_to_gamma,
    delta_osnr_from_pdl_db,
    delta_pmd_ps,
)


def _fixed_phase(_uid: str, _metric: str) -> float:
    return 0.0


# Ordered (uid, kind) hinge lists, as produced by OpmBaseline.pdl_elements.
def _roadms(n: int) -> list[tuple[str, str]]:
    return [(f"roadm{i}", "Roadm") for i in range(n)]


class TestPdlDbToGamma:
    """Verify the Eq. 3 inversion ρ_dB → γ."""

    def test_zero_pdl_gives_zero_gamma(self):
        assert _pdl_db_to_gamma(0.0) == 0.0

    def test_known_value(self):
        """1.4 dB → γ = (10^0.14 - 1)/(10^0.14 + 1) ≈ 0.160."""
        assert abs(_pdl_db_to_gamma(1.4) - 0.1598) < 1e-3

    def test_gamma_in_unit_range(self):
        for pdl_db in (0.1, 0.5, 1.4, 3.0, 10.0):
            gamma = _pdl_db_to_gamma(pdl_db)
            assert 0.0 < gamma < 1.0


class TestMaxwellPdl:
    """Verify the UID-seeded Maxwell-distributed per-hinge PDL draw."""

    def test_zero_mean_gives_zero(self):
        assert _maxwell_pdl_db(0.0, "roadm0") == 0.0

    def test_deterministic_per_uid(self):
        """Same UID → same draw (stable for the service lifetime)."""
        assert _maxwell_pdl_db(0.5, "roadm0") == _maxwell_pdl_db(0.5, "roadm0")

    def test_distinct_uids_differ(self):
        """Different hinges draw different PDL values."""
        values = {_maxwell_pdl_db(0.5, f"roadm{i}") for i in range(20)}
        assert len(values) == 20

    def test_positive_draw(self):
        for i in range(50):
            assert _maxwell_pdl_db(0.5, f"roadm{i}") > 0.0

    def test_sample_mean_matches_configured_mean(self):
        """The configured value is the mean of the Maxwell distribution."""
        n = 4000
        mean_cfg = 0.5
        sample = [_maxwell_pdl_db(mean_cfg, f"h{i}") for i in range(n)]
        sample_mean = sum(sample) / n
        assert abs(sample_mean - mean_cfg) < 0.08 * mean_cfg

    def test_upper_tail_truncated(self):
        """No draw exceeds 4× the configured mean (Miotto OFC 2025)."""
        mean_cfg = 0.5
        for i in range(4000):
            assert _maxwell_pdl_db(mean_cfg, f"h{i}") <= 4.0 * mean_cfg


class TestHingePeriod:
    """Verify the per-hinge incommensurate SOP-drift period."""

    def test_deterministic_per_uid(self):
        assert _hinge_period_s("roadm0", 300.0) == _hinge_period_s(
            "roadm0", 300.0
        )

    def test_within_detuning_band(self):
        """Each period stays within base × (1 ± 25%)."""
        for i in range(100):
            period = _hinge_period_s(f"h{i}", 300.0)
            assert 0.75 * 300.0 <= period <= 1.25 * 300.0

    def test_distinct_uids_differ(self):
        """Hinges get mutually incommensurate periods."""
        periods = {_hinge_period_s(f"h{i}", 300.0) for i in range(20)}
        assert len(periods) == 20


class TestPdlOsnrPenalty:
    """Verify the hinge-model PDL-induced OSNR penalty."""

    def test_zero_pdl_gives_zero_penalty(self):
        """No PDL on any hinge → no penalty."""
        cfg = PolarizationConfig(pdl_per_roadm_db=0.0, pdl_per_edfa_db=0.0)
        result = delta_osnr_from_pdl_db(
            t=0.0, pdl_elements=_roadms(3),
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert result == 0.0

    def test_empty_hinges_gives_zero(self):
        """No hinges on path → no penalty."""
        result = delta_osnr_from_pdl_db(
            t=0.0, pdl_elements=[],
            cfg=PolarizationConfig(), _phase_fn=_fixed_phase,
        )
        assert result == 0.0

    def test_tx_distribution_gives_zero(self):
        """ASE at Tx crosses the same hinges as the signal → OSNR conserved."""
        cfg = PolarizationConfig(ase_distribution="tx")
        result = delta_osnr_from_pdl_db(
            t=123.0, pdl_elements=_roadms(5),
            cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert result == 0.0

    def test_penalty_fluctuates_around_zero(self):
        """As the SOP drifts the penalty swings both negative and positive."""
        cfg = PolarizationConfig(
            pdl_per_roadm_db=0.7, ase_distribution="rx",
            sop_drift_period_s=300.0,
        )
        samples = [
            delta_osnr_from_pdl_db(
                t=t, pdl_elements=_roadms(4),
                cfg=cfg, _phase_fn=_fixed_phase,
            )
            for t in range(0, 600, 5)
        ]
        assert min(samples) < 0.0      # adverse alignment → OSNR loss
        assert max(samples) > 0.0      # favourable alignment → OSNR gain

    def test_single_hinge_rx_within_eq5_bounds(self):
        """For one hinge under 'rx', ΔOSNR stays within ±ρ_dB/2 — the
        decibel form of the Eq. 5 attenuation bounds [l, 1/l].  ρ_dB is the
        hinge's *drawn* Maxwell PDL, not the configured mean."""
        cfg = PolarizationConfig(
            pdl_per_roadm_db=0.8, ase_distribution="rx",
            sop_drift_period_s=300.0,
        )
        drawn_pdl_db = _maxwell_pdl_db(cfg.pdl_per_roadm_db, "roadm0")
        bound = drawn_pdl_db / 2.0
        for t in range(0, 600, 3):
            result = delta_osnr_from_pdl_db(
                t=t, pdl_elements=_roadms(1),
                cfg=cfg, _phase_fn=_fixed_phase,
            )
            assert -bound - 1e-9 <= result <= bound + 1e-9

    def test_swing_grows_with_more_hinges(self):
        """More hinges → larger peak-to-peak OSNR swing."""
        cfg = PolarizationConfig(
            pdl_per_roadm_db=0.5, ase_distribution="rx",
            sop_drift_period_s=300.0,
        )

        def swing(n: int) -> float:
            s = [
                delta_osnr_from_pdl_db(
                    t=t, pdl_elements=_roadms(n),
                    cfg=cfg, _phase_fn=_fixed_phase,
                )
                for t in range(0, 600, 5)
            ]
            return max(s) - min(s)

        assert swing(10) > swing(2)

    def test_rx_swing_exceeds_distributed(self):
        """'rx' (all noise at receiver) is the worst case; 'distributed'
        noise injection partly shares the PDL and shrinks the swing."""
        hinges = [
            ("roadm0", "Roadm"), ("edfa0", "Edfa"),
            ("roadm1", "Roadm"), ("edfa1", "Edfa"),
            ("roadm2", "Roadm"), ("edfa2", "Edfa"),
        ]

        def swing(distribution: str) -> float:
            cfg = PolarizationConfig(
                pdl_per_roadm_db=0.7, pdl_per_edfa_db=0.1,
                ase_distribution=distribution, sop_drift_period_s=300.0,
            )
            s = [
                delta_osnr_from_pdl_db(
                    t=t, pdl_elements=hinges,
                    cfg=cfg, _phase_fn=_fixed_phase,
                )
                for t in range(0, 600, 5)
            ]
            return max(s) - min(s)

        assert swing("rx") > swing("distributed") > 0.0


class TestPmdDrift:
    """The path DGD is the quadrature sum of drifting per-fiber DGDs, τᵢ ∝ √Lᵢ
    (Gordon & Kogelnik, PNAS 2000, Sec. III); its drift is signed and has the
    configured period."""

    _FIBERS: ClassVar[list[str]] = [f"fiber-{i}" for i in range(32)]

    def _series(self, cfg: PolarizationConfig, fibers: list[str], **kwargs) -> np.ndarray:
        """ΔPMD over three periods, 200 samples per period, with hash phases."""
        period = cfg.pmd_drift_period_s
        times = np.arange(0.0, 3 * period, period / 200)
        return np.array([
            delta_pmd_ps(t, fibers, 2.134, cfg, **kwargs) for t in times
        ])

    def test_zero_amplitude_gives_zero(self):
        cfg = PolarizationConfig(pmd_drift_amplitude=0.0)
        result = delta_pmd_ps(
            t=0.0, fiber_uids=["f1", "f2"],
            baseline_pmd_ps=1.0, cfg=cfg, _phase_fn=_fixed_phase,
        )
        assert result == 0.0

    def test_takes_both_signs_over_one_period(self):
        # The old model returned √(Σ drift²), never below the baseline.
        drift = self._series(PolarizationConfig(), self._FIBERS)
        assert drift.min() < 0.0 < drift.max()

    def test_dominant_period_is_the_configured_one(self):
        # The old model's sin² ran at twice the configured frequency.
        cfg = PolarizationConfig(pmd_drift_period_s=90.0)
        drift = self._series(cfg, self._FIBERS)
        spectrum = np.abs(np.fft.rfft(drift - drift.mean()))
        frequencies = np.fft.rfftfreq(drift.size, d=cfg.pmd_drift_period_s / 200)
        dominant = frequencies[1:][spectrum[1:].argmax()]
        assert 1.0 / dominant == pytest.approx(cfg.pmd_drift_period_s, rel=1e-6)

    def test_a_single_fiber_moves_by_exactly_the_amplitude(self):
        cfg = PolarizationConfig(pmd_drift_amplitude=0.1)
        drift = self._series(cfg, ["only-fiber"]) / 2.134
        assert drift.max() == pytest.approx(0.1, abs=1e-4)
        assert drift.min() == pytest.approx(-0.1, abs=1e-4)

    def test_in_phase_fibers_move_together_by_the_amplitude(self):
        # With equal phases every τᵢ scales by the same factor, so the
        # quadrature sum does too, whatever the lengths.
        cfg = PolarizationConfig(pmd_drift_amplitude=0.1)
        quarter = cfg.pmd_drift_period_s / 4
        result = delta_pmd_ps(
            quarter, ["a", "b", "c"], 2.0, cfg,
            fiber_lengths_km=[10.0, 80.0, 120.0], _phase_fn=_fixed_phase,
        )
        assert result == pytest.approx(0.2)

    def test_a_longer_fiber_weighs_more(self):
        # τᵢ ∝ √Lᵢ: at T/4 fiber "long" (phase 0) is at its peak and "short"
        # (phase 3π/2) at a zero crossing, so only the long fiber has moved.
        cfg = PolarizationConfig(pmd_drift_amplitude=0.1)

        def phases(uid: str, _metric: str) -> float:
            return 0.0 if uid == "long" else 1.5 * math.pi

        quarter = cfg.pmd_drift_period_s / 4
        result = delta_pmd_ps(
            quarter, ["long", "short"], 1.0, cfg,
            fiber_lengths_km=[90.0, 10.0], _phase_fn=phases,
        )
        expected = math.sqrt(0.9 * 1.1 ** 2 + 0.1) - 1.0
        assert result == pytest.approx(expected)

    def test_unknown_lengths_mean_equal_sections(self):
        cfg = PolarizationConfig()
        equal = delta_pmd_ps(17.0, self._FIBERS, 2.0, cfg)
        assert delta_pmd_ps(
            17.0, self._FIBERS, 2.0, cfg, fiber_lengths_km=[50.0] * 32,
        ) == pytest.approx(equal)
        # A length list that does not match the fibers is ignored.
        assert delta_pmd_ps(
            17.0, self._FIBERS, 2.0, cfg, fiber_lengths_km=[50.0, 60.0],
        ) == pytest.approx(equal)

    def test_no_fiber_no_drift(self):
        assert delta_pmd_ps(5.0, [], 2.0, PolarizationConfig()) == 0.0
