"""Tests for eye diagram synthesis.

Verifies that OPM metrics (GSNR, PMD) correctly map to eye opening and
timing jitter, and that the statistical properties match expectations.

References:
    - docs/PHYSICS.md: eye and constellation synthesis
    - Standard coherent DSP eye diagram interpretation
"""

from __future__ import annotations

import numpy as np
import pytest

from twinlight.output.eye_diagram import (
    EyeDiagramData,
    eye_to_density,
    eye_to_json,
    synthesize_eye,
)

# Fixed seed for the statistical tests: synthesize_eye() is a Monte-Carlo
# generator, so tests that compare its output must seed it to be reproducible.
# Without a seed the RNG differs every run and threshold comparisons flake.
_SEED = 20240501


class TestSynthesizeEye:
    def test_returns_eye_diagram_data(self) -> None:
        measurements = {"gsnr-db": 20.0, "pmd-ps": 0.5}
        result = synthesize_eye(measurements, "DP-QPSK", n_traces=50)
        assert isinstance(result, EyeDiagramData)

    def test_correct_number_of_traces(self) -> None:
        measurements = {"gsnr-db": 20.0}
        result = synthesize_eye(measurements, "DP-QPSK", n_traces=100)
        assert result.n_traces == 100
        assert result.traces.shape[0] == 100

    def test_correct_samples_per_symbol(self) -> None:
        measurements = {"gsnr-db": 20.0}
        result = synthesize_eye(
            measurements, "DP-QPSK", n_traces=10, samples_per_symbol=32
        )
        expected_samples = 2 * 32
        assert result.traces.shape[1] == expected_samples

    def test_high_gsnr_has_clear_eye(self) -> None:
        measurements = {"gsnr-db": 30.0, "pmd-ps": 0.0}
        result = synthesize_eye(
            measurements, "DP-QPSK", n_traces=200, seed=_SEED
        )
        mid_idx = result.traces.shape[1] // 2
        mid_col = result.traces[:, mid_idx]
        positive = mid_col[mid_col > 0]
        if len(positive) > 10:
            var_positive = np.var(positive)
            assert var_positive < 0.1, "High GSNR should have clear eye levels"

    def test_low_gsnr_has_noisy_eye(self) -> None:
        measurements = {"gsnr-db": 10.0, "pmd-ps": 0.0}
        result = synthesize_eye(
            measurements, "DP-QPSK", n_traces=200, seed=_SEED
        )
        mid_idx = result.traces.shape[1] // 2
        mid_col = result.traces[:, mid_idx]
        var_at_center = np.var(mid_col)
        assert var_at_center > 0.1, "Low GSNR should have noisy eye"

    def test_pmd_adds_jitter(self) -> None:
        # Controlled comparison: the SAME seed gives both runs identical bit
        # patterns and noise, so the only difference is the PMD timing
        # jitter. With that isolation the assertion is deterministic.
        n_traces = 600
        result_no_pmd = synthesize_eye(
            {"gsnr-db": 30.0, "pmd-ps": 0.0},
            "DP-QPSK",
            n_traces=n_traces,
            seed=_SEED,
        )
        result_with_pmd = synthesize_eye(
            {"gsnr-db": 30.0, "pmd-ps": 10.0},
            "DP-QPSK",
            n_traces=n_traces,
            seed=_SEED,
        )
        # Timing jitter shows up in the transition region of the eye, where
        # the slope is steep (a time shift becomes an amplitude spread). The
        # flat symbol centres barely move, so a whole-eye average washes the
        # effect out -- measure variance in the columns around the crossing.
        n_samples = result_no_pmd.traces.shape[1]
        cross = n_samples // 4  # transition column, at t = -T/2
        window = slice(cross - 3, cross + 4)
        spread_no_pmd = float(
            np.var(result_no_pmd.traces[:, window], axis=0).mean()
        )
        spread_with_pmd = float(
            np.var(result_with_pmd.traces[:, window], axis=0).mean()
        )
        assert spread_with_pmd > spread_no_pmd, "PMD should add timing spread"

    def test_symbol_period_matches_modulation(self) -> None:
        measurements = {"gsnr-db": 20.0}
        result = synthesize_eye(measurements, "DP-QPSK", n_traces=10)
        expected_ns = 1e9 / 32e9
        assert abs(result.symbol_period_ns - expected_ns) < 0.001

    @pytest.mark.parametrize("fmt", ["DP-QPSK", "DP-16QAM", "DP-64QAM"])
    def test_all_modulation_formats(self, fmt: str) -> None:
        measurements = {"gsnr-db": 20.0}
        result = synthesize_eye(measurements, fmt, n_traces=10)
        assert result.n_traces == 10


