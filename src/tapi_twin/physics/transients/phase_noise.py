"""Phase noise transient model: laser linewidth-induced SNR penalty.

Physical basis
--------------
Finite laser linewidth causes carrier phase noise.  In a coherent DP-QAM
system the receiver DSP partially compensates phase noise but a residual
penalty remains.

The dominant effect for long-haul links is Equalization-Enhanced Phase
Noise (EEPN), where the interaction between LO phase noise and
accumulated chromatic dispersion produces an SNR penalty that grows with
link length (Shieh-Ho, Opt. Express 2008, Eq. 33-41):

    α = π·c / (2·f₀²) · |D_t| · B · Δν_LO

    penalty_dB = 10·log10((GSNR_lin·α + 1) / (1 - α))

where D_t is accumulated dispersion [s/m], B is baud rate [Hz],
Δν_LO is the local oscillator linewidth [Hz], f₀ is the carrier
frequency [Hz], and GSNR_lin is the pre-EEPN GSNR in linear units.

Key differences from the previous CPE-residual formula:
  - Dispersion-dependent (dominant factor: 1000 km ≈ 100× more than 10 km)
  - Only LO linewidth matters (Tx phase noise cancels through fiber+equalizer)
  - SNR-dependent (penalty grows with pre-EEPN GSNR)

Time variation of linewidth (period and amplitude) is configurable.
"""

from __future__ import annotations

import math

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tapi_twin.config import PhaseNoiseConfig

# Physical constants
_C_LIGHT = 299_792_458.0       # speed of light [m/s]
_F0 = 193.1e12                 # ITU-T centre frequency [Hz]


def delta_gsnr_db(
    t: float,
    service_uuid: str,
    baud_rate_hz: float,
    cfg: "PhaseNoiseConfig",
    accumulated_cd_ps_nm: float = 0.0,
    gsnr_db: float = 30.0,
    _phase_fn=None,
) -> float:
    """Compute GSNR penalty [dB] from time-varying laser linewidths (EEPN).

    Uses the Shieh-Ho 2008 EEPN model where penalty depends on accumulated
    chromatic dispersion and LO linewidth.

    Args:
        t: Wall-clock time [s].
        service_uuid: Service UUID (used as per-service seed for phase).
        baud_rate_hz: Symbol rate [Hz].
        cfg: Phase noise config (linewidths, variation period/amplitude).
        accumulated_cd_ps_nm: Accumulated chromatic dispersion [ps/nm] from
            GNPy baseline.  Determines EEPN severity (longer links → larger).
        gsnr_db: Pre-EEPN baseline GSNR [dB].  EEPN penalty grows with
            pre-penalty SNR.
        _phase_fn: Optional callable(uid, metric) for testing.

    Returns:
        Delta GSNR [dB] (negative — phase noise always degrades GSNR).
    """
    from tapi_twin.physics.transients.cascade import hash_phase
    ph = _phase_fn or hash_phase

    phase = ph(service_uuid, "phase_noise")
    lw_variation = cfg.linewidth_variation_amplitude * math.sin(
        2 * math.pi * t / cfg.linewidth_variation_period_s + phase
    )
    # Effective LO linewidth with time variation
    eff_lo_lw = cfg.lo_linewidth_hz * (1.0 + lw_variation)

    # Shieh-Ho EEPN parameter α
    # D_t in SI: ps/nm → s/m: multiply by 1e-3 (ps→s=1e-12, nm→m=1e-9, ratio=1e-3)
    d_t_si = abs(accumulated_cd_ps_nm) * 1e-3
    alpha = math.pi * _C_LIGHT / (2.0 * _F0**2) * d_t_si * baud_rate_hz * eff_lo_lw

    # Clamp alpha to avoid singularity at α→1
    alpha = min(alpha, 0.99)

    if alpha < 1e-12:
        # Negligible EEPN (very short link or zero linewidth)
        return 0.0

    # GSNR in linear units (pre-EEPN)
    gsnr_lin = 10.0 ** (gsnr_db / 10.0)

    # Shieh-Ho penalty: accounts for interaction between EEPN noise and signal SNR
    penalty_db = 10.0 * math.log10(
        (gsnr_lin * alpha + 1.0) / (1.0 - alpha)
    )
    return -penalty_db
