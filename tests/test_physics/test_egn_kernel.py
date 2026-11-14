"""Unit tests for the GN-model kernel and analytical-metric helpers."""

from __future__ import annotations

import math

import pytest

from twinlight.physics.analytical_metrics import (
    chromatic_dispersion_ps_per_nm,
    latency_ms,
    pmd_ps,
)
from twinlight.physics.egn_kernel import (
    SpanInputs,
    accumulate_path_noise,
    db_per_km_to_neper_per_m,
    nf_db_to_linear,
    per_span_ase_power,
    per_span_self_nli_power,
)


class TestUnitConversions:
    def test_loss_db_per_km_to_neper_per_m(self) -> None:
        # Returns the FIELD attenuation coefficient: 1 dB/km = ln(10)/10 Np/km
        # of *power*, halved to the field convention the kernel uses, then
        # /1000 for per-metre.
        expected = math.log(10) / 10.0 / 1000.0 / 2.0
        assert db_per_km_to_neper_per_m(1.0) == pytest.approx(expected)
        # Zero loss is exact zero.
        assert db_per_km_to_neper_per_m(0.0) == 0.0

    def test_span_power_loss_matches_db(self) -> None:
        # The whole point of the field convention: a span's power loss is
        # exp(2·α·L), which must equal the operator's dB/km × length.
        alpha = db_per_km_to_neper_per_m(0.2)  # 0.2 dB/km
        power_loss_db = 10.0 * math.log10(math.exp(2.0 * alpha * 90_000.0))
        assert power_loss_db == pytest.approx(18.0)  # 0.2 dB/km × 90 km

    def test_nf_db_to_linear(self) -> None:
        assert nf_db_to_linear(0.0) == pytest.approx(1.0)
        assert nf_db_to_linear(3.0) == pytest.approx(10 ** 0.3)
        assert nf_db_to_linear(10.0) == pytest.approx(10.0)


class TestPerSpan:
    def _span(self) -> SpanInputs:
        return SpanInputs(
            length_m=80_000.0,
            attenuation_neper_per_m=db_per_km_to_neper_per_m(0.2),
            noise_figure_linear=nf_db_to_linear(5.0),
        )

    def test_ase_positive(self) -> None:
        ase = per_span_ase_power(self._span(), 32e9, 193.1e12)
        assert ase > 0.0

    def test_ase_scales_with_loss(self) -> None:
        # Higher fiber loss → larger amp gain → more ASE.
        s1 = self._span()
        s2 = SpanInputs(
            length_m=s1.length_m,
            attenuation_neper_per_m=db_per_km_to_neper_per_m(0.4),
            noise_figure_linear=s1.noise_figure_linear,
        )
        assert per_span_ase_power(s2, 32e9, 193.1e12) > per_span_ase_power(
            s1, 32e9, 193.1e12
        )

    def test_nli_positive(self) -> None:
        nli = per_span_self_nli_power(self._span(), 1e-3, 32e9)
        assert nli > 0.0

    def test_nli_scales_with_launch_power_cubed(self) -> None:
        # GN model: NLI ∝ P^3. Halving P should give ~1/8 the NLI.
        span = self._span()
        nli_1 = per_span_self_nli_power(span, 2e-3, 32e9)
        nli_2 = per_span_self_nli_power(span, 1e-3, 32e9)
        assert nli_1 / nli_2 == pytest.approx(8.0, rel=1e-6)

    def test_zero_length_span_has_zero_nli(self) -> None:
        span = SpanInputs(
            length_m=0.0,
            attenuation_neper_per_m=db_per_km_to_neper_per_m(0.2),
            noise_figure_linear=nf_db_to_linear(5.0),
        )
        assert per_span_self_nli_power(span, 1e-3, 32e9) == 0.0


class TestAccumulate:
    def test_empty_path_is_ideal(self) -> None:
        qot = accumulate_path_noise(
            [], launch_power_w=1e-3, bandwidth_hz=32e9,
            centre_frequency_hz=193.1e12,
        )
        assert qot.p_ase_w == 0.0
        assert qot.p_nli_w == 0.0
        assert qot.gsnr_db == float("inf")

    def test_two_spans_double_noise(self) -> None:
        span = SpanInputs(
            length_m=80_000.0,
            attenuation_neper_per_m=db_per_km_to_neper_per_m(0.2),
            noise_figure_linear=nf_db_to_linear(5.0),
        )
        one = accumulate_path_noise(
            [span], launch_power_w=1e-3, bandwidth_hz=32e9,
            centre_frequency_hz=193.1e12,
        )
        two = accumulate_path_noise(
            [span, span], launch_power_w=1e-3, bandwidth_hz=32e9,
            centre_frequency_hz=193.1e12,
        )
        # Linear noise-power accumulation → exactly 2×.
        assert two.p_ase_w == pytest.approx(2.0 * one.p_ase_w, rel=1e-9)
        assert two.p_nli_w == pytest.approx(2.0 * one.p_nli_w, rel=1e-9)
        # GSNR drops by ~3 dB.
        assert two.gsnr_db == pytest.approx(one.gsnr_db - 3.01, abs=0.05)

    def test_zero_launch_power_rejected(self) -> None:
        with pytest.raises(ValueError, match="launch_power_w must be positive"):
            accumulate_path_noise(
                [], launch_power_w=0.0, bandwidth_hz=32e9,
                centre_frequency_hz=193.1e12,
            )


