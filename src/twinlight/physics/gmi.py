"""GSNR → generalized mutual information (GMI) for coherent DP-QAM formats.

The GMI is the achievable rate of a bit-interleaved coded modulation (BICM)
receiver — the bit-wise soft-decision decoder of every coherent transceiver —
and the normalized GMI (NGMI = GMI / bits per symbol) is the quantity a
soft-decision FEC threshold is properly stated in: unlike pre-FEC BER, it
predicts post-FEC BER consistently across modulation formats and code rates.

    GMI = Σ_k I(B_k; Y)
        = m − Σ_k E[ log2( Σ_{x'} p(y|x') / Σ_{x' : b_k(x') = b_k(x)} p(y|x') ) ]

for uniform symbols on the AWGN channel at SNR = GSNR. Both polarizations see
the same SNR, so the value is per polarization, in bit per 2D symbol.

A Gray-labelled square M-QAM is the product of two Gray-labelled √M-PAMs, one
per quadrature, and each bit depends on one quadrature only; so the GMI is
twice that of the PAM, and the expectation over the noise is a 1D integral,
evaluated with Gauss–Hermite quadrature. That keeps it deterministic and
reproducible — no Monte-Carlo sampling in the physics path.

References:
    - Alvarado, Agrell, Lavery, Maher and Bayvel, "Replacing the soft-decision
      FEC limit paradigm in the design of optical communication systems,"
      JLT 33(20):4338–4352, 2015
"""

from __future__ import annotations

import math
from functools import cache

import numpy as np

from twinlight.physics.modulation import ModulationFormat, get_params

# 64 nodes reach a quadrature error below 1e-5 bit at any SNR. They also keep
# the bit-metric sums finite: the largest node satisfies t² ≈ 110, so the
# transmitted symbol's term is at least e^-110 of the largest one and never
# underflows, whatever the SNR.
_HERMITE_NODES = 64


@cache
def _pam_tables(
    bits_per_symbol: int,
) -> tuple[np.ndarray, float, np.ndarray, np.ndarray, np.ndarray]:
    """Per-format constants: PAM levels, energy, bit masks and quadrature.

    Returns:
        levels: the √M PAM amplitudes, (2i − √M + 1).
        energy: mean PAM symbol energy, (M − 1) / 3.
        same_bit: boolean (bits, tx, 1, candidate) mask — whether candidate
            and transmitted symbol share bit k of their Gray label.
        nodes, weights: Gauss–Hermite nodes and weights.
    """
    bits_per_dim = bits_per_symbol // 2
    size = 2**bits_per_dim
    index = np.arange(size)
    levels = 2.0 * index - (size - 1)
    gray = index ^ (index >> 1)
    bits = (gray[None, :] >> np.arange(bits_per_dim)[:, None]) & 1  # (k, i)
    same_bit = bits[:, :, None, None] == bits[:, None, None, :]
    nodes, weights = np.polynomial.hermite.hermgauss(_HERMITE_NODES)
    return levels, (size**2 - 1) / 3.0, same_bit, nodes, weights


def gsnr_to_gmi(gsnr_db: float, fmt: str | ModulationFormat) -> float:
    """Generalized mutual information at a given GSNR.

    Args:
        gsnr_db: Generalised SNR in dB.
        fmt: Modulation format string or enum.

    Returns:
        GMI in bit per 2D symbol (per polarization), in [0, bits_per_symbol].
    """
    bits_per_symbol = get_params(fmt).bits_per_symbol
    levels, energy, same_bit, nodes, weights = _pam_tables(bits_per_symbol)
    sigma = math.sqrt(energy / 10 ** (gsnr_db / 10.0))  # per quadrature

    # Received samples y = x + √2·σ·t at every quadrature node, and the
    # log-likelihood of every candidate symbol: shape (tx, node, candidate).
    y = levels[:, None] + math.sqrt(2.0) * sigma * nodes[None, :]
    loglik = -((y[:, :, None] - levels) ** 2) / (2 * sigma**2)
    lik = np.exp(loglik - loglik.max(axis=-1, keepdims=True))
    all_bits = np.log(lik.sum(axis=-1))
    same = np.log((lik * same_bit).sum(axis=-1))  # (k, tx, node)

    # E over tx (uniform) and noise (Gauss–Hermite: ∫e^{-t²}f = Σ w·f).
    loss = ((all_bits - same) @ weights).sum() / (
        levels.size * math.sqrt(math.pi) * math.log(2)
    )
    gmi = 2 * (bits_per_symbol // 2 - loss)
    return float(min(max(gmi, 0.0), bits_per_symbol))
