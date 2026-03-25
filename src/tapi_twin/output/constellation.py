"""Constellation diagram synthesis from OPM measurements.

Statistical model (OCATA-inspired):
    Treat the received constellation as a GMM (Gaussian Mixture Model) where
    each ideal symbol point is the mean of a 2D Gaussian and the variance is
    derived from OPM metrics (GSNR, linewidth).

    - Sequeira et al., "OCATA: A Deep-Learning-Based Digital Twin for
      Optical Networks," ECOC 2023 — GMM constellation features; our
      synthesis provides OPM-driven GMM parameters.

Parameters from OPM:
    - gsnr-db → σ²_noise = 1/(2·GSNR_lin) for additive circular symmetric noise
    - linewidth (config) → σ²_φ = 2π·(Δν_tx + Δν_lo)·T_sym for phase diffusion

Synthesis:
    r = s · exp(j·φ) + n
    where s is ideal symbol, φ ~ N(0, σ²_φ), n ~ CN(0, σ²_noise)
"""

from __future__ import annotations

import math

import numpy as np

from tapi_twin.physics.modulation import ModulationFormat, get_params


def _ideal_constellation(
    modulation_format: str | ModulationFormat,
) -> np.ndarray:
    """Return normalized ideal constellation points (complex array).

    Points are normalized so that E[|s|²] = 1 (unit average symbol power).
    """
    fmt = ModulationFormat(modulation_format) if isinstance(modulation_format, str) else modulation_format

    if fmt == ModulationFormat.DP_QPSK:
        points = np.array([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j])
    elif fmt == ModulationFormat.DP_16QAM:
        levels = np.array([-3, -1, 1, 3])
        points = np.array([i + 1j * q for i in levels for q in levels])
    elif fmt == ModulationFormat.DP_64QAM:
        levels = np.array([-7, -5, -3, -1, 1, 3, 5, 7])
        points = np.array([i + 1j * q for i in levels for q in levels])
    else:
        raise ValueError(f"Unsupported modulation format: {fmt}")

    avg_power = np.mean(np.abs(points) ** 2)
    return points / math.sqrt(avg_power)


def synthesize_constellation(
    measurements: dict[str, float],
    modulation_format: str,
    n_symbols: int = 10000,
    linewidth_hz: float | None = None,
) -> np.ndarray:
    """Synthesize a received constellation diagram from OPM measurements.

    Args:
        measurements: Dict with at least 'gsnr-db'; other keys ignored.
        modulation_format: One of "DP-QPSK", "DP-16QAM", "DP-64QAM".
        n_symbols: Number of symbols to generate.
        linewidth_hz: Combined TX+LO linewidth [Hz] for phase noise.
            If None, uses a default of 100 kHz (typical ECL).

    Returns:
        Complex ndarray of shape (n_symbols,) representing received symbols.
    """
    params = get_params(modulation_format)
    ideal = _ideal_constellation(modulation_format)

    gsnr_db = measurements.get("gsnr-db", 20.0)
    gsnr_lin = 10 ** (gsnr_db / 10.0)

    noise_var = 1.0 / (2.0 * gsnr_lin)
    noise_sigma = math.sqrt(noise_var)

    eff_linewidth = linewidth_hz if linewidth_hz is not None else 100_000.0
    t_sym = 1.0 / params.baud_rate_hz
    phase_var = 2.0 * math.pi * eff_linewidth * t_sym
    phase_sigma = math.sqrt(phase_var) if phase_var > 0 else 0.0

    rng = np.random.default_rng()
    symbol_indices = rng.integers(0, len(ideal), size=n_symbols)
    tx_symbols = ideal[symbol_indices]

    if phase_sigma > 0:
        phase_noise = rng.normal(0.0, phase_sigma, size=n_symbols)
    else:
        phase_noise = 0.0
    rotated = tx_symbols * np.exp(1j * phase_noise)

    noise_i = rng.normal(0.0, noise_sigma, size=n_symbols)
    noise_q = rng.normal(0.0, noise_sigma, size=n_symbols)
    additive_noise = noise_i + 1j * noise_q

    return rotated + additive_noise


def constellation_to_iq(
    symbols: np.ndarray,
) -> tuple[list[float], list[float]]:
    """Convert complex symbols to I/Q lists for JSON serialization."""
    return np.real(symbols).tolist(), np.imag(symbols).tolist()
