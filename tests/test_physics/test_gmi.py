"""Tests for the GSNR → GMI conversion."""

import itertools
import math

import numpy as np
import pytest

from twinlight.physics.gmi import gsnr_to_gmi

FORMATS = [("DP-QPSK", 2), ("DP-16QAM", 4), ("DP-64QAM", 6)]


def _monte_carlo_gmi(gsnr_db: float, bits: int, samples: int = 50_000) -> float:
    """Independent estimate of the same GMI, by seeded sampling.

    Works on the full 2D constellation with complex noise rather than on one
    PAM with quadrature, so it also checks the factorization gmi.py relies
    on. Labels are the product of two Gray PAM labels, as there.
    """
    rng = np.random.default_rng(12345)
    size = 2 ** (bits // 2)
    pam = 2.0 * np.arange(size) - (size - 1)
    gray = np.arange(size) ^ (np.arange(size) >> 1)
    i, q = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
    points = (pam[i] + 1j * pam[q]).ravel()
    labels = ((gray[i] << (bits // 2)) | gray[q]).ravel()
    n0 = np.mean(np.abs(points) ** 2) / 10 ** (gsnr_db / 10)

    tx = rng.integers(points.size, size=samples)
    noise = rng.standard_normal(samples) + 1j * rng.standard_normal(samples)
    y = points[tx] + math.sqrt(n0 / 2) * noise
    loglik = -np.abs(y[:, None] - points[None, :]) ** 2 / n0
    lik = np.exp(loglik - loglik.max(axis=1, keepdims=True))
    total = np.log(lik.sum(axis=1))
    gmi = float(bits)
    for k in range(bits):
        bit = (labels >> k) & 1
        same = bit[None, :] == bit[tx][:, None]
        gmi -= float(np.mean(total - np.log((lik * same).sum(axis=1)))) / math.log(2)
    return gmi


class TestGsnrToGmi:
    @pytest.mark.parametrize("fmt, bits", FORMATS)
    def test_matches_a_monte_carlo_estimate(self, fmt, bits):
        """Across the waterfall, where the GMI is neither ~0 nor saturated."""
        for gsnr_db in (3.0 * bits - 4.0, 3.0 * bits + 2.0):
            assert gsnr_to_gmi(gsnr_db, fmt) == pytest.approx(
                _monte_carlo_gmi(gsnr_db, bits), abs=0.02
            ), gsnr_db

    @pytest.mark.parametrize("fmt, bits", FORMATS)
    def test_saturates_at_bits_per_symbol(self, fmt, bits):
        assert gsnr_to_gmi(40.0, fmt) == pytest.approx(bits, abs=1e-9)

    @pytest.mark.parametrize("fmt, bits", FORMATS)
    def test_vanishes_at_low_gsnr(self, fmt, bits):
        assert gsnr_to_gmi(-20.0, fmt) < 0.02

    @pytest.mark.parametrize("fmt, bits", FORMATS)
    def test_rises_with_gsnr_and_stays_in_range(self, fmt, bits):
        values = [gsnr_to_gmi(g, fmt) for g in np.arange(-10.0, 40.0, 0.5)]
        assert all(0.0 <= v <= bits for v in values)
        assert all(a <= b for a, b in itertools.pairwise(values))
