"""Environmental transient model: thermal effects on fiber CD and loss.

Physical basis
--------------
CD drift
    Chromatic dispersion falls as the fiber warms.  Kato, Koyano & Nishimura
    (Opt. Lett. 25(16):1156, 2000) measure dD/dT from -0.0005 ps/(nm·km·°C)
    (dispersion-flattened fiber) to -0.0038 (large-core NZ-DSF) and find that
    it depends strongly on the dispersion slope; they give no G.652 value.
    The default, -0.0015 ps/(nm·km·°C), lies inside their range (PHYSICS.md
    § Known limitations).  A 5 °C diurnal swing over a 100 km link moves CD
    by ~0.75 ps/nm: negligible for coherent systems with DSP, but included
    as a realistic monitor metric.

Loss drift
    Fiber attenuation changes with temperature at dα/dT ≈ 2×10⁻⁴ dB/(km·°C)
    [CITATION NEEDED: no published source is recorded for this value on
    deployed G.652 fiber].  A 5 °C swing changes the loss of a 100 km span
    by δ ≈ 0.1 dB.

    What that does to the lightpath depends on how the amplifiers are
    controlled.  TwinLight's amplifiers hold their output power: GNPy's
    ``designed_network()`` designs them in power mode (``Span.power_mode``
    in the equipment library), and the EGN backend uses the same design.
    Each amplifier then makes up its span's extra loss, so the launch
    powers, and with them the NLI, do not move, while each amplifier's ASE
    relative to the signal rises by its span's δ.  With the incoherent
    accumulation of the noise terms (Carena et al., JLT 30(10):1524, 2012;
    the lightpath GSNR of Curri, JLT 40(11):3498, 2022),

        1/OSNR' = 10^(δ/10) / OSNR
        1/GSNR' = 1/GSNR + (10^(δ/10) − 1) / OSNR

    for a δ common to every span.  The OSNR falls by δ, the change of *one*
    span, however many spans the route has; the GSNR falls by less, by the
    share of ASE in its noise.  The route's spans differ in length, so δ is
    taken at their mean length (see PHYSICS.md § Known limitations).

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


def span_loss_change_db(
    t: float,
    total_fiber_km: float,
    n_spans: int,
    cfg: EnvironmentalConfig,
) -> float:
    """Loss change [dB] of one span of the route's mean length at time t.

    Under output-power control this, not the route's summed loss change, is
    what the OSNR loses (module docstring).

    Args:
        t: Wall-clock time [s].
        total_fiber_km: Total fiber length of the route [km].
        n_spans: Number of amplified spans (fibers) on the route.
        cfg: Environmental configuration.

    Returns:
        δ [dB]: positive (more loss) while the fiber is warmer than its mean
        temperature.  Zero if the model is disabled or the route has no span.
    """
    if not cfg.enabled or n_spans <= 0:
        return 0.0
    mean_span_km = total_fiber_km / n_spans
    return cfg.alpha_temp_coeff_db_km_c * mean_span_km * _temp_delta(t, cfg)


def apply_span_loss_change(
    gsnr_db: float, osnr_db: float, delta_db: float,
) -> tuple[float, float]:
    """GSNR and OSNR [dB] after every span's loss changes by ``delta_db``.

    Output-power-controlled amplifiers make up the extra loss: each one's ASE
    relative to the signal rises by ``delta_db`` and the NLI is unchanged, so,
    with the noise terms adding incoherently (Carena et al. 2012),

        1/OSNR' = 10^(δ/10) / OSNR
        1/GSNR' = 1/GSNR + (10^(δ/10) − 1) / OSNR

    Args:
        gsnr_db: GSNR (ASE + NLI + the other noise terms) [dB].
        osnr_db: OSNR (ASE only) on the same reference bandwidth [dB].
        delta_db: Loss change of each span [dB].

    Returns:
        ``(gsnr_db, osnr_db)`` after the change.
    """
    if delta_db == 0.0:
        return gsnr_db, osnr_db
    growth = 10.0 ** (delta_db / 10.0)
    inverse_osnr = 10.0 ** (-osnr_db / 10.0)
    inverse_gsnr = 10.0 ** (-gsnr_db / 10.0) + (growth - 1.0) * inverse_osnr
    return -10.0 * math.log10(inverse_gsnr), osnr_db - delta_db
