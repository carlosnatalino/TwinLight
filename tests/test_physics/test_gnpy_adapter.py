"""Unit consistency of the GNPy → OpmBaseline conversion.

``SpectralInformation`` carries accumulated CD in s/m, PMD in s and latency
in s; gnpy's own ``Transceiver._calc_cd`` / ``_calc_pmd`` / ``_calc_latency``
are the authority for the scale factors that turn those into the ps/nm, ps
and ms this project reports.  A missing factor here is invisible in the API
response — it just reads plausibly wrong — so it is asserted directly.
"""

from __future__ import annotations

import numpy as np
import pytest

from twinlight.physics.analytical_metrics import chromatic_dispersion_ps_per_nm
from twinlight.physics.channel_plan import line_comb
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
    return compute_path_baseline(
        gnpy_context._gnpy_uid_map, path, "DP-QPSK",
        defaults=gnpy_context._backend.spectral_defaults,
        bands=gnpy_context._backend.amplifier_bands(path),
        centre_frequency_hz=193.1e12, media_channel_width_hz=56.25e9,
        launch_power_dbm=gnpy_context.default_launch_power_dbm,
    )


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
    """The launched comb is the equipment library's SI comb, fully loaded,
    with the service channel inserted at its own frequency."""

    @pytest.fixture
    def si(self):
        from twinlight.physics.channel_plan import load_spectral_defaults

        return load_spectral_defaults(None)  # gnpy's bundled eqpt_config.json

    def test_si_comb_is_76_channels_from_191_35_to_195_10_thz(self, si) -> None:
        """gnpy 3 treats f_min and f_max as the first and last channel centres.

        Regression: gnpy 2 put the first channel one slot above f_min, so the
        same bounds launched one channel fewer, with no error. line_comb counts
        channels the gnpy 3 way; create_input_spectral_information pins it.
        """
        from gnpy.core.info import create_input_spectral_information

        reference = create_input_spectral_information(
            f_min=si.f_min_hz, f_max=si.f_max_hz, roll_off=si.roll_off,
            baud_rate=si.baud_rate_hz, spacing=si.spacing_hz, tx_osnr=100.0,
            tx_power=1e-3,
        )
        assert reference.number_of_channels == 76
        assert reference.frequency[0] == pytest.approx(191.35e12)
        assert reference.frequency[-1] == pytest.approx(195.10e12)

        far = line_comb(si, centre_frequency_hz=200e12, media_channel_width_hz=1.0,
                        baud_rate_hz=32e9, launch_power_dbm=0.0, bands=None)
        assert list(far.frequency_hz[far.neighbours]) == pytest.approx(
            list(reference.frequency))

    def test_service_replaces_the_channels_its_media_channel_overlaps(self, si) -> None:
        # 193.10 THz is an SI channel; a 56.25 GHz media channel overlaps the
        # 50 GHz slots of 193.05, 193.10 and 193.15 THz.
        comb = line_comb(si, centre_frequency_hz=193.1e12, media_channel_width_hz=56.25e9,
                         baud_rate_hz=32e9, launch_power_dbm=2.0, bands=None)
        assert comb.frequency_hz.size == 76 - 3 + 1
        assert comb.frequency_hz[comb.service_index] == 193.1e12
        assert comb.launch_power_dbm[comb.service_index] == 2.0
        assert comb.slot_width_hz[comb.service_index] == 56.25e9
        others = comb.frequency_hz[comb.neighbours]
        assert np.min(np.abs(others - 193.1e12)) == pytest.approx(100e9)
        assert set(comb.launch_power_dbm[comb.neighbours]) == {si.power_dbm}
        assert np.all(np.diff(comb.frequency_hz) > 0)

    def test_off_grid_service_and_amplifier_band(self, si) -> None:
        centre = 193.125e12  # a 6.25 GHz grid point between two SI channels
        comb = line_comb(si, centre_frequency_hz=centre, media_channel_width_hz=56.25e9,
                         baud_rate_hz=32e9, launch_power_dbm=0.0,
                         bands=[(191.5e12, 196.125e12)])
        others = comb.frequency_hz[comb.neighbours]
        assert 193.10e12 not in others and 193.15e12 not in others
        assert others.min() - si.spacing_hz / 2 >= 191.5e12   # GNPy's is_in_band
        assert comb.frequency_hz[comb.service_index] == centre


class TestServiceChannelIsReported:
    def test_metrics_are_the_service_channel(self, gnpy_context, monkeypatch) -> None:
        """Not the worst channel: the channel at the assigned frequency."""
        from gnpy.core.elements import Transceiver

        received = {}
        call = Transceiver.__call__

        def spy(self, si, *args, **kwargs):
            out = call(self, si, *args, **kwargs)
            received["si"] = out
            return out

        monkeypatch.setattr(Transceiver, "__call__", spy)
        sip_a, sip_z = (sip.uuid for sip in gnpy_context.get_sips()[:2])
        path = gnpy_context.get_service_path(sip_a, sip_z)
        defaults = gnpy_context._backend.spectral_defaults
        centre = 194.003125e12
        result = compute_path_baseline(
            gnpy_context._gnpy_uid_map, path, "DP-QPSK", defaults=defaults,
            bands=gnpy_context._backend.amplifier_bands(path),
            centre_frequency_hz=centre, media_channel_width_hz=56.25e9,
            launch_power_dbm=defaults.power_dbm + 2,
        )
        si = received["si"]
        (ch,) = np.flatnonzero(np.abs(si.frequency - centre) < 1)
        assert result.gsnr_db == pytest.approx(float(si.gsnr_db[ch]))
        assert result.cd_ps_nm == pytest.approx(float(si.chromatic_dispersion[ch]) * 1e3)
        assert result.gsnr_db > float(np.min(si.gsnr_db))  # +2 dB: not the worst

    def test_frequency_moves_the_result(self, gnpy_context) -> None:
        sip_a, sip_z = (sip.uuid for sip in gnpy_context.get_sips()[:2])
        path = gnpy_context.get_service_path(sip_a, sip_z)

        def at(centre: float):
            return compute_path_baseline(
                gnpy_context._gnpy_uid_map, path, "DP-QPSK",
                defaults=gnpy_context._backend.spectral_defaults,
                bands=gnpy_context._backend.amplifier_bands(path),
                centre_frequency_hz=centre, media_channel_width_hz=56.25e9,
                launch_power_dbm=0.0,
            )

        # The EDFA noise figure and gain vary across the band, so the ASE a
        # channel collects depends on where it sits.
        low, high = at(191.453125e12), at(195.003125e12)
        assert abs(low.osnr_ase_db - high.osnr_ase_db) > 0.01
