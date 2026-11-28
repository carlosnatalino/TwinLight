"""EDFA gain-reservoir transient model (Bononi exponential step).

Physical basis
--------------
The Giles-Desurvire 1991 (JLT vol. 9) / Sun-Saleh-Zyskind 1997
(Electron. Lett. vol. 33) reservoir model describes EDFA gain transients
during channel add/drop events.  The single-ODE model (Bononi-Rusch, JLT 1998, Eq. 5):

    dr/dt = -r(t)/τ + Σ Q_j^in(t) [1 - exp(B_j·r(t) - A_j)]

has an exponential step response (Bononi-Rusch Eq. 19):

    r(t) = r_ss_new + (r_ss_old - r_ss_new) · exp(-(t - t_event) / τ_e)

where τ_e is the effective time constant (Bononi-Rusch Eq. 29), which
depends on input power and is asymmetric for add (~1-10 µs) vs.
drop (~100-500 µs).

Magnitude (Sun et al., JSTQE 1997, § Single EDFA).  A saturated amplifier
holds its total output power, so the swing in surviving-channel power is set
by the ratio of the channel counts before and after the event:

    Δ = 10·log10(N_before / N_after)   [dB]

negative for an add (the channels share the output with one more), positive
for a drop.  One channel added to 87 is −0.05 dB; one added to one is −3 dB.
With no channel before (or none after) there is no surviving channel, and
nothing to perturb.

Cascade (same paper, § Numerical Simulation of Fast Power Transients: the
overshoot along a chain "asymptotically approaches, but never exceed[s] a
certain value").  In a chain of amplifiers carrying the same channels the
first one sets the surviving channel's share of the output power and the
others conserve it, so the swing does not grow with the number of
amplifiers.  A further swing appears only where the channel meets another
amplifier whose load changed differently.  The amplifiers of one
ROADM-to-ROADM link carry the same lightpaths, so a route's excursion counts
each run of consecutive amplifiers with the same event record once, and
adds the runs.

Rate (Sun et al., Electron. Lett. 33(4):313, 1997: "the rate at which
surviving channel power increases scales linearly with the number of
EDFAs").  A channel that crosses M amplifiers touched by the event sees the
transient M times faster than one crossing a single amplifier, so the
route's excursion relaxes with τ_e / M.

Without AGC, and with packet-like loading, cascades show peak swings of up to
~28 dB p-p (Tancevski-Bononi-Rusch, IEEE JLT 1999); the AGC assumption below
is what keeps this model's excursions transient.

Implementation
--------------
This module tracks, per EDFA, the number of lit lightpaths through it and
the last add/drop event: its time, the excursion it left (the swing above,
plus whatever an earlier transient still had in flight) and τ_e.  A route's
excursion is evaluated at query time from the records of its amplifiers.

**Reference load.**  The channel counts are the admitted lightpaths through
each amplifier, under both backends.  The GNPy backend's QoT baseline
assumes the full SI comb on every line; the reservoir does not, so under
GNPy an excursion is what an amplifier carrying only the lit lightpaths
would see, not one whose empty slots are filled with loading channels
(PHYSICS.md § Known limitations).

**The steady state is zero deviation (AGC).**  Between add/drop events an
AGC-controlled, gain-flattened EDFA delivers its designed per-channel gain
regardless of how many channels it carries — and that designed operating
point is exactly what the QoT backend's baseline already represents
(``designed_network()`` sets each amplifier's operating point before any
propagation).  What this model contributes is therefore the *excursion*
around that baseline, and never a standing offset.  The peak is the swing of
the saturated amplifier above, the upper bound for an AGC loop slower than
the reservoir; the loop's own response is not modelled, and the excursion
relaxes to the set point with τ_e / M.  Modelling the steady state as a
load-dependent penalty would double-count the loading the baseline has
already priced in.

Default parameters:
  - tau_ms = 10.0 ms: erbium metastable lifetime (Sun-Saleh-Zyskind 1997,
    Electron. Lett. vol. 33)
  - tau_add_factor = 0.001: τ_e/τ ratio for channel-add events
    (Bononi-Rusch 1998: ~1-10 µs for τ = 10 ms)
  - tau_drop_factor = 0.01: τ_e/τ ratio for channel-drop events
    (Bononi-Rusch 1998: ~100-500 µs for τ = 10 ms)
"""

from __future__ import annotations

import itertools
import math
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twinlight.config import EdfaReservoirConfig


