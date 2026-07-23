"""GSNR → pre-FEC BER → Q-factor conversion for coherent DP-QAM formats.

Uses Gaussian approximation (erfc-based) for coherent DP-QAM systems.
All formulas assume ideal DSP with no implementation penalty beyond GSNR.

References:
    - Essiambre et al., "Capacity Limits of Optical Fiber Networks," JLT 2010
    - Schmogrow et al., "Error Vector Magnitude as a Performance Measure," PTL 2012
"""

from __future__ import annotations

import math

from scipy.special import erfc, erfcinv

from twinlight.physics.modulation import ModulationFormat, get_params


# Gaussian approximation coefficients: (pre_factor, snr_divisor)
# BER = pre_factor * erfc(sqrt(GSNR_lin / snr_divisor))
_BER_COEFFICIENTS: dict[int, tuple[float, float]] = {
    2: (1.0,    2.0),    # DP-QPSK:  BER ≈ erfc(sqrt(GSNR/2))
    4: (3 / 8,  10.0),   # DP-16QAM: BER ≈ (3/8) * erfc(sqrt(GSNR/10))
    6: (7 / 24, 42.0),   # DP-64QAM: BER ≈ (7/24) * erfc(sqrt(GSNR/42))
}


def gsnr_to_ber(gsnr_db: float, fmt: str | ModulationFormat) -> float:
    """Convert GSNR [dB] to pre-FEC BER using Gaussian approximation.

    Args:
        gsnr_db: Generalised SNR in dB.
        fmt: Modulation format string or enum.

    Returns:
        Pre-FEC bit error ratio (0 < BER < 1).
    """
    params = get_params(fmt)
    gsnr_lin = 10 ** (gsnr_db / 10.0)
    coeff, divisor = _BER_COEFFICIENTS[params.bits_per_symbol]
    arg = math.sqrt(max(gsnr_lin / divisor, 0.0))
    return float(coeff * erfc(arg))


def ber_to_q_db(ber: float) -> float:
    """Convert pre-FEC BER to Q-factor in dB.

    Q [dB] = 20 * log10(sqrt(2) * erfcinv(2 * BER))

    Args:
        ber: Pre-FEC bit error ratio.

    Returns:
        Q-factor in dB (positive).
    """
    ber = max(ber, 1e-20)  # guard log(0)
    q_lin = math.sqrt(2.0) * float(erfcinv(2.0 * ber))
    return 20.0 * math.log10(max(q_lin, 1e-10))
