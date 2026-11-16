"""GN-model closed-form noise terms for a lightpath, element by element.

Implementation of the Gaussian-Noise (GN) model — the analytical
alternative to GNPy's split-step simulation. Heavily inspired by the
``optical-networking-gym`` (EGN) project's
``optical/kernels/qot_kernel.py`` (used here with the original
author's blessing — same person owns both projects).

Underlying formulas:

* Poggiolini, Bosco, Carena, Curri, Jiang, Forghieri,
  "The GN-Model of Fiber Non-Linear Propagation and its Applications",
  IEEE/OSA J. Lightwave Technol. 32(4):694 (2014); arXiv:1209.0394,
  Eqs. 120 and 123 — the incoherent closed form for the NLI one channel
  (the "pump") generates on another (the "channel under test", CUT). The
  self-channel term is the same expression with the CUT as its own pump.
  GNPy implements exactly this (``gnpy.core.science_utils.NliSolver.
  _gn_analytic`` and ``_psi``), which the tests check to 1e-9.
* Carena, Curri, Bosco, Poggiolini, Forghieri,
  "Modeling of the Impact of Nonlinear Propagation Effects in
  Uncompensated Optical Coherent Transmission Links",
  IEEE/OSA J. Lightwave Technol. 30(10):1524 (2012) — incoherent
  accumulation of the per-span noise.
* Carena et al., "EGN model of non-linear fiber propagation",
  Opt. Express 22(13):16335 (2014).
* Curri, "GNPy: A network-oriented open-source GN/EGN-model library",
  IEEE/OSA J. Lightwave Technol. 40(11):3498 (2022) — the amplifier noise
  convention (ASE referred to the amplifier input, as GNPy's
  ``Edfa.noise_profile``).

Each noise source is compared with the signal power where it is added,
and the inverse SNRs add along the path (Carena 2012 §IV: inter-span
correlation neglected):

    1/GSNR = Σ_amplifiers P_ASE,i / P_in,i + Σ_fibers P_NLI,j / P_in,j

which is how GNPy accumulates noise on a propagated carrier too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Standard SSMF parameters, used when a fiber's own are unknown (no
# equipment library): |β₂| ≈ 21.3 ps²/km (D = 16.7 ps/nm/km at 1550 nm)
# and the γ of GNPy's SSMF type.
DEFAULT_BETA_2_S2_PER_M = 21.3e-27
DEFAULT_GAMMA_PER_W_PER_M = 1.265e-3
_H_PLANCK_J_S = 6.62607015e-34
_PI_SQ = math.pi * math.pi

# Weights of the incoherent GN model (Poggiolini 2014 Eq. 120; gnpy
# NliSolver.SPM_WEIGHT and XPM_WEIGHT): 16/27 for the channel on itself,
# twice that for each other channel.
_SPM_WEIGHT = 16.0 / 27.0
_XPM_WEIGHT = 2.0 * _SPM_WEIGHT


@dataclass(frozen=True)
class FiberSpan:
    """The fiber parameters the NLI terms need.

    ``attenuation_neper_per_m`` is the **field** attenuation coefficient α
    (convert from dB/km with :func:`db_per_km_to_neper_per_m`): the power
    loss is ``exp(2αL)``. ``beta2_s2_per_m`` is |β₂|.
    """

    length_m: float
    attenuation_neper_per_m: float
    gamma_per_w_per_m: float = DEFAULT_GAMMA_PER_W_PER_M
    beta2_s2_per_m: float = DEFAULT_BETA_2_S2_PER_M


@dataclass(frozen=True)
class Neighbour:
    """Another channel in the same fiber: its offset from the channel under
    test, its power at the fiber input, and its bandwidth (symbol rate)."""

    offset_hz: float
    power_w: float
    bandwidth_hz: float


def db_per_km_to_neper_per_m(loss_db_per_km: float) -> float:
    """Convert fiber loss from dB/km (operator units) to the field
    attenuation coefficient α in Neper/m (SI).

    The dB figure operators quote is a *power* ratio, so the conversion
    carries a factor of one half:

        α_field[Np/m] = α[dB/km] · (ln 10 / 10) · 1e-3 / 2

    Dropping that half doubles every span's modelled loss (an 18 dB span is
    charged 36 dB).
    """
    return loss_db_per_km * (math.log(10) / 10.0) * 1e-3 / 2.0


def nf_db_to_linear(nf_db: float) -> float:
    """Convert an amplifier noise figure from dB to a linear power ratio."""
    return 10.0 ** (nf_db / 10.0)


def dbm_to_w(power_dbm: float) -> float:
    """Convert a power from dBm to W."""
    return 10.0 ** (power_dbm / 10.0) * 1e-3


def _effective_length_m(alpha: float, length_m: float) -> float:
    """L_eff = (1 − exp(−2αL)) / (2α), with α the field coefficient."""
    return (1.0 - math.exp(-2.0 * alpha * length_m)) / (2.0 * alpha)


def ase_power(
    noise_figure_linear: float, bandwidth_hz: float, centre_frequency_hz: float,
) -> float:
    """ASE power of one amplifier in the channel bandwidth, referred to its
    input: ``NF · h · ν · B`` (GNPy ``Edfa.noise_profile``; Curri 2022).

    Compare it with the signal power at the amplifier input.
    """
    return noise_figure_linear * _H_PLANCK_J_S * centre_frequency_hz * bandwidth_hz


def _psi(fiber: FiberSpan, cut_bandwidth_hz: float, pump: Neighbour) -> float:
    """ψ of Poggiolini 2014 Eq. 123, as GNPy's ``NliSolver._psi``:

        ψ(Δf) = ½ [asinh(π² L_a |β₂| B_cut (Δf + B_pump/2))
                  − asinh(π² L_a |β₂| B_cut (Δf − B_pump/2))]
                · L_eff² / (2π |β₂| L_a)

    The paper's α is the power coefficient (2α here), so the asymptotic
    length is L_a = 1/(2α) and L_eff = (1 − e^(−2αL))/(2α).
    """
    alpha = fiber.attenuation_neper_per_m
    beta2 = fiber.beta2_s2_per_m
    l_eff = _effective_length_m(alpha, fiber.length_m)
    l_a = 1.0 / (2.0 * alpha)
    k = _PI_SQ * l_a * beta2 * cut_bandwidth_hz
    half = pump.bandwidth_hz / 2.0
    return 0.5 * (
        math.asinh(k * (pump.offset_hz + half)) - math.asinh(k * (pump.offset_hz - half))
    ) * l_eff * l_eff / (2.0 * math.pi * beta2 * l_a)


def self_channel_nli_power(fiber: FiberSpan, power_w: float, bandwidth_hz: float) -> float:
    """NLI the channel generates on itself in one fiber, at the fiber input.

    Poggiolini 2014 Eqs. 120/123 with the channel as its own pump (Δf = 0):

        P_NLI = (16/27) · γ² · ψ(0) / B² · P³,
        ψ(0) = asinh((π²/2) |β₂| L_a B²) · L_eff² / (2π |β₂| L_a)

    which is the single-channel GN closed form
    (8/27) γ² P³ L_eff² asinh((π²/2)|β₂| L_a B²) / (π |β₂| L_a B²).
    """
    if fiber.attenuation_neper_per_m <= 0.0 or fiber.length_m <= 0.0:
        return 0.0
    psi = _psi(fiber, bandwidth_hz, Neighbour(0.0, power_w, bandwidth_hz))
    return _SPM_WEIGHT * fiber.gamma_per_w_per_m ** 2 * psi / bandwidth_hz ** 2 * power_w ** 3


def cross_channel_nli_power(
    fiber: FiberSpan,
    cut_power_w: float,
    cut_bandwidth_hz: float,
    neighbours: list[Neighbour],
) -> float:
    """NLI the other channels in the fiber generate on the channel under test.

    Incoherent GN model, Poggiolini 2014 Eq. 120 with the ψ of Eq. 123,
    written as GNPy's ``_gn_analytic`` writes it:

        P_NLI = Σ_pump (32/27) · γ² · ψ(Δf) / B_pump² · P_cut · P_pump²

    All powers are at the fiber input.
    """
    if fiber.attenuation_neper_per_m <= 0.0 or fiber.length_m <= 0.0:
        return 0.0
    gamma_sq = fiber.gamma_per_w_per_m ** 2
    return sum(
        _XPM_WEIGHT * gamma_sq * _psi(fiber, cut_bandwidth_hz, pump)
        / pump.bandwidth_hz ** 2 * cut_power_w * pump.power_w ** 2
        for pump in neighbours
    )


@dataclass(frozen=True)
class PathQoT:
    """Accumulated noise of one channel along a path, in dB."""

    gsnr_db: float       # signal / (ASE + NLI)
    osnr_ase_db: float   # signal / ASE, in the channel bandwidth
    snr_nli_db: float    # signal / NLI


@dataclass
class NoiseBudget:
    """Inverse SNRs of one channel, accumulated element by element.

    Each noise power is compared with the channel's own power where the
    noise is added, so the sum holds whatever the power levels along the
    line are (Carena 2012 §IV).
    """

    inverse_osnr: float = field(default=0.0)
    inverse_snr_nli: float = field(default=0.0)

    def add_ase(self, ase_w: float, signal_w: float) -> None:
        self.inverse_osnr += ase_w / signal_w

    def add_nli(self, nli_w: float, signal_w: float) -> None:
        self.inverse_snr_nli += nli_w / signal_w

    def result(self) -> PathQoT:
        """The accumulated SNRs; infinite for a noiseless path."""

        def db(inverse: float) -> float:
            return -10.0 * math.log10(inverse) if inverse > 0.0 else math.inf

        return PathQoT(
            gsnr_db=db(self.inverse_osnr + self.inverse_snr_nli),
            osnr_ase_db=db(self.inverse_osnr),
            snr_nli_db=db(self.inverse_snr_nli),
        )
