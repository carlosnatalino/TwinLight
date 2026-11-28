"""Environmental transient model: thermal CD drift and fiber-loss drift."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from twinlight.config import EnvironmentalConfig
from twinlight.physics.transients import environmental

# A quarter of the diurnal period: the temperature excursion is at its peak.
_PEAK_T = 86400.0 / 4


class TestThermalDispersionCoefficient:
    def test_the_default_is_negative(self) -> None:
        # Kato, Koyano & Nishimura, Opt. Lett. 25(16):1156 (2000): every
        # coefficient they measure is negative, -0.0005 to -0.0038
        # ps/(nm·km·°C); dispersion falls as the fiber warms.
        assert -0.0038 <= EnvironmentalConfig().cd_temp_coeff_ps_nm_km_c <= -0.0005

    @pytest.mark.parametrize("value", [-0.0005, -0.0015, -0.0038])
    def test_a_measured_value_is_accepted(self, value: float) -> None:
        # Kato et al. 2000's range; the old ge=0 bound refused all of it.
        assert EnvironmentalConfig(cd_temp_coeff_ps_nm_km_c=value).cd_temp_coeff_ps_nm_km_c == value

    def test_a_warmer_fiber_has_less_dispersion(self) -> None:
        cfg = EnvironmentalConfig(enabled=True)
        assert environmental.delta_cd_ps_nm(_PEAK_T, 100.0, cfg) == pytest.approx(
            -0.0015 * 100.0 * cfg.temp_variation_c
        )

    def test_the_coefficient_is_still_a_number(self) -> None:
        with pytest.raises(ValidationError):
            EnvironmentalConfig(cd_temp_coeff_ps_nm_km_c="cold")


def _route_snr_db(
    n_spans: int, span_loss_db: float, *, ase_per_span: float = 1e-4,
    nli_per_span: float = 5e-5,
) -> tuple[float, float]:
    """(GSNR, OSNR) [dB] of ``n_spans`` identical spans whose amplifiers hold
    their output power, from the incoherent sum of per-span noise terms
    (Carena et al., JLT 2012): each amplifier's ASE relative to the signal
    grows with its span loss, the NLI depends on the launch power only."""
    inverse_osnr = n_spans * ase_per_span * 10 ** (span_loss_db / 10)
    inverse_gsnr = inverse_osnr + n_spans * nli_per_span
    return -10 * math.log10(inverse_gsnr), -10 * math.log10(inverse_osnr)


class TestSpanLossUnderOutputPowerControl:
    """A uniform per-span loss change moves the OSNR by one span's change,
    whatever the route length (the module docstring)."""

    @pytest.mark.parametrize("n_spans", [1, 5, 32])
    def test_matches_the_per_span_noise_sum(self, n_spans: int) -> None:
        delta = 0.12
        before = _route_snr_db(n_spans, 20.0)
        after = _route_snr_db(n_spans, 20.0 + delta)
        gsnr, osnr = environmental.apply_span_loss_change(*before, delta)
        assert gsnr == pytest.approx(after[0], abs=1e-12)
        assert osnr == pytest.approx(after[1], abs=1e-12)

    def test_the_change_does_not_grow_with_the_number_of_spans(self) -> None:
        delta = 0.1
        changes = []
        for n_spans in (2, 10, 40):
            gsnr, osnr = _route_snr_db(n_spans, 20.0)
            new_gsnr, new_osnr = environmental.apply_span_loss_change(gsnr, osnr, delta)
            assert new_osnr - osnr == pytest.approx(-delta)
            changes.append(new_gsnr - gsnr)
        # Identical spans: the ASE share of the noise, and so the GSNR change,
        # does not depend on how many there are.
        assert changes == pytest.approx([changes[0]] * 3)
        assert -delta < changes[0] < 0  # NLI is untouched: less than δ

    def test_the_loss_change_is_one_mean_span(self) -> None:
        cfg = EnvironmentalConfig(enabled=True)
        # 2847 km in 32 spans: δ is that of one 89 km span, not of 2847 km.
        delta = environmental.span_loss_change_db(_PEAK_T, 2847.0, 32, cfg)
        assert delta == pytest.approx(
            cfg.alpha_temp_coeff_db_km_c * 2847.0 / 32 * cfg.temp_variation_c
        )
        assert delta < 0.1

    def test_a_cooler_fiber_improves_the_snr(self) -> None:
        cfg = EnvironmentalConfig(enabled=True)
        delta = environmental.span_loss_change_db(3 * _PEAK_T, 800.0, 10, cfg)
        assert delta < 0
        gsnr, osnr = environmental.apply_span_loss_change(14.0, 17.0, delta)
        assert gsnr > 14.0 and osnr > 17.0

    def test_disabled_or_spanless_is_no_change(self) -> None:
        assert environmental.span_loss_change_db(_PEAK_T, 800.0, 10, EnvironmentalConfig()) == 0.0
        enabled = EnvironmentalConfig(enabled=True)
        assert environmental.span_loss_change_db(_PEAK_T, 0.0, 0, enabled) == 0.0
        assert environmental.apply_span_loss_change(14.0, 17.0, 0.0) == (14.0, 17.0)
