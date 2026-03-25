"""Eye diagram synthesis from OPM measurements.

Statistical model:
    Generate multiple traces of raised-cosine pulses with:
    - Additive Gaussian noise (variance from GSNR)
    - Timing jitter (from PMD/DGD)
    - Optional amplitude variation (PDL)

Parameters from OPM:
    - gsnr-db → σ²_noise = 1/(2·GSNR_lin)
    - pmd-ps → σ_t = pmd_ps·1e-12/√3 (timing jitter)

The result is a collection of traces that can be rendered as a 2D density plot
(time vs. amplitude) to form the characteristic "eye" shape.

References:
    - IMPLEMENTATION_PLAN Phase 5: raised-cosine pulse, jitter from DGD
    - Standard coherent DSP literature for eye diagram interpretation
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from tapi_twin.physics.modulation import get_params


@dataclass
class EyeDiagramData:
    """Container for eye diagram synthesis results."""

    time_ns: np.ndarray
    traces: np.ndarray
    symbol_period_ns: float
    n_traces: int


def _raised_cosine(t: np.ndarray, t_sym: float, roll_off: float) -> np.ndarray:
    """Raised-cosine pulse shape centered at t=0.

    Args:
        t: Time array [s].
        t_sym: Symbol period [s].
        roll_off: Roll-off factor (0 to 1).

    Returns:
        Pulse amplitude at each time point.
    """
    eps = 1e-12
    result = np.zeros_like(t)

    for i, ti in enumerate(t):
        x = ti / t_sym
        if abs(x) < eps:
            result[i] = 1.0
        elif roll_off > 0 and abs(abs(x) - 1.0 / (2.0 * roll_off)) < eps:
            result[i] = (math.pi / 4.0) * np.sinc(1.0 / (2.0 * roll_off))
        else:
            sinc_term = np.sinc(x)
            cos_term = math.cos(math.pi * roll_off * x)
            denom = 1.0 - (2.0 * roll_off * x) ** 2
            if abs(denom) < eps:
                result[i] = sinc_term
            else:
                result[i] = sinc_term * cos_term / denom

    return result


def _generate_pulse_sequence(
    bits: np.ndarray,
    t: np.ndarray,
    t_sym: float,
    roll_off: float,
) -> np.ndarray:
    """Generate a pulse sequence for a given bit pattern.

    Args:
        bits: Binary array (0 or 1).
        t: Time array [s], centered on the middle symbol.
        t_sym: Symbol period [s].
        roll_off: Raised-cosine roll-off factor.

    Returns:
        Amplitude waveform.
    """
    n_bits = len(bits)
    mid_idx = n_bits // 2
    waveform = np.zeros_like(t)

    for i, bit in enumerate(bits):
        amplitude = 2.0 * bit - 1.0
        offset = (i - mid_idx) * t_sym
        pulse = _raised_cosine(t - offset, t_sym, roll_off)
        waveform += amplitude * pulse

    return waveform


def synthesize_eye(
    measurements: dict[str, float],
    modulation_format: str,
    n_traces: int = 500,
    samples_per_symbol: int = 64,
    roll_off: float = 0.2,
    n_symbols_display: int = 2,
) -> EyeDiagramData:
    """Synthesize an eye diagram from OPM measurements.

    Args:
        measurements: Dict with 'gsnr-db' and optionally 'pmd-ps'.
        modulation_format: One of "DP-QPSK", "DP-16QAM", "DP-64QAM".
        n_traces: Number of traces to generate.
        samples_per_symbol: Time resolution per symbol period.
        roll_off: Raised-cosine roll-off factor (0.0–1.0).
        n_symbols_display: Number of symbol periods to display (1–3).

    Returns:
        EyeDiagramData with time_ns, traces, and metadata.
    """
    params = get_params(modulation_format)
    t_sym = 1.0 / params.baud_rate_hz

    gsnr_db = measurements.get("gsnr-db", 20.0)
    gsnr_lin = 10 ** (gsnr_db / 10.0)
    noise_var = 1.0 / (2.0 * gsnr_lin)
    noise_sigma = math.sqrt(noise_var)

    pmd_ps = measurements.get("pmd-ps", 0.0)
    jitter_sigma = pmd_ps * 1e-12 / math.sqrt(3.0) if pmd_ps > 0 else 0.0

    n_samples = n_symbols_display * samples_per_symbol
    half_span = n_symbols_display * t_sym / 2
    t = np.linspace(-half_span, half_span, n_samples)

    rng = np.random.default_rng()
    traces = np.zeros((n_traces, n_samples))

    n_context_bits = n_symbols_display + 2
    for i in range(n_traces):
        bits = rng.integers(0, 2, size=n_context_bits)
        jitter = rng.normal(0.0, jitter_sigma) if jitter_sigma > 0 else 0.0
        t_jittered = t - jitter

        waveform = _generate_pulse_sequence(bits, t_jittered, t_sym, roll_off)
        noise = rng.normal(0.0, noise_sigma, size=n_samples)
        traces[i] = waveform + noise

    time_ns = t * 1e9
    symbol_period_ns = t_sym * 1e9

    return EyeDiagramData(
        time_ns=time_ns,
        traces=traces,
        symbol_period_ns=symbol_period_ns,
        n_traces=n_traces,
    )


def eye_to_density(
    data: EyeDiagramData,
    amplitude_bins: int = 100,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert eye traces to a 2D density histogram.

    Args:
        data: EyeDiagramData from synthesize_eye().
        amplitude_bins: Number of bins for amplitude axis.

    Returns:
        Tuple of (time_edges, amplitude_edges, density_matrix).
    """
    all_amplitudes = data.traces.flatten()
    amp_min, amp_max = np.percentile(all_amplitudes, [1, 99])
    amp_range = amp_max - amp_min
    amp_min -= 0.1 * amp_range
    amp_max += 0.1 * amp_range

    time_edges = data.time_ns
    amplitude_edges = np.linspace(amp_min, amp_max, amplitude_bins + 1)

    density = np.zeros((amplitude_bins, len(time_edges)))
    amp_span = amp_max - amp_min
    for trace in data.traces:
        for j, amp in enumerate(trace):
            bin_idx = int((amp - amp_min) / amp_span * amplitude_bins)
            bin_idx = max(0, min(amplitude_bins - 1, bin_idx))
            density[bin_idx, j] += 1

    density /= data.n_traces

    return time_edges, amplitude_edges, density


def eye_to_json(data: EyeDiagramData, max_traces: int = 200) -> dict:
    """Convert EyeDiagramData to JSON-serializable dict.

    Args:
        data: EyeDiagramData from synthesize_eye().
        max_traces: Maximum number of traces to include (for bandwidth).

    Returns:
        Dict with time_ns, traces (list of lists), and metadata.
    """
    stride = max(1, data.n_traces // max_traces)
    selected_traces = data.traces[::stride]

    return {
        "time_ns": data.time_ns.tolist(),
        "traces": selected_traces.tolist(),
        "symbol_period_ns": data.symbol_period_ns,
        "n_traces": len(selected_traces),
    }
