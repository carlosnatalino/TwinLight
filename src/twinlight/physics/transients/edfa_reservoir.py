"""EDFA gain-reservoir transient model (Bononi exponential step).

Physical basis
--------------
The Giles-Desurvire 1991 (JLT vol. 9) / Sun-Saleh-Zyskind 1997
(Electron. Lett. vol. 33) reservoir model describes EDFA gain transients
during channel add/drop events.  The single-ODE model (Bononi-Rusch,
JLT 1998, Eq. 5):

    dr/dt = -r(t)/τ + Σ Q_j^in(t) [1 - exp(B_j·r(t) - A_j)]

has an exponential step response (Bononi-Rusch Eq. 19):

    r(t) = r_ss_new + (r_ss_old - r_ss_new) · exp(-(t - t_event) / τ_e)

where τ_e is the effective time constant (Bononi-Rusch Eq. 29), which
depends on input power and is asymmetric for add (~1-10 µs) vs.
drop (~100-500 µs).

Cascade behaviour (Sun 1997): transient rate scales linearly with
amplifier count: 1/T_N = N · 1/T_1.  Peak excursions can reach
~28 dB p-p without AGC (Tancevski-Bononi-Rusch, IEEE JLT 1999).

Implementation
--------------
This module maintains per-EDFA state tracking the current channel count,
the last event time, and the gain excursion that event produced.  On each
query, the exponential decay is evaluated at the current wall-clock time.

**The steady state is zero deviation.**  Between add/drop events an
AGC-controlled, gain-flattened EDFA delivers its designed per-channel gain
regardless of how many channels it carries — and that designed operating
point is exactly what the QoT backend's baseline already represents
(``designed_network()`` sets each amplifier's operating point before any
propagation).  What this model contributes is therefore the *excursion*
around that baseline, which relaxes back to it with τ_e, and never a
standing offset.  Modelling the steady state itself as a load-dependent
penalty would double-count the loading the baseline has already priced in.

Default parameters:
  - tau_ms = 10.0 ms: erbium metastable lifetime (Sun-Saleh-Zyskind 1997)
  - gain_per_channel_db = 0.3 dB: peak gain excursion [dB] per channel of
    load step (conservative for a single EDFA; cascade accumulates
    linearly in dB, Sun 1997)
  - tau_add_factor = 0.001: τ_e/τ ratio for channel-add events
    (Bononi-Rusch 1998: ~1-10 µs for τ = 10 ms)
  - tau_drop_factor = 0.01: τ_e/τ ratio for channel-drop events
    (Bononi-Rusch 1998: ~100-500 µs for τ = 10 ms)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twinlight.config import EdfaReservoirConfig


@dataclass
class EdfaEvent:
    """Record of the last channel-count change on an EDFA.

    The reservoir relaxes back to zero deviation (the designed operating
    point), so only the excursion at the event and its decay constant
    need to be stored — see the module docstring.
    """

    event_time: float          # wall-clock time [s] of the event
    excursion_db: float        # GSNR deviation [dB] immediately after the step
    tau_eff_s: float           # effective time constant [s]


class EdfaStateTracker:
    """Per-EDFA stateful reservoir model.

    Tracks channel count on each EDFA and records events (add/drop) with
    their timestamps to compute the exponential step response.
    """

    def __init__(self) -> None:
        # edfa_uid -> current channel count
        self._channel_count: dict[str, int] = {}
        # edfa_uid -> last event record
        self._last_event: dict[str, EdfaEvent] = {}

    def notify_channel_change(
        self,
        edfa_uids: list[str],
        delta_channels: int,
        cfg: EdfaReservoirConfig,
        t: float | None = None,
    ) -> None:
        """Record a channel add/drop event for a set of EDFAs.

        Args:
            edfa_uids: EDFAs affected by this event.
            delta_channels: +N for add, -N for drop.
            cfg: EDFA reservoir configuration.
            t: Event wall-clock time [s]; defaults to now.
        """
        if t is None:
            t = time.time()

        for uid in edfa_uids:
            old_count = self._channel_count.get(uid, 0)
            new_count = max(0, old_count + delta_channels)
            self._channel_count[uid] = new_count

            # The clamp above means a drop below zero channels is not a
            # load step at all, so nothing is perturbed.
            effective_delta = new_count - old_count
            if effective_delta == 0:
                continue

            # Current deviation at event time (evaluate ongoing transient)
            # so back-to-back events compose instead of discarding whatever
            # excursion is still in flight.
            current_dev = self._get_deviation(uid, t)

            # Bononi-Rusch: the gain excursion is proportional to the load
            # *change*. Adding channels depletes the reservoir, so gain
            # drops and surviving channels lose GSNR (negative excursion);
            # dropping channels lets gain overshoot (positive excursion).
            # It then relaxes back to the designed operating point — the
            # steady state is zero deviation, not a standing penalty.
            excursion = current_dev - cfg.gain_per_channel_db * effective_delta

            # Effective time constant depends on direction
            # (Bononi-Rusch 1998, Eq. 29: τ_e is asymmetric)
            tau_ms = cfg.tau_ms
            if effective_delta > 0:
                # Channel add: fast response (~1-10 µs)
                tau_eff = (tau_ms / 1000.0) * cfg.tau_add_factor
            else:
                # Channel drop: slower response (~100-500 µs)
                tau_eff = (tau_ms / 1000.0) * cfg.tau_drop_factor

            self._last_event[uid] = EdfaEvent(
                event_time=t,
                excursion_db=excursion,
                tau_eff_s=max(tau_eff, 1e-9),
            )

    def _get_deviation(self, uid: str, t: float) -> float:
        """Evaluate current GSNR deviation for an EDFA at time t."""
        ev = self._last_event.get(uid)
        if ev is None:
            return 0.0
        elapsed = t - ev.event_time
        if elapsed < 0:
            # Before the recorded event the amplifier is at its designed
            # operating point as far as this tracker can tell — only the
            # most recent event is retained.
            return 0.0
        # Bononi-Rusch Eq. 19: exponential step response, relaxing to the
        # zero-deviation steady state (see the module docstring).
        return ev.excursion_db * math.exp(-elapsed / ev.tau_eff_s)

    def get_total_delta_gsnr_db(
        self,
        edfa_uids: list[str],
        t: float | None = None,
    ) -> float:
        """Compute total GSNR perturbation from all EDFAs on a path.

        Cascade accumulation: GSNR deviations add linearly in dB
        (Sun 1997: 1/T_N = N · 1/T_1).

        Args:
            edfa_uids: Ordered EDFA UIDs along the path.
            t: Query wall-clock time [s]; defaults to now.

        Returns:
            Total GSNR deviation [dB] — negative while an add transient is
            in flight, positive during a drop, and zero once the cascade
            has relaxed to its designed operating point.  Because τ_e is
            microseconds, that is the value any wall-clock-rate poll sees.
        """
        if t is None:
            t = time.time()
        return sum(self._get_deviation(uid, t) for uid in edfa_uids)

    def clear_edfa(self, uid: str) -> None:
        """Remove state for an EDFA (e.g. when no services traverse it)."""
        self._channel_count.pop(uid, None)
        self._last_event.pop(uid, None)


def delta_gsnr_db(
    t: float,
    edfa_uids: list[str],
    cfg: EdfaReservoirConfig,
    edfa_tracker: EdfaStateTracker | None = None,
    _phase_fn=None,
) -> float:
    """Compute GSNR perturbation [dB] from EDFA gain dynamics at time t.

    If an EdfaStateTracker is provided, uses the Bononi exponential step
    response model (stateful, event-driven).  Otherwise falls back to the
    sinusoidal slow-drift approximation (stateless, for backwards compat).

    Args:
        t: Wall-clock time [s].
        edfa_uids: Ordered EDFA UIDs along the path.
        cfg: EDFA reservoir config.
        edfa_tracker: Optional stateful tracker with event history.
        _phase_fn: Optional callable(uid, metric) → float for testing.

    Returns:
        Delta GSNR [dB].
    """
    if edfa_tracker is not None:
        return edfa_tracker.get_total_delta_gsnr_db(edfa_uids, t)

    # Fallback: stateless sinusoidal approximation (original model)
    # Retained for backward compatibility when no event tracking is
    # available (e.g. snapshot restore without event history).
    from twinlight.physics.transients.cascade import hash_phase
    ph = _phase_fn or hash_phase

    T_drift = (cfg.tau_ms / 1000.0) * cfg.drift_period_multiplier
    amp_db = cfg.gain_drift_amp_db
    total = 0.0
    for uid in edfa_uids:
        phase = ph(uid, "edfa_gain")
        excursion = amp_db * math.sin(
            2 * math.pi * t / T_drift + phase
        )
        total += excursion * 0.05 - abs(excursion) * 0.1
    return total
