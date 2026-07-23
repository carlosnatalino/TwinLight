"""Closed-form formulas for the metrics the EGN backend doesn't compute.

The EGN QoT engine produces OSNR, ASE, and NLI per path. ``OpmBaseline``
also carries CD, PMD, and latency, which the transient layer perturbs
over time. Without a meaningful baseline value the transient layer
multiplies against zero and the UI shows flat lines for those metrics.

So when the EGN backend builds an ``OpmBaseline``, we populate the
missing fields from these closed-form expressions using total path
length and SSMF defaults:

* CD = D · L_total, with D = 16.7 ps/(nm·km) (G.652 reference).
* PMD = pmd_coef · √L_total (Gordon-Kogelnik random-walk).
* latency = L_total · n_eff / c (group-velocity propagation).

These are exposed as ``float -> float`` helpers so they're trivially
unit-testable in isolation, and so a future Raman/multiband extension
can swap the constants without touching the backend.
"""

from __future__ import annotations

# Speed of light in vacuum [m/s].
_C_M_PER_S = 2.99792458e8
# SSMF group index (G.652). Used for propagation delay.
_DEFAULT_N_EFF = 1.468
# SSMF dispersion at 1550 nm [ps/(nm·km)] — Kato et al., Opt. Lett. 2000.
_DEFAULT_D_PS_NM_KM = 16.7
# SSMF PMD coefficient [ps/√km] — typical buried fiber (matches the
# transient layer's default in PolarizationConfig).
_DEFAULT_PMD_COEF_PS_SQRT_KM = 0.04


def chromatic_dispersion_ps_per_nm(
    length_km: float,
    d_ps_nm_km: float = _DEFAULT_D_PS_NM_KM,
) -> float:
    """Total accumulated CD over a path [ps/nm]."""
    return d_ps_nm_km * length_km


def pmd_ps(
    length_km: float,
    pmd_coef_ps_sqrt_km: float = _DEFAULT_PMD_COEF_PS_SQRT_KM,
) -> float:
    """Mean differential group delay over a path [ps].

    Gordon-Kogelnik random-walk: <Δτ> ∝ √L. Returns the magnitude of
    the mean DGD, not a per-realisation sample.
    """
    if length_km < 0:
        raise ValueError("length_km must be non-negative")
    return pmd_coef_ps_sqrt_km * (length_km ** 0.5)


def latency_ms(
    length_km: float,
    n_eff: float = _DEFAULT_N_EFF,
) -> float:
    """One-way propagation delay over a path [ms]."""
    length_m = length_km * 1e3
    return (length_m * n_eff / _C_M_PER_S) * 1e3