class TestAnalyticalMetrics:
    def test_cd_linear_in_length(self) -> None:
        assert chromatic_dispersion_ps_per_nm(100.0) == pytest.approx(1670.0)
        assert chromatic_dispersion_ps_per_nm(0.0) == 0.0

    def test_pmd_sqrt_law(self) -> None:
        # √100 = 10; default coef 0.04 → 0.4 ps.
        assert pmd_ps(100.0) == pytest.approx(0.4, rel=1e-6)
        assert pmd_ps(0.0) == 0.0
        with pytest.raises(ValueError):
            pmd_ps(-1.0)

    def test_latency_speed_of_light(self) -> None:
        # 1 km / (c/n_eff) → ~4.9 µs ≈ 0.0049 ms.
        ms = latency_ms(1.0)
        assert 0.0048 < ms < 0.0050


class TestCrossChannelNli:
    """The cross-channel term is GNPy's gn_model_analytic (Poggiolini 2014,
    arXiv:1209.0394 Eq. 120/123), with this kernel's field attenuation."""

    @staticmethod
    def _span():
        from twinlight.physics.egn_kernel import SpanInputs, db_per_km_to_neper_per_m

        return SpanInputs(length_m=80e3, attenuation_neper_per_m=db_per_km_to_neper_per_m(0.2),
                          noise_figure_linear=10 ** 0.5)

    @pytest.mark.parametrize("offset_hz", [50e9, -50e9, 200e9, 1.5e12])
    def test_matches_gnpy_psi(self, offset_hz: float) -> None:
        import numpy as np
        from gnpy.core.science_utils import NliSolver

        from twinlight.physics import egn_kernel as k

        span = self._span()
        power_alpha = 2 * span.attenuation_neper_per_m  # GNPy's α is the power one
        l_eff = NliSolver.effective_length(power_alpha, span.length_m)
        baud = np.array([32e9, 32e9])
        df = np.array([[0.0, offset_hz], [-offset_hz, 0.0]])
        psi = NliSolver._psi(df, baud, np.full(2, -k._BETA_2_S2_PER_M), l_eff, 1 / power_alpha)
        gamma = k._GAMMA_PER_W_PER_M
        eta = gamma ** 2 * NliSolver.XPM_WEIGHT * psi[0, 1] / (baud[0] * baud[1] ** 2) * baud[0]
        p_cut, p_pump = 1e-3, 2e-3
        expected = eta * p_cut * p_pump ** 2

        ours = k.per_span_cross_channel_nli_power(
            span, p_cut, 32e9, [k.Neighbour(offset_hz, p_pump, 32e9)],
        )
        assert ours == pytest.approx(expected, rel=1e-9)

    def test_loaded_line_is_worse_and_closer_neighbours_hurt_more(self) -> None:
        from twinlight.physics import egn_kernel as k

        spans = [self._span()] * 5
        alone = k.accumulate_path_noise(spans, launch_power_w=1e-3, bandwidth_hz=32e9,
                                        centre_frequency_hz=193.1e12)
        near = k.accumulate_path_noise(spans, launch_power_w=1e-3, bandwidth_hz=32e9,
                                       centre_frequency_hz=193.1e12,
                                       neighbours=[k.Neighbour(50e9, 1e-3, 32e9)])
        far = k.accumulate_path_noise(spans, launch_power_w=1e-3, bandwidth_hz=32e9,
                                      centre_frequency_hz=193.1e12,
                                      neighbours=[k.Neighbour(1e12, 1e-3, 32e9)])
        assert alone.gsnr_db > far.gsnr_db > near.gsnr_db
        assert alone.osnr_ase_db == near.osnr_ase_db  # neighbours add NLI, not ASE

    def test_ase_is_evaluated_at_the_channel_frequency(self) -> None:
        import math

        from twinlight.physics import egn_kernel as k

        spans = [self._span()] * 3
        low = k.accumulate_path_noise(spans, launch_power_w=1e-3, bandwidth_hz=32e9,
                                      centre_frequency_hz=191.5e12)
        high = k.accumulate_path_noise(spans, launch_power_w=1e-3, bandwidth_hz=32e9,
                                       centre_frequency_hz=195.0e12)
        assert low.osnr_ase_db - high.osnr_ase_db == pytest.approx(
            10 * math.log10(195.0 / 191.5))
