"""Tests for the GSNR → pre-FEC BER → Q-factor conversion."""

import itertools
import math

import pytest
from scipy.special import erfc

from twinlight.physics.ber_conversion import ber_to_q_db, gsnr_to_ber

FORMATS = [("DP-QPSK", 4), ("DP-16QAM", 16), ("DP-64QAM", 64)]


def _square_qam_ber(gsnr_db: float, order: int) -> float:
    """Gray-coded square M-QAM: (2/log2 M)(1 − 1/√M)·erfc(√(3·SNR/(2(M−1))))."""
    snr = 10 ** (gsnr_db / 10)
    return (
        2 / math.log2(order) * (1 - 1 / math.sqrt(order))
        * erfc(math.sqrt(3 * snr / (2 * (order - 1))))
    )


class TestGsnrToBer:
    @pytest.mark.parametrize("fmt, order", FORMATS)
    @pytest.mark.parametrize("gsnr_db", [5.0, 10.0, 15.0, 20.0])
    def test_matches_the_general_square_qam_expression(self, fmt, order, gsnr_db):
        """The per-format table is one formula; no entry may drift from it."""
        assert gsnr_to_ber(gsnr_db, fmt) == pytest.approx(
            _square_qam_ber(gsnr_db, order), rel=1e-12
        )

    @pytest.mark.parametrize("gsnr_db", [6.0, 8.5, 12.0])
    def test_qpsk_q_factor_equals_gsnr(self, gsnr_db):
        """For QPSK the expression is exact and Q² = SNR, so Q [dB] = GSNR [dB]."""
        assert ber_to_q_db(gsnr_to_ber(gsnr_db, "DP-QPSK")) == pytest.approx(
            gsnr_db, abs=1e-9
        )

    @pytest.mark.parametrize("fmt, _order", FORMATS)
    def test_ber_falls_as_gsnr_rises(self, fmt, _order):
        bers = [gsnr_to_ber(g, fmt) for g in range(25)]
        assert all(a > b for a, b in itertools.pairwise(bers))

    def test_denser_formats_need_more_gsnr(self):
        bers = [gsnr_to_ber(15.0, fmt) for fmt, _ in FORMATS]
        assert bers[0] < bers[1] < bers[2]
