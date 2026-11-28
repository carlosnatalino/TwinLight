"""Polarization transient models: PMD random-walk drift and PDL OSNR penalty.

Physical basis
--------------
PMD: The state of polarization drifts due to thermal and mechanical
perturbations.  The DGDs of concatenated fiber sections add in quadrature
(incoherent sum), the classic PMD random-walk model (Gordon-Kogelnik, PNAS
2000, Sec. III): DGD follows a Maxwell distribution and <DGD²> = Σ <DGD_i²>.
Each fiber's DGD scales with the square root of its length (the PMD
coefficient is in ps/√km), so fiber i carries τᵢ ∝ √Lᵢ, normalised so that
√(Σ τᵢ²) is the baseline PMD.  Each τᵢ drifts sinusoidally,

    τᵢ(t) = τᵢ · (1 + a · sin(2πt/T + φᵢ)),

and the path's PMD is the quadrature sum of the drifting DGDs, so the drift
is τ(t) − τ = √(Σ τᵢ(t)²) − √(Σ τᵢ²): signed, with period T.  The
amplitude a and the period T are configurable (pmd_drift_amplitude,
pmd_drift_period_s); φᵢ comes from the fiber UID.

PDL: The hinge model of Zarkosvky & Shtaif, "Statistical distribution of
polarization-dependent loss in systems characterized by the hinge model",
Opt. Lett. 45(5):1224-1227 (2020).  A coherent link is a cascade of N discrete
PDL elements (*hinges*) — ROADMs/WSSs and EDFAs — separated by fiber spans
whose birefringence fully randomizes the signal SOP, so the SOP entering each
hinge is uniformly distributed on the Poincaré sphere.

  - Eq. 3: per-hinge power transfer matrix Tⱼ†Tⱼ = (I + γⱼ·σ)/√(1-γⱼ²), with
    PDL vector magnitude γⱼ ∈ [0,1).  The hinge PDL ratio is (1+γⱼ)/(1-γⱼ), in
    dB ρ_dB = 10·log₁₀((1+γⱼ)/(1-γⱼ)).
  - Eq. 4: the whole-link power transfer has the same form with the link PDL
    vector Γ; here the per-hinge transfers are cascaded directly.
  - Eq. 5: the power attenuation a signal experiences crossing hinge j is
    aⱼ = (1 + γⱼ·cosθⱼ)/√(1-γⱼ²), uniformly distributed because cosθⱼ ~ U(-1,1)
    for a SOP uniform on the Poincaré sphere.

The signal crosses every hinge (a_sig = ∏ aⱼ) while ASE noise injected at a
given amplifier only crosses the hinges *after* it.  PDL therefore manifests
as a time-varying OSNR penalty whose magnitude depends on the ASE distribution
(D'Amico, OFC 2023, W1E.6; Miotto, OFC 2025, M3F.5).  cosθⱼ drifts with
wall-clock time as fiber birefringence changes (sop_drift_period_s).

Stochastic per-hinge PDL.  The hinge model takes each γⱼ as a fixed measured
quantity, but in real WSS-based nodes the PDL is itself stochastic — port- and
frequency-dependent — and is well modeled as Maxwell-distributed (Mecozzi &
Shtaif, IEEE PTL 2002; Borraccini et al., IPC 2023; Miotto, OFC 2025).  Each
hinge therefore draws one PDL value from a Maxwell distribution whose mean is
the configured per-ROADM/per-EDFA value; the draw is deterministically seeded
by the hinge UID so the value is stable for the lifetime of a service.

Ergodic joint coverage.  Each hinge drifts with a slightly different
(incommensurate) period — the configured sop_drift_period_s detuned by a
small, UID-dependent factor.  This turns the joint alignment trajectory
(cosθ₁,…,cosθ_N) from a closed 1-D loop into a quasi-periodic path that densely
fills the N-dimensional alignment space over long run times, so the long-run
joint statistics converge to the independent-alignment ensemble assumed by the
hinge model — without an explicit Monte Carlo pass.

Note: aⱼ has arithmetic mean 1/√(1-γⱼ²) > 1 because Eq. 5 omits the
polarization-*independent* loss term (αⱼ₀ in Zarkosvky Eq. 1), which the GNPy
baseline already accounts for.  For realistic per-hinge PDL this bias is well
below 0.1 dB over a path; the physically meaningful effect is the fluctuation.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twinlight.config import PolarizationConfig

# Mean → scale conversion for the Maxwell distribution: a Maxwell variate is
# the magnitude of a 3-D Gaussian with iid N(0, σ²) components, with
# mean = 2σ·√(2/π), hence σ = mean·√(π/8).
_MAXWELL_MEAN_TO_SIGMA = math.sqrt(math.pi / 8.0)

# Upper cap on a per-hinge PDL draw, as a multiple of the configured mean.
# The Maxwell tail is light (∝ x²·e^(-x²)); this only trims absurd outliers,
# echoing the truncation Miotto (OFC 2025) applies to the Maxwell pdf.
_MAXWELL_TRUNCATION_FACTOR = 4.0

# Peak fractional detuning of the per-hinge SOP-drift period around the
# configured base period (±25%).  Makes per-hinge periods incommensurate so
# the joint alignment trajectory ergodically fills the alignment space.
_PERIOD_SPREAD = 0.25


def _seeded_unit(uid: str, salt: str) -> float:
    """Deterministic value in [0, 1) from a hinge UID and a salt string.

    Uses an MD5 digest (matching ``cascade.hash_phase``) so the result is
    stable across processes and independent of PYTHONHASHSEED.
    """
    digest = hashlib.md5(
        f"{uid}:{salt}".encode(), usedforsecurity=False
    ).hexdigest()
    return (int(digest, 16) % 1_000_000) / 1_000_000.0


def delta_pmd_ps(
    t: float,
    fiber_uids: list[str],
    baseline_pmd_ps: float,
    cfg: PolarizationConfig,
    fiber_lengths_km: list[float] | None = None,
    _phase_fn=None,
) -> float:
    """Time-varying PMD perturbation [ps] from per-fiber DGD drift.

    The path's DGD is the quadrature sum of its fibers' DGDs (Gordon &
    Kogelnik, PNAS 2000, Sec. III), and each fiber's drifts by
    ±``pmd_drift_amplitude`` around its share τᵢ ∝ √Lᵢ of the baseline
    (module docstring):

        ΔPMD(t) = √(Σ (τᵢ · (1 + a·sin(2πt/T + φᵢ)))²) − √(Σ τᵢ²)

    Args:
        t: Wall-clock time [s].
        fiber_uids: Ordered Fiber UIDs along the path.
        baseline_pmd_ps: Baseline PMD of the path [ps], √(Σ τᵢ²).
        cfg: Polarization config (pmd_drift_period_s, pmd_drift_amplitude).
        fiber_lengths_km: Length of each fiber in ``fiber_uids`` [km]; the
            fibers are equal sections when omitted or not one per fiber.
        _phase_fn: Optional callable(uid, metric) for testing.

    Returns:
        PMD change [ps], signed: within ±a of the baseline, and exactly ±a·PMD
        on a single-fiber path.  Zero on a path with no fiber.
    """
    from twinlight.physics.transients.cascade import hash_phase
    ph = _phase_fn or hash_phase

    if not fiber_uids or baseline_pmd_ps <= 0.0:
        return 0.0
    lengths = fiber_lengths_km or []
    if len(lengths) != len(fiber_uids) or sum(lengths) <= 0.0:
        lengths = [1.0] * len(fiber_uids)
    # τᵢ² ∝ Lᵢ, normalised so that Σ τᵢ² is the baseline PMD squared.
    total_km = sum(lengths)
    drifted_sq = 0.0
    for uid, length_km in zip(fiber_uids, lengths, strict=True):
        tau_sq = baseline_pmd_ps ** 2 * max(length_km, 0.0) / total_km
        factor = 1.0 + cfg.pmd_drift_amplitude * math.sin(
            2 * math.pi * t / cfg.pmd_drift_period_s + ph(uid, "pmd")
        )
        drifted_sq += tau_sq * factor ** 2
    return math.sqrt(drifted_sq) - baseline_pmd_ps


def _pdl_db_to_gamma(pdl_db: float) -> float:
    """PDL vector magnitude γ from a hinge PDL value in dB.

    Inverts Zarkosvky & Shtaif (Opt. Lett. 2020) Eq. 3: the hinge PDL ratio is
    (1+γ)/(1-γ), so ρ_dB = 10·log₁₀((1+γ)/(1-γ)) and
        γ = (10^(ρ_dB/10) - 1) / (10^(ρ_dB/10) + 1).
    """
    if pdl_db <= 0.0:
        return 0.0
    rho = 10.0 ** (pdl_db / 10.0)
    return (rho - 1.0) / (rho + 1.0)


def _maxwell_pdl_db(mean_pdl_db: float, uid: str) -> float:
    """Per-hinge PDL [dB] drawn from a Maxwell distribution.

    The hinge model (Zarkosvky 2020) treats the per-element PDL as a fixed
    measured quantity, but real WSS-based nodes exhibit a stochastic, port- and
    frequency-dependent PDL that is well modeled as Maxwell-distributed
    (Mecozzi & Shtaif, IEEE PTL 2002; Borraccini et al., IPC 2023; Miotto,
    OFC 2025).  Each hinge draws one value, deterministically seeded by its
    UID, so the PDL is stable for the lifetime of a service.

    A Maxwell variate is the magnitude of a 3-D Gaussian with iid N(0, σ²)
    components; ``mean_pdl_db`` is the *mean* of the distribution, so
    σ = mean·√(π/8).  The rare upper tail is truncated (Miotto OFC 2025).

    Args:
        mean_pdl_db: Mean PDL [dB] of the Maxwell distribution.
        uid: Hinge UID — seeds the deterministic draw.

    Returns:
        The drawn per-hinge PDL [dB] (≥ 0).
    """
    if mean_pdl_db <= 0.0:
        return 0.0
    sigma = mean_pdl_db * _MAXWELL_MEAN_TO_SIGMA
    # Deterministic per-hinge RNG seeded from the UID (process-independent).
    seed = int(
        hashlib.md5(
            f"{uid}:pdl-maxwell".encode(), usedforsecurity=False
        ).hexdigest(),
        16,
    )
    rng = random.Random(seed)
    g1, g2, g3 = (rng.gauss(0.0, sigma) for _ in range(3))
    pdl_db = math.sqrt(g1 * g1 + g2 * g2 + g3 * g3)
    return min(pdl_db, mean_pdl_db * _MAXWELL_TRUNCATION_FACTOR)


def _hinge_period_s(uid: str, base_period_s: float) -> float:
    """Per-hinge SOP-drift period [s] — base period detuned per hinge.

    Each hinge's drift period is the configured ``base_period_s`` scaled by a
    small, deterministic, UID-dependent factor in 1 ± _PERIOD_SPREAD.  Giving
    hinges mutually incommensurate periods turns the joint alignment
    trajectory (cosθ₁,…,cosθ_N) from a closed 1-D loop into a quasi-periodic,
    ergodic path that densely fills the N-dimensional alignment space over
    long run times.  The per-hinge marginals stay uniform on [-1,1]; the
    long-run joint statistics then converge to the independent-alignment
    ensemble the hinge model assumes — without a Monte Carlo pass.
    """
    detune = 2.0 * _seeded_unit(uid, "pdl-period") - 1.0   # → [-1, 1)
    return base_period_s * (1.0 + _PERIOD_SPREAD * detune)


def _cos_theta(t: float, uid: str, period_s: float, phase: float) -> float:
    """Signal-to-hinge SOP alignment cosθ — triangle wave over [-1,1].

    The hinge model (Zarkosvky 2020) assumes the SOP entering each hinge is
    uniform on the Poincaré sphere, i.e. cosθ ~ U(-1,1) — which is exactly what
    makes the per-hinge attenuation of Eq. 5 uniformly distributed.

    Assumption: to keep the transient deterministic in wall-clock time t
    (consistent with the other transient models in this package) while
    preserving the *uniform* marginal that Eq. 5 requires, cosθ is modeled as a
    triangle wave — a linear sweep of [-1,1] — rather than a sinusoid (whose
    marginal would be arcsine-distributed, over-weighting the ±1 extremes).
    Each hinge carries an independent phase offset so the alignments of
    different hinges are uncorrelated, as the hinge model assumes.
    """
    frac = ((t / period_s) + phase / (2.0 * math.pi)) % 1.0
    # Triangle wave: frac 0 → 0.5 → 1 maps to cosθ -1 → +1 → -1.
    return 1.0 - 4.0 * abs(frac - 0.5)


def delta_osnr_from_pdl_db(
    t: float,
    pdl_elements: list[tuple[str, str]],
    cfg: PolarizationConfig,
    _phase_fn=None,
) -> float:
    """PDL-induced OSNR penalty [dB] from the Zarkosvky hinge model.

    Cascades the per-hinge power transfers of Zarkosvky & Shtaif (Opt. Lett.
    2020) Eqs. 3-5 and forms the OSNR penalty per D'Amico (OFC 2023) and
    Miotto (OFC 2025): the signal crosses every hinge while ASE noise crosses
    only the hinges downstream of where it is injected.

    Each hinge draws a Maxwell-distributed PDL (``_maxwell_pdl_db``) and drifts
    with its own incommensurate period (``_hinge_period_s``), both seeded
    deterministically by the hinge UID.

    Args:
        t: Wall-clock time [s].
        pdl_elements: Ordered (uid, kind) hinges on the path, kind in
            {"Roadm", "Edfa"}.
        cfg: Polarization config (pdl_per_roadm_db, pdl_per_edfa_db,
            sop_drift_period_s, ase_distribution).
        _phase_fn: Optional callable(uid, metric) for testing.

    Returns:
        Delta OSNR [dB].  Fluctuates around 0 as the SOP drifts and dips
        negative (penalty) at adverse alignments.  Returns 0.0 when the path
        has no hinges or under the 'tx' ASE assumption.
    """
    from twinlight.physics.transients.cascade import hash_phase
    ph = _phase_fn or hash_phase

    if not pdl_elements:
        return 0.0

    # 'tx': ASE injected at the transmitter crosses the same hinges as the
    # signal, so the OSNR is conserved end-to-end — no PDL-induced OSNR penalty
    # (D'Amico OFC 2023, Scenario TX).
    if cfg.ase_distribution == "tx":
        return 0.0

    # Per-hinge power transfer aⱼ(t) — Zarkosvky 2020 Eq. 5:
    #   aⱼ = (1 + γⱼ·cosθⱼ) / √(1-γⱼ²)
    a_list: list[float] = []
    edfa_idx: list[int] = []
    for idx, (uid, kind) in enumerate(pdl_elements):
        # Configured value is the mean of a per-hinge Maxwell-distributed PDL.
        mean_pdl_db = (
            cfg.pdl_per_edfa_db if kind == "Edfa" else cfg.pdl_per_roadm_db
        )
        gamma = _pdl_db_to_gamma(_maxwell_pdl_db(mean_pdl_db, uid))
        # Each hinge drifts with its own incommensurate period for ergodic
        # coverage of the joint alignment space.
        period_s = _hinge_period_s(uid, cfg.sop_drift_period_s)
        cos_theta = _cos_theta(t, uid, period_s, ph(uid, "pdl"))
        a_j = (1.0 + gamma * cos_theta) / math.sqrt(1.0 - gamma ** 2)
        a_list.append(a_j)
        if kind == "Edfa":
            edfa_idx.append(idx)

    # The signal crosses every hinge — whole-link transfer (Zarkosvky Eq. 4).
    a_sig = math.prod(a_list)

    # 'rx': all ASE injected at the receiver crosses no hinge, so the penalty
    # is the full signal-power fluctuation (D'Amico OFC 2023, Scenario RX).
    # Also the fallback when the path carries no EDFA noise source.
    if cfg.ase_distribution == "rx" or not edfa_idx:
        return 10.0 * math.log10(a_sig)

    # 'distributed': equal ASE injected at each EDFA (Miotto OFC 2025).  ASE
    # generated at the EDFA at index k only crosses the hinges after it; the
    # received noise is the mean over the EDFA sources.
    noise_factors = [
        math.prod(a_list[k + 1:]) if k + 1 < len(a_list) else 1.0
        for k in edfa_idx
    ]
    mean_noise = sum(noise_factors) / len(noise_factors)
    return 10.0 * math.log10(a_sig / mean_noise)
