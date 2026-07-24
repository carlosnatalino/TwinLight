"""GN/EGN closed-form noise model for a multi-span lightpath.

Implementation of the Gaussian-Noise (GN) model — the analytical
alternative to GNPy's split-step simulation. Heavily inspired by the
``optical-networking-gym`` (EGN) project's
``optical/kernels/qot_kernel.py`` (used here with the original
author's blessing — same person owns both projects), simplified to
the single-channel MVP scope: self-channel NLI only, neighbour-channel
XPM/FWM is a TODO at the bottom.

Underlying formulas:

* Carena, Curri, Bosco, Poggiolini, Forghieri,
  "Modeling of the Impact of Nonlinear Propagation Effects in
  Uncompensated Optical Coherent Transmission Links",
  IEEE/OSA J. Lightwave Technol. 30(10):1524 (2012).
* Carena et al., "EGN model of non-linear fiber propagation",
  Opt. Express 22(13):16335 (2014).
* Curri, "GNPy: A network-oriented open-source GN/EGN-model library",
  IEEE/OSA J. Lightwave Technol. 40(11):3498 (2022).

The model gives, for a single channel propagated over a chain of
amplified fiber spans:

    GSNR = P_signal / (P_ASE + P_NLI)

with per-span contributions accumulated linearly in the noise-power
domain (Eq. 1-3 of Carena 2012). Self-channel NLI uses Carena 2012's
closed-form integral; cross-channel XPM/FWM from neighbouring services
is not yet modelled here (TODO at the bottom).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Standard SSMF parameters — same defaults Curri 2022 uses for GNPy's
# reference C-band channel.
_BETA_2_S2_PER_M = 21.3e-27       # |β₂| group-velocity-dispersion parameter
_GAMMA_PER_W_PER_M = 1.3e-3       # γ Kerr nonlinearity coefficient
_H_PLANCK_J_S = 6.626e-34
_PI_SQ = math.pi * math.pi


@dataclass(frozen=True)
class SpanInputs:
    """Per-span physical parameters needed by the GN-model kernel.

    Units are SI: length in metres, attenuation in 1/m (Neper/m, *not*
    dB/km — convert with ``db_per_km_to_neper_per_m`` below), linear
    noise figure (not dB).
    """

    length_m: float
    attenuation_neper_per_m: float
    noise_figure_linear: float


def db_per_km_to_neper_per_m(loss_db_per_km: float) -> float:
    """Convert fiber loss from dB/km (operator units) to the field
    attenuation coefficient α in Neper/m (SI).

    The GN-model formulas in this module follow the standard Carena 2012
    convention where α is the **field** (amplitude) attenuation, so that
    the span *power* loss is ``exp(2·α·L)`` and the effective length is
    ``(1 − exp(−2·α·L)) / (2·α)``. The dB figure operators quote is a
    *power* ratio, so the conversion carries a factor of one half:

        α_field[Np/m] = α[dB/km] · (ln 10 / 10) · 1e-3 / 2

    Dropping that half — treating the power coefficient as if it were the
    field coefficient — doubles every span's modelled loss (an 18 dB span
    is charged 36 dB), which inflates ASE by tens of dB over a long path.
    """
    return loss_db_per_km * (math.log(10) / 10.0) * 1e-3 / 2.0


def nf_db_to_linear(nf_db: float) -> float:
    """Convert an EDFA noise figure from dB to a linear power ratio."""
    return 10.0 ** (nf_db / 10.0)


def _effective_length_m(alpha: float, length_m: float) -> float:
    """L_eff = (1 - exp(-2αL)) / (2α)  — Carena 2012 §III.A.

    The "1/e amplitude" reach within a single span, used for both the
    NLI integration and the L_eff_a (= 1/(2α)) limit for long spans.
    """
    return (1.0 - math.exp(-2.0 * alpha * length_m)) / (2.0 * alpha)


def per_span_ase_power(
    span: SpanInputs,
    bandwidth_hz: float,
    centre_frequency_hz: float,
) -> float:
    """ASE noise power emitted by the EDFA that terminates this span.

    Standard amplifier ASE formula:

        P_ASE = NF_lin · h · ν · (G - 1) · B

    where the amplifier gain G compensates the span loss exactly, so
    G = exp(α_power · L) = exp(2α_amp · L) with α_amp the
    amplitude-attenuation in Neper/m.
    """
    gain_minus_one = math.exp(
        2.0 * span.attenuation_neper_per_m * span.length_m
    ) - 1.0
    return (
        span.noise_figure_linear
        * _H_PLANCK_J_S
        * centre_frequency_hz
        * gain_minus_one
        * bandwidth_hz
    )


def per_span_self_nli_power(
    span: SpanInputs,
    launch_power_w: float,
    bandwidth_hz: float,
) -> float:
    """Self-channel NLI noise emitted by this span (no neighbours).

    Carena 2012 / Curri 2022 single-span GN expression (single Gaussian
    channel of bandwidth B, span attenuation α, dispersion β₂):

        G_NLI = (8/27) · (γ²/(π·|β₂|))
              · P_ch³ / B³
              · L_eff
              · asinh( (π²/4) · |β₂| · B² · L_eff_a )

    where L_eff = (1 - e^(-2αL))/(2α) and L_eff_a = 1/(2α). We then
    multiply by B to get the NLI power within the channel bandwidth.
    """
    alpha = span.attenuation_neper_per_m
    if alpha <= 0.0 or span.length_m <= 0.0:
        return 0.0
    l_eff = _effective_length_m(alpha, span.length_m)
    l_eff_a = 1.0 / (2.0 * alpha)
    pre = (
        (8.0 / 27.0)
        * (_GAMMA_PER_W_PER_M ** 2)
        / (math.pi * _BETA_2_S2_PER_M)
    )
    # Inner integral collapses to asinh for a single rectangular Gaussian
    # channel (Carena 2012 Eq. 13 in the high-bandwidth / fully-coherent
    # regime). Multiply by B at the end to get integrated NLI power.
    psd_factor = math.asinh(
        _PI_SQ * _BETA_2_S2_PER_M * (bandwidth_hz ** 2) * l_eff_a / 4.0
    )
    g_nli = pre * (launch_power_w ** 3 / bandwidth_hz ** 3) * l_eff * psd_factor
    return g_nli * bandwidth_hz


@dataclass(frozen=True)
class PathQoT:
    """Aggregated link-noise results returned by ``accumulate_path_noise``."""

    gsnr_db: float           # 10·log10(P_signal / (P_ASE + P_NLI))
    osnr_ase_db: float       # 10·log10(P_signal / P_ASE)
    p_ase_w: float           # Total accumulated ASE power [W]
    p_nli_w: float           # Total accumulated NLI power [W]


def accumulate_path_noise(
    spans: list[SpanInputs],
    *,
    launch_power_w: float,
    bandwidth_hz: float,
    centre_frequency_hz: float,
) -> PathQoT:
    """Aggregate ASE and NLI over a chain of amplified spans.

    Noise powers add linearly in this model (the inter-span correlation
    is neglected — Carena 2012 §IV) so the per-span contributions
    accumulate directly. GSNR is then the launch power divided by the
    total accumulated noise, in dB.
    """
    if launch_power_w <= 0.0:
        raise ValueError("launch_power_w must be positive")
    if not spans:
        # Empty path (e.g. TRX-attached SIP-to-SIP with no fiber) — no
        # propagation loss, no noise; return an idealised SNR floor.
        return PathQoT(gsnr_db=float("inf"), osnr_ase_db=float("inf"),
                       p_ase_w=0.0, p_nli_w=0.0)

    p_ase_total = 0.0
    p_nli_total = 0.0
    for span in spans:
        p_ase_total += per_span_ase_power(
            span, bandwidth_hz, centre_frequency_hz,
        )
        p_nli_total += per_span_self_nli_power(
            span, launch_power_w, bandwidth_hz,
        )

    osnr_ase_db = (
        10.0 * math.log10(launch_power_w / p_ase_total)
        if p_ase_total > 0.0 else float("inf")
    )
    total_noise = p_ase_total + p_nli_total
    gsnr_db = (
        10.0 * math.log10(launch_power_w / total_noise)
        if total_noise > 0.0 else float("inf")
    )
    return PathQoT(
        gsnr_db=gsnr_db,
        osnr_ase_db=osnr_ase_db,
        p_ase_w=p_ase_total,
        p_nli_w=p_nli_total,
    )


# Future work: cross-channel XPM/FWM from neighbouring services on
# shared spans, per Carena 2014 §IV. Would take a list of
# (centre_frequency, bandwidth, launch_power, modulation_phi) tuples
# for every other service active on each span.