class TestEyeToDensity:
    def test_returns_three_arrays(self) -> None:
        measurements = {"gsnr-db": 20.0}
        eye_data = synthesize_eye(measurements, "DP-QPSK", n_traces=50)
        time_edges, amp_edges, density = eye_to_density(eye_data)
        assert isinstance(time_edges, np.ndarray)
        assert isinstance(amp_edges, np.ndarray)
        assert isinstance(density, np.ndarray)

    def test_density_shape(self) -> None:
        measurements = {"gsnr-db": 20.0}
        eye_data = synthesize_eye(
            measurements, "DP-QPSK", n_traces=50, samples_per_symbol=32
        )
        _, _amp_edges, density = eye_to_density(eye_data, amplitude_bins=50)
        assert density.shape[0] == 50
        assert density.shape[1] == len(eye_data.time_ns)

    def test_density_sums_to_one_per_column(self) -> None:
        measurements = {"gsnr-db": 20.0}
        eye_data = synthesize_eye(measurements, "DP-QPSK", n_traces=100)
        _, _, density = eye_to_density(eye_data)
        col_sums = density.sum(axis=0)
        for s in col_sums:
            assert 0.9 < s < 1.1, "Density should sum to ~1 per time bin"


class TestEyeToJson:
    def test_returns_dict(self) -> None:
        measurements = {"gsnr-db": 20.0}
        eye_data = synthesize_eye(measurements, "DP-QPSK", n_traces=50)
        result = eye_to_json(eye_data)
        assert isinstance(result, dict)

    def test_has_required_keys(self) -> None:
        measurements = {"gsnr-db": 20.0}
        eye_data = synthesize_eye(measurements, "DP-QPSK", n_traces=50)
        result = eye_to_json(eye_data)
        assert "time_ns" in result
        assert "traces" in result
        assert "symbol_period_ns" in result
        assert "n_traces" in result

    def test_respects_max_traces(self) -> None:
        measurements = {"gsnr-db": 20.0}
        eye_data = synthesize_eye(measurements, "DP-QPSK", n_traces=100)
        result = eye_to_json(eye_data, max_traces=20)
        assert len(result["traces"]) <= 20

    def test_time_ns_is_list(self) -> None:
        measurements = {"gsnr-db": 20.0}
        eye_data = synthesize_eye(measurements, "DP-QPSK", n_traces=10)
        result = eye_to_json(eye_data)
        assert isinstance(result["time_ns"], list)


class TestGsnrToEyeOpening:
    """Verify that GSNR affects eye opening as expected."""

    def test_higher_gsnr_has_larger_eye_opening(self) -> None:
        n_traces = 200
        samples = 64
        # Same seed => identical bit patterns; only the GSNR-driven noise
        # differs, so the comparison is deterministic.
        result_high = synthesize_eye(
            {"gsnr-db": 25.0, "pmd-ps": 0.0},
            "DP-QPSK",
            n_traces=n_traces,
            samples_per_symbol=samples,
            seed=_SEED,
        )
        result_low = synthesize_eye(
            {"gsnr-db": 15.0, "pmd-ps": 0.0},
            "DP-QPSK",
            n_traces=n_traces,
            samples_per_symbol=samples,
            seed=_SEED,
        )
        mid_idx = samples

        high_vals = result_high.traces[:, mid_idx]
        low_vals = result_low.traces[:, mid_idx]

        high_opening = np.percentile(high_vals, 90) - np.percentile(high_vals, 10)
        low_opening = np.percentile(low_vals, 90) - np.percentile(low_vals, 10)

        assert high_opening < low_opening, (
            "Higher GSNR should have tighter spread (larger effective opening)"
        )