@dataclass(frozen=True)
class EdfaEvent:
    """Record of the last channel-count change on an EDFA.

    The reservoir relaxes back to zero deviation (the designed operating
    point), so only the excursion at the event and its decay constant
    need to be stored — see the module docstring.  The amplifiers of one
    link, notified by the same event with the same load, hold equal records.
    """

    event_time: float          # wall-clock time [s] of the event
    excursion_db: float        # GSNR deviation [dB] immediately after the step
    tau_eff_s: float           # effective time constant [s] of one amplifier


def load_step_swing_db(n_before: int, n_after: int) -> float:
    """Surviving-channel power swing [dB] of a saturated EDFA whose load goes
    from ``n_before`` to ``n_after`` channels.

    Sun, Zyskind & Srivastava, IEEE JSTQE 3(4):991 (1997), § Single EDFA:
    "the total swing in surviving channel power is proportional to ratio of
    the number of channels initially to the number of surviving channels",
    which is the saturated amplifier holding its total output power.  Zero
    when either count is zero: no channel survives the step.
    """
    if n_before <= 0 or n_after <= 0:
        return 0.0
    return 10.0 * math.log10(n_before / n_after)


class EdfaStateTracker:
    """Per-EDFA stateful reservoir model.

    Tracks channel count on each EDFA and records events (add/drop) with
    their timestamps to compute the exponential step response.
    """

    def __init__(self) -> None:
        # edfa_uid -> current channel count (lit lightpaths through it)
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
            # excursion is still in flight.  It is this amplifier's own
            # (M = 1) view of the transient.
            current_dev = self._get_deviation(uid, t)

            # Sun et al. JSTQE 1997: the swing follows the channel-count
            # ratio — negative for an add (gain compresses), positive for a
            # drop (gain overshoots).  It then relaxes back to the designed
            # operating point: the steady state is zero deviation.
            excursion = current_dev + load_step_swing_db(old_count, new_count)

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
        """GSNR deviation of one EDFA at time t, as a channel crossing it
        alone would see it (no cascade speed-up)."""
        ev = self._last_event.get(uid)
        if ev is None:
            return 0.0
        return _decayed(ev, t, 1)

    def get_total_delta_gsnr_db(
        self,
        edfa_uids: list[str],
        t: float | None = None,
    ) -> float:
        """Compute total GSNR perturbation from all EDFAs on a path.

        Cascade (module docstring): a run of consecutive amplifiers with the
        same event record (one link's, notified by one event with one load)
        counts once, since a saturated chain conserves the surviving
        channel's share of its output (Sun et al., JSTQE 1997); different
        runs add.  Each event's excursion relaxes with τ_e / M, M being the
        number of the path's amplifiers that event touched (Sun et al.,
        Electron. Lett. 1997: the rate scales linearly with the number of
        EDFAs).

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
        records = [self._last_event.get(uid) for uid in edfa_uids]
        touched = Counter(ev.event_time for ev in records if ev is not None)
        total = 0.0
        for ev, _run in itertools.groupby(records):
            if ev is not None:
                total += _decayed(ev, t, touched[ev.event_time])
        return total

    def clear_edfa(self, uid: str) -> None:
        """Remove state for an EDFA (e.g. when no services traverse it)."""
        self._channel_count.pop(uid, None)
        self._last_event.pop(uid, None)


def _decayed(ev: EdfaEvent, t: float, speed_up: int) -> float:
    """The excursion of ``ev`` at time t, relaxing with τ_e / ``speed_up``."""
    elapsed = t - ev.event_time
    if elapsed < 0:
        # Before the recorded event the amplifier is at its designed
        # operating point as far as this tracker can tell — only the
        # most recent event is retained.
        return 0.0
    # Bononi-Rusch Eq. 19: exponential step response, relaxing to the
    # zero-deviation steady state (see the module docstring).
    return ev.excursion_db * math.exp(-elapsed * max(speed_up, 1) / ev.tau_eff_s)


def delta_gsnr_db(
    t: float,
    edfa_uids: list[str],
    edfa_tracker: EdfaStateTracker,
) -> float:
    """GSNR perturbation [dB] from the EDFA gain transients at time t.

    The tracker holds the add/drop events (Bononi exponential step
    response); there is no stateless form, so every reader of a lightpath
    applies the same events and reports the same value.

    Args:
        t: Wall-clock time [s].
        edfa_uids: Ordered EDFA UIDs along the path.
        edfa_tracker: The context's tracker of add/drop events.

    Returns:
        Delta GSNR [dB].
    """
    return edfa_tracker.get_total_delta_gsnr_db(edfa_uids, t)
