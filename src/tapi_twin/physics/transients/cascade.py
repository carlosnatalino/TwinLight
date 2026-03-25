"""Compose all transient perturbation models onto a GNPy baseline.

This module is the single entry point for the OPM endpoint.  It imports
from all four transient modules and applies them in sequence to produce
a final measurements dict suitable for the API response.

Models and primary references:
  1. EDFA reservoir: Bononi-Rusch JLT 1998 Eq. 5/19/29; Sun 1997
  2. PMD drift: Gordon-Kogelnik PNAS 2000 (Maxwell DGD)
  3. PDL penalty: Mecozzi-Shtaif PTL 2002 (accumulation);
     Lichtman 1995 / Bruyère-Audouin PTL 1994 (penalty formula)
  4. Phase noise (EEPN): Shieh-Ho Opt. Express 2008 Eq. 33-41
  5. Environmental: Kato et al. Opt. Lett. 2000 (dD/dT)

It also exports the ``hash_phase`` helper used by every transient module
to generate deterministic, per-(uid, metric) phase offsets.
"""

from __future__ import annotations

import hashlib
import math

from tapi_twin.config import TransientsConfig
from tapi_twin.physics.ber_conversion import ber_to_q_db, gsnr_to_ber
from tapi_twin.physics.gnpy_adapter import OpmBaseline
from tapi_twin.physics.modulation import get_params


def hash_phase(uid: str, metric: str) -> float:
    """Deterministic phase offset in [0, 2π) from uid + metric string.

    Uses the first 5 decimal digits of an MD5 digest to produce a stable,
    non-zero phase offset that is unique per (uid, metric) pair.

    Args:
        uid: Element or service UUID string.
        metric: Metric name (e.g. "edfa_gain", "pmd").

    Returns:
        Phase in radians, in [0, 2π).
    """
    digest = hashlib.md5(
        f"{uid}:{metric}".encode(), usedforsecurity=False
    ).hexdigest()
    return (int(digest, 16) % 100_000) / 100_000.0 * 2 * math.pi


def apply_all_transients(
    baseline: OpmBaseline,
    service_uuid: str,
    modulation_format: str,
    t: float,
    cfg: TransientsConfig,
    edfa_tracker=None,
) -> dict[str, float]:
    """Apply all enabled transient perturbations to a GNPy baseline.

    Each model contributes an additive delta (in dB or ps) on top of the
    static GNPy result.  After applying physical perturbations, BER and
    Q-factor are recomputed from the perturbed GSNR using the Gaussian
    approximation for the given modulation format.

    Args:
        baseline: Static QoT metrics from GNPy propagation.
        service_uuid: UUID of the connectivity service (seeds phase noise).
        modulation_format: Format string (e.g. "DP-QPSK").
        t: Wall-clock time [s].
        cfg: Transient model configuration.
        edfa_tracker: Optional EdfaStateTracker for Bononi exponential
            step model.  When None, falls back to sinusoidal approximation.

    Returns:
        Measurements dict with keys:
            gsnr-db, osnr-db, pre-fec-ber, q-factor-db,
            chromatic-dispersion-ps-per-nm, pmd-ps
    """
    from tapi_twin.physics.transients import (
        edfa_reservoir, polarization, phase_noise, environmental,
    )

    params = get_params(modulation_format)

    gsnr = baseline.gsnr_db
    osnr = baseline.osnr_ase_db
    cd = baseline.cd_ps_nm
    pmd = baseline.pmd_ps

    # 1. EDFA gain reservoir (Bononi-Rusch JLT 1998 / Sun 1997)
    if cfg.edfa_reservoir.enabled and baseline.edfa_uids:
        d = edfa_reservoir.delta_gsnr_db(
            t, baseline.edfa_uids, cfg.edfa_reservoir,
            edfa_tracker=edfa_tracker,
        )
        gsnr += d
        osnr += d

    # 2. Polarization: PMD drift (Gordon-Kogelnik 2000) +
    #    PDL penalty (Lichtman 1995 / Mecozzi-Shtaif 2002)
    #    PDL affects both GSNR and OSNR: it causes signal power
    #    fluctuation that degrades both ASE-limited and NLI-limited SNR.
    if cfg.polarization.enabled:
        all_uids = baseline.fiber_uids + baseline.edfa_uids
        pmd += polarization.delta_pmd_ps(
            t, baseline.fiber_uids, baseline.pmd_ps, cfg.polarization
        )
        pdl_penalty = polarization.delta_gsnr_from_pdl_db(
            t, all_uids, cfg.polarization,
        )
        gsnr += pdl_penalty
        osnr += pdl_penalty

    # 3. Phase noise: EEPN (Shieh-Ho, Opt. Express 2008)
    #    EEPN is a non-ASE impairment (DSP interaction with LO phase
    #    noise and accumulated CD) — affects only GSNR, not OSNR.
    if cfg.phase_noise.enabled:
        gsnr += phase_noise.delta_gsnr_db(
            t, service_uuid, params.baud_rate_hz, cfg.phase_noise,
            accumulated_cd_ps_nm=baseline.cd_ps_nm,
            gsnr_db=gsnr,
        )

    # 4. Environmental: thermal CD drift + loss variation
    #    Fiber loss variation affects both GSNR and OSNR: higher loss
    #    reduces signal power reaching EDFAs, degrading ASE-limited SNR.
    if cfg.environmental.enabled:
        cd += environmental.delta_cd_ps_nm(
            t, baseline.total_fiber_km, cfg.environmental,
        )
        env_loss_penalty = environmental.delta_gsnr_from_temp_db(
            t, baseline.total_fiber_km, cfg.environmental,
        )
        gsnr += env_loss_penalty
        osnr += env_loss_penalty

    # Recompute BER and Q from perturbed GSNR
    ber = gsnr_to_ber(gsnr, modulation_format)

    return {
        "gsnr-db": gsnr,
        "osnr-db": osnr,
        "pre-fec-ber": ber,
        "q-factor-db": ber_to_q_db(ber),
        "chromatic-dispersion-ps-per-nm": cd,
        "pmd-ps": pmd,
    }
