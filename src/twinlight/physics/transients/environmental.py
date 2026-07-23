"""Environmental transient model: thermal effects on fiber CD and loss.

Physical basis
--------------
CD drift
    Chromatic dispersion of G.652 SSMF varies with temperature at roughly
    dD/dT ≈ 0.002 ps/(nm·km·°C) (Kato et al., Opt. Lett. 2000;
    range -0.0015 to -0.0038; ITU-T G.652 data sheet).
    A 5 °C diurnal swing over a 100 km link produces ~0.1 ps/nm variation,
    which is negligible for coherent systems with DSP but is included as a
    realistic monitor metric.

Loss drift
    Fiber attenuation coefficient changes with temperature at approximately
    dα/dT ≈ 2×10⁻⁴ dB/(km·°C) [measured on deployed G.652 fibers].
    The same 5 °C swing over 100 km gives ~0.1 dB loss variation, which
    translates to a GSNR impact of the same magnitude.

Timezone offset
    The diurnal cycle phase is shifted by ``timezone_offset × 3600 s`` so
    that the temperature maximum tracks local noon regardless of the server
    clock's UTC setting.
"""

from __future__ import annotations

import math

from twinlight.config import EnvironmentalConfig


def _temp_delta(t: float, cfg: EnvironmentalConfig) -> float:
    """Instantaneous temperature excursion [°C] at wall time t."""
    t_local = t - cfg.timezone_offset * 3600.0
    return cfg.temp_variation_c * math.sin(
        2 * math.pi * t_local / cfg.temp_cycle_period_s
    )


def delta_cd_ps_nm(
    t: float,
    total_fiber_km: float,
    cfg: EnvironmentalConfig,
) -> float:
    """CD variation [ps/nm] due to diurnal temperature cycle.

    Args:
        t: Wall-clock time [s].
        total_fiber_km: Total fiber length [km].
        cfg: Environmental configuration (must have ``enabled=True``).

    Returns:
        Delta CD [ps/nm].  Zero if environmental model is disabled.
    """
    if not cfg.enabled:
        return 0.0
    return cfg.cd_temp_coeff_ps_nm_km_c * total_fiber_km * _temp_delta(t, cfg)


def delta_gsnr_from_temp_db(
    t: float,
    total_fiber_km: float,
    cfg: EnvironmentalConfig,
) -> float:
    """GSNR variation [dB] due to fiber loss change with temperature.

    More loss at elevated temperature → lower GSNR.

    Args:
        t: Wall-clock time [s].
        total_fiber_km: Total fiber length [km].
        cfg: Environmental configuration (must have ``enabled=True``).

    Returns:
        Delta GSNR [dB] (can be positive or negative with the temperature cycle).
    """
    if not cfg.enabled:
        return 0.0
    delta_loss_db = cfg.alpha_temp_coeff_db_km_c * total_fiber_km * _temp_delta(t, cfg)
    return -delta_loss_db  # more loss → lower GSNR
