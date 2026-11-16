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
    FiberSpan,
    Neighbour,
    NoiseBudget,
    ase_power,
    cross_channel_nli_power,
    db_per_km_to_neper_per_m,
    dbm_to_w,
    nf_db_to_linear,
    self_channel_nli_power,
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


def _fiber(length_km: float = 80.0, loss_db_per_km: float = 0.2) -> FiberSpan:
    return FiberSpan(length_m=length_km * 1e3,
                     attenuation_neper_per_m=db_per_km_to_neper_per_m(loss_db_per_km))


def _gnpy_eta(fiber: FiberSpan, offset_hz: float, baud: float = 32e9) -> float:
    """NLI coefficient GNPy's gn_model_analytic gives one pump on the CUT:
    NliSolver._gn_analytic with its _psi (Poggiolini 2014 Eqs. 120/123)."""
    import numpy as np
    from gnpy.core.science_utils import NliSolver

    power_alpha = 2 * fiber.attenuation_neper_per_m  # GNPy's α is the power one
    l_eff = NliSolver.effective_length(power_alpha, fiber.length_m)
    bauds = np.array([baud, baud])
    df = np.array([[0.0, offset_hz], [-offset_hz, 0.0]])
    psi = NliSolver._psi(df, bauds, np.full(2, -fiber.beta2_s2_per_m), l_eff, 1 / power_alpha)
    weight = NliSolver.SPM_WEIGHT if offset_hz == 0 else NliSolver.XPM_WEIGHT
    return fiber.gamma_per_w_per_m ** 2 * weight * psi[0, 1] / baud ** 2


class TestAse:
    def test_is_nf_h_nu_b_at_the_amplifier_input(self) -> None:
        """GNPy Edfa.noise_profile: h · B · f · NF, compared with the input."""
        expected = 6.62607015e-34 * 32e9 * 193.1e12 * 10 ** 0.55
        assert ase_power(nf_db_to_linear(5.5), 32e9, 193.1e12) == pytest.approx(expected)

    def test_scales_with_noise_figure_and_bandwidth(self) -> None:
        base = ase_power(2.0, 32e9, 193.1e12)
        assert ase_power(4.0, 32e9, 193.1e12) == pytest.approx(2 * base)
        assert ase_power(2.0, 64e9, 193.1e12) == pytest.approx(2 * base)


class TestSelfChannelNli:
    @pytest.mark.parametrize("length_km", [20.0, 80.0, 120.0])
    def test_matches_gnpy(self, length_km: float) -> None:
        """Eq. 120/123 at Δf = 0: the single-channel closed form with
        asinh((π²/2)|β₂| L_a B²), exactly GNPy's SPM term."""
        fiber = _fiber(length_km)
        ours = self_channel_nli_power(fiber, 1e-3, 32e9)
        assert ours == pytest.approx(_gnpy_eta(fiber, 0.0) * 1e-9, rel=1e-9)

    def test_scales_with_power_cubed(self) -> None:
        fiber = _fiber()
        ratio = self_channel_nli_power(fiber, 2e-3, 32e9) / self_channel_nli_power(fiber, 1e-3, 32e9)
        assert ratio == pytest.approx(8.0)

    def test_uses_the_fiber_nonlinearity(self) -> None:
        fiber = _fiber()
        doubled = FiberSpan(fiber.length_m, fiber.attenuation_neper_per_m,
                            gamma_per_w_per_m=2 * fiber.gamma_per_w_per_m)
        assert self_channel_nli_power(doubled, 1e-3, 32e9) == pytest.approx(
            4 * self_channel_nli_power(fiber, 1e-3, 32e9))

    def test_zero_length_has_no_nli(self) -> None:
        assert self_channel_nli_power(_fiber(0.0), 1e-3, 32e9) == 0.0


class TestCrossChannelNli:
    @pytest.mark.parametrize("offset_hz", [50e9, -50e9, 200e9, 1.5e12])
    def test_matches_gnpy(self, offset_hz: float) -> None:
        fiber = _fiber()
        cut_w, pump_w = 1e-3, 2e-3
        ours = cross_channel_nli_power(fiber, cut_w, 32e9, [Neighbour(offset_hz, pump_w, 32e9)])
        assert ours == pytest.approx(_gnpy_eta(fiber, offset_hz) * cut_w * pump_w ** 2, rel=1e-9)

    def test_closer_and_stronger_neighbours_hurt_more(self) -> None:
        fiber = _fiber()

        def nli(offset_hz: float, power_w: float) -> float:
            return cross_channel_nli_power(fiber, 1e-3, 32e9, [Neighbour(offset_hz, power_w, 32e9)])

        assert nli(50e9, 1e-3) > nli(200e9, 1e-3) > nli(1e12, 1e-3) > 0
        assert nli(50e9, 2e-3) == pytest.approx(4 * nli(50e9, 1e-3))
        assert nli(-50e9, 1e-3) == pytest.approx(nli(50e9, 1e-3))

    def test_neighbours_add_up(self) -> None:
        fiber = _fiber()
        a, b = Neighbour(50e9, 1e-3, 32e9), Neighbour(-100e9, 2e-3, 32e9)
        both = cross_channel_nli_power(fiber, 1e-3, 32e9, [a, b])
        assert both == pytest.approx(cross_channel_nli_power(fiber, 1e-3, 32e9, [a])
                                     + cross_channel_nli_power(fiber, 1e-3, 32e9, [b]))
        assert cross_channel_nli_power(fiber, 1e-3, 32e9, []) == 0.0


class TestNoiseBudget:
    def test_noiseless_path_is_infinite(self) -> None:
        qot = NoiseBudget().result()
        assert qot.gsnr_db == qot.osnr_ase_db == qot.snr_nli_db == math.inf

    def test_inverse_snrs_add(self) -> None:
        """Each source is compared with the signal where it is added, so two
        equal contributions at different levels cost 3 dB."""
        budget = NoiseBudget()
        budget.add_ase(1e-6, 1e-3)      # 30 dB at a 0 dBm point
        budget.add_ase(1e-8, 1e-5)      # 30 dB at a -20 dBm point
        budget.add_nli(1e-6, 1e-3)      # 30 dB of NLI
        qot = budget.result()
        assert qot.osnr_ase_db == pytest.approx(30 - 10 * math.log10(2))
        assert qot.snr_nli_db == pytest.approx(30.0)
        assert qot.gsnr_db == pytest.approx(30 - 10 * math.log10(3))

    def test_dbm_to_w(self) -> None:
        assert dbm_to_w(0.0) == pytest.approx(1e-3)
        assert dbm_to_w(-30.0) == pytest.approx(1e-6)


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
