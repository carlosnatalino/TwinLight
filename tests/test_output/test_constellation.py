"""Tests for constellation diagram synthesis.

Verifies that OPM metrics (GSNR, linewidth) correctly map to constellation
spread, and that the statistical properties match expectations.

References:
    - Sequeira et al., "OCATA: A Deep-Learning-Based Digital Twin," ECOC 2023
    - docs/PHYSICS.md: eye and constellation synthesis
"""

from __future__ import annotations

import numpy as np
import pytest

from twinlight.output.constellation import (
    _ideal_constellation,
    constellation_to_iq,
    synthesize_constellation,
)


class TestIdealConstellation:
    def test_qpsk_has_4_points(self) -> None:
        points = _ideal_constellation("DP-QPSK")
        assert len(points) == 4

    def test_16qam_has_16_points(self) -> None:
        points = _ideal_constellation("DP-16QAM")
        assert len(points) == 16

    def test_64qam_has_64_points(self) -> None:
        points = _ideal_constellation("DP-64QAM")
        assert len(points) == 64

    def test_points_are_normalized(self) -> None:
        for fmt in ["DP-QPSK", "DP-16QAM", "DP-64QAM"]:
            points = _ideal_constellation(fmt)
            avg_power = np.mean(np.abs(points) ** 2)
            assert abs(avg_power - 1.0) < 1e-10, f"{fmt} not normalized"


class TestSynthesizeConstellation:
    def test_returns_correct_number_of_symbols(self) -> None:
        measurements = {"gsnr-db": 20.0}
        result = synthesize_constellation(
            measurements, "DP-QPSK", n_symbols=500
        )
        assert len(result) == 500

    def test_high_gsnr_has_low_spread(self) -> None:
        measurements = {"gsnr-db": 30.0}
        result = synthesize_constellation(
            measurements, "DP-QPSK", n_symbols=5000, linewidth_hz=0.0
        )
        variance = np.var(result)
        assert variance < 1.1, "High GSNR should have low variance"

    def test_low_gsnr_has_high_spread(self) -> None:
        measurements = {"gsnr-db": 10.0}
        result = synthesize_constellation(
            measurements, "DP-QPSK", n_symbols=5000, linewidth_hz=0.0
        )
        variance = np.var(result)
        assert variance > 1.0, "Low GSNR should have higher variance"

    def test_spread_increases_with_lower_gsnr(self) -> None:
        n_sym = 5000
        linewidth = 0.0
        result_high = synthesize_constellation(
            {"gsnr-db": 25.0}, "DP-QPSK", n_sym, linewidth
        )
        result_low = synthesize_constellation(
            {"gsnr-db": 15.0}, "DP-QPSK", n_sym, linewidth
        )
        var_high = np.var(result_high)
        var_low = np.var(result_low)
        assert var_low > var_high, "Lower GSNR should increase spread"

    def test_linewidth_adds_phase_spread(self) -> None:
        measurements = {"gsnr-db": 30.0}
        n_sym = 10000
        result_no_phase = synthesize_constellation(
            measurements, "DP-QPSK", n_sym, linewidth_hz=0.0
        )
        result_with_phase = synthesize_constellation(
            measurements, "DP-QPSK", n_sym, linewidth_hz=10_000_000.0
        )
        ideal = _ideal_constellation("DP-QPSK")

        def avg_dist_to_nearest(samples: np.ndarray) -> float:
            return np.mean([min(np.abs(s - p) for p in ideal) for s in samples])

        dist_no = avg_dist_to_nearest(result_no_phase)
        dist_with = avg_dist_to_nearest(result_with_phase)
        assert dist_with > dist_no, "Linewidth should add phase spread"

    def test_uses_default_linewidth_when_none(self) -> None:
        measurements = {"gsnr-db": 20.0}
        result = synthesize_constellation(measurements, "DP-QPSK", n_symbols=100)
        assert len(result) == 100

    @pytest.mark.parametrize("fmt", ["DP-QPSK", "DP-16QAM", "DP-64QAM"])
    def test_all_modulation_formats(self, fmt: str) -> None:
        measurements = {"gsnr-db": 20.0}
        result = synthesize_constellation(measurements, fmt, n_symbols=100)
        assert len(result) == 100


class TestConstellationToIq:
    def test_returns_two_lists(self) -> None:
        symbols = np.array([1 + 1j, -1 - 1j])
        i_coords, q_coords = constellation_to_iq(symbols)
        assert isinstance(i_coords, list)
        assert isinstance(q_coords, list)

    def test_correct_values(self) -> None:
        symbols = np.array([1 + 2j, -3 + 4j])
        i_coords, q_coords = constellation_to_iq(symbols)
        assert i_coords == [1.0, -3.0]
        assert q_coords == [2.0, 4.0]


class TestNoiseMappingFromGsnr:
    """Verify that noise variance matches the theoretical formula."""

    def test_noise_variance_matches_formula(self) -> None:
        for gsnr_db in [10.0, 20.0, 30.0]:
            gsnr_lin = 10 ** (gsnr_db / 10.0)
            expected_var = 1.0 / (2.0 * gsnr_lin)
            measurements = {"gsnr-db": gsnr_db}
            result = synthesize_constellation(
                measurements, "DP-QPSK", n_symbols=50000, linewidth_hz=0.0
            )
            ideal = _ideal_constellation("DP-QPSK")

            distances = []
            for sym in result:
                dist_to_nearest = min(np.abs(sym - p) for p in ideal)
                distances.append(dist_to_nearest ** 2)
            measured_var = np.mean(distances)
            error = abs(measured_var - 2 * expected_var) / (2 * expected_var)
            assert error < 0.15, (
                f"GSNR {gsnr_db} dB: measured noise variance {measured_var:.4f} "
                f"vs expected {2*expected_var:.4f} (2σ²)"
            )
