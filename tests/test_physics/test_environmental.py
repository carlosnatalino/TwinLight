"""Environmental transient model: thermal CD drift and fiber-loss drift."""

from __future__ import annotations

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
