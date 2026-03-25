"""Polarization transient models: PMD random-walk drift and PDL penalty.

Physical basis
--------------
PMD: The state of polarization drifts due to thermal and mechanical
perturbations.  Each fiber segment independently drifts, and the contributions
add in quadrature (incoherent sum) — the classic PMD random-walk model
(Gordon-Kogelnik, PNAS 2000, Sec. III).  DGD follows Maxwell
distribution;
quadrature accumulation: <DGD²> = Σ <DGD_i²>.
Period and amplitude are configurable (pmd_drift_period_s, pmd_drift_amplitude).

PDL: Each network element (EDFA, fiber, connector, splice) introduces a small
PDL.  PDL accumulates in quadrature (Mecozzi-Shtaif, IEEE PTL 2002, Eq. 7):
<Γ²> = N·<γ²>.  The GSNR penalty uses linear PDL ratio (Lichtman 1995;
Bruyère-Audouin, IEEE PTL 1994):

    penalty_dB = -10·log10(1 / (1 - Γ_lin²/3))

where Γ_lin is the PDL expressed as a linear ratio.
PDL varies over time (pdl_variation_period_s, pdl_variation_amplitude).
"""

from __future__ import annotations

import math

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tapi_twin.config import PolarizationConfig


def delta_pmd_ps(
    t: float,
    fiber_uids: list[str],
    baseline_pmd_ps: float,
    cfg: "PolarizationConfig",
    _phase_fn=None,
) -> float:
    """Time-varying PMD perturbation [ps] from per-fiber SOP drift.

    Args:
        t: Wall-clock time [s].
        fiber_uids: Ordered Fiber UIDs along the path.
        baseline_pmd_ps: GNPy-computed baseline PMD [ps].
        cfg: Polarization config (pmd_drift_period_s, pmd_drift_amplitude).
        _phase_fn: Optional callable(uid, metric) for testing.

    Returns:
        Additional PMD [ps] (positive, adds to baseline).
    """
    from tapi_twin.physics.transients.cascade import hash_phase
    ph = _phase_fn or hash_phase

    n_fibers = max(len(fiber_uids), 1)
    per_fiber_pmd = baseline_pmd_ps / n_fibers
    variance = 0.0
    for uid in fiber_uids:
        phase = ph(uid, "pmd")
        drift = per_fiber_pmd * cfg.pmd_drift_amplitude * math.sin(
            2 * math.pi * t / cfg.pmd_drift_period_s + phase
        )
        variance += drift ** 2
    return math.sqrt(variance)


def delta_gsnr_from_pdl_db(
    t: float,
    all_uids: list[str],
    cfg: "PolarizationConfig",
    _phase_fn=None,
) -> float:
    """GSNR penalty [dB] from time-varying PDL accumulation.

    Args:
        t: Wall-clock time [s].
        all_uids: All element UIDs on the path (Fiber + EDFA + connectors).
        cfg: Polarization config (pdl_per_element_db, variation
            period/amplitude).
        _phase_fn: Optional callable(uid, metric) for testing.

    Returns:
        Delta GSNR [dB] (negative — PDL always degrades GSNR).
    """
    from tapi_twin.physics.transients.cascade import hash_phase
    ph = _phase_fn or hash_phase

    total_pdl_sq = 0.0
    for uid in all_uids:
        phase = ph(uid, "pdl")
        el_pdl = cfg.pdl_per_element_db * (
            1.0 + cfg.pdl_variation_amplitude * math.sin(
                2 * math.pi * t / cfg.pdl_variation_period_s + phase
            )
        )
        total_pdl_sq += el_pdl ** 2
    total_pdl_db = math.sqrt(total_pdl_sq)

    # Convert dB PDL to linear ratio for correct penalty computation.
    # Lichtman 1995; Bruyère-Audouin IEEE PTL 1994:
    #   Γ_lin = (10^(PDL_dB/10) - 1) / (10^(PDL_dB/10) + 1)
    #   penalty = -10·log10(1 / (1 - Γ_lin²/3))
    if total_pdl_db < 1e-9:
        return 0.0
    pdl_lin_power = 10.0 ** (total_pdl_db / 10.0)
    gamma_lin = (pdl_lin_power - 1.0) / (pdl_lin_power + 1.0)
    penalty_db = -10.0 * math.log10(
        1.0 / (1.0 - gamma_lin**2 / 3.0)
    )
    return penalty_db  # negative (degrades GSNR)
