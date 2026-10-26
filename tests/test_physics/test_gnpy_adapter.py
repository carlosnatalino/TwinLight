"""Unit consistency of the GNPy → OpmBaseline conversion.

``SpectralInformation`` carries accumulated CD in s/m, PMD in s and latency
in s; gnpy's own ``Transceiver._calc_cd`` / ``_calc_pmd`` / ``_calc_latency``
are the authority for the scale factors that turn those into the ps/nm, ps
and ms this project reports.  A missing factor here is invisible in the API
response — it just reads plausibly wrong — so it is asserted directly.
"""

from __future__ import annotations

import pytest

from twinlight.physics.analytical_metrics import chromatic_dispersion_ps_per_nm
from twinlight.physics.gnpy_adapter import compute_path_baseline


@pytest.fixture
def baseline(gnpy_context):
    """Baseline for the fixture topology's only path (Brest → Morlaix, 80 km).

    The path is resolved through the context rather than hand-written:
    gnpy's ROADM propagation needs the degree context that the real path
    computation supplies.
    """
    sip_a, sip_z = (sip.uuid for sip in gnpy_context.get_sips()[:2])
    path = gnpy_context.get_service_path(sip_a, sip_z)
    assert path, "fixture topology should have a path between its two SIPs"
    return compute_path_baseline(gnpy_context._gnpy_uid_map, path, "DP-QPSK")


class TestBaselineUnits:
    def test_cd_is_ps_per_nm_not_s_per_m(self, baseline) -> None:
        """Accumulated CD must be scaled out of gnpy's s/m.

        Regression: the raw ``si.chromatic_dispersion`` was stored straight
        into ``cd_ps_nm``, reporting CD 1000x low. That also silently
        zeroed the EEPN term, whose alpha is proportional to accumulated
        dispersion.
        """
        # Cross-check against the closed-form value the EGN backend uses,
        # so the two physics backends cannot drift apart on this field.
        expected = chromatic_dispersion_ps_per_nm(baseline.total_fiber_km)
        assert baseline.cd_ps_nm == pytest.approx(expected, rel=0.2)
        # 80 km of SSMF is of order 1e3 ps/nm, emphatically not of order 1.
        assert baseline.cd_ps_nm > 100.0

    def test_pmd_and_latency_scales(self, baseline) -> None:
        """PMD and latency keep the scales gnpy's Transceiver applies."""
        assert baseline.total_fiber_km == pytest.approx(80.0, abs=1.0)
        # 0.04 ps/sqrt(km) over 80 km ~ 0.36 ps; a bare seconds value
        # would be ~1e-13.
        assert 0.01 < baseline.pmd_ps < 10.0
        # 80 km of fiber at ~2e8 m/s is ~0.4 ms.
        assert 0.1 < baseline.latency_ms < 10.0


class TestPropagationComb:
    def test_comb_is_88_channels_from_190_95_to_195_30_thz(self) -> None:
        """The launched comb must not move when gnpy changes what f_min means.

        Regression: gnpy 3 made f_min/f_max the first and last channel
        centres, where gnpy 2 put the first channel one slot above f_min.
        Passing the old bounds then launched 89 channels from 190.90 THz —
        no error, just a different spectrum under every baseline.
        """
        from gnpy.core.info import create_input_spectral_information

        from twinlight.physics import gnpy_adapter as ga

        si = create_input_spectral_information(
            f_min=ga._F_MIN_HZ, f_max=ga._F_MAX_HZ, roll_off=0.15,
            baud_rate=32e9, spacing=ga._SPACING_HZ, tx_osnr=100.0,
            tx_power=1e-3,
        )
        assert si.number_of_channels == ga._NB_CHANNELS == 88
        assert si.frequency[0] == pytest.approx(190.95e12)
        assert si.frequency[-1] == pytest.approx(195.30e12)
