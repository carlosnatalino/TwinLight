"""Pre-FEC BER → post-FEC BER, by two independent models.

Neither is universal, so both are reported, each as its own KPI:

1. Threshold model — the network-planning convention: a code corrects any
   pre-FEC BER up to its threshold down to the BER it is specified at, and
   fails beyond it. Valid for any code, soft- or hard-decision, but a step
   rather than a curve: it knows the cliff, not the waterfall.

2. Bounded-distance hard-decision decoding of a t-error-correcting block code
   over GF(2^m) with independent bit errors. The decoded symbol error rate is
   Sklar, *Digital Communications*, 2nd ed., Ch. 8 (Reed–Solomon performance):

       P_E = (1/n) Σ_{j=t+1..n} j · C(n,j) · p_s^j · (1 − p_s)^(n−j),
       p_s = 1 − (1 − p)^m

   Sklar converts it to bits as if every erroneous output symbol were random,
   P_B = 2^(m−1)/(2^m − 1) · P_E. Here the erroneous symbols are instead taken
   to keep the channel's bit errors — a failed bounded-distance decoder
   passes the received word through — so each carries m·p/p_s wrong bits:

       P_B = P_E · p / p_s

   This reproduces the published RS(255,239) net coding gain, 6.2 dB at
   1e-15 (Smith et al., JLT 30(1), 2012), where Sklar's factor gives 6.1 dB;
   and it tends to p, not to ~m·p/2, once the decoder always fails. The two
   agree for a binary code (m = 1). A closed-form curve, but only for
   classical hard-decision codes such as the G.709 RS(255,239); it ignores
   miscorrection and burst errors, and does not describe concatenated or
   soft-decision codes (CFEC, oFEC).

``fec_measurements`` also attaches the GMI and NGMI (physics/gmi.py), the
soft-decision counterpart; they are not turned into a post-FEC BER, because
that needs a per-code NGMI threshold or waterfall that is not modelled.
"""

from __future__ import annotations

import math

from scipy.stats import binom

from twinlight.config import FecConfig, FecThresholdConfig, HardDecisionFecConfig
from twinlight.physics.gmi import gsnr_to_gmi
from twinlight.physics.modulation import ModulationFormat, get_params


def post_fec_ber_threshold(pre_fec_ber: float, cfg: FecThresholdConfig) -> float:
    """Post-FEC BER under the threshold model.

    At or below the threshold the code delivers its specified output BER
    (or the input, if that is already lower — a code never adds errors
    there). Above it the decoder fails and the errors pass through
    uncorrected, so the pre-FEC BER is returned: the model knows where the
    cliff is, not the shape of the waterfall.
    """
    if pre_fec_ber <= cfg.pre_fec_ber_threshold:
        return min(pre_fec_ber, cfg.post_fec_ber_floor)
    return pre_fec_ber


def post_fec_ber_hard_decision(
    pre_fec_ber: float, cfg: HardDecisionFecConfig
) -> float:
    """Post-FEC BER of bounded-distance hard-decision decoding.

    Sklar's decoded symbol error rate (Ch. 8), converted to bits at the
    channel's bits-per-symbol-error ratio — see the module docstring. The
    sum is evaluated through the identity j·C(n,j) = n·C(n−1,j−1), which
    turns it into a binomial tail:

        P_E = p_s · P[Binomial(n − 1, p_s) ≥ t],   so   P_B = p · P[…]

    ``scipy.stats.binom.sf`` computes that tail through the regularized
    incomplete beta function, accurate down to BERs where the direct sum
    underflows term by term.
    """
    if not 0.0 <= pre_fec_ber <= 1.0:
        raise ValueError(f"pre-FEC BER must be in [0, 1], got {pre_fec_ber}")
    if pre_fec_ber == 0.0:
        return 0.0
    # 1 − (1 − p)^m without cancellation at small p.
    m = cfg.symbol_bits
    p_symbol = -math.expm1(m * math.log1p(-pre_fec_ber)) if pre_fec_ber < 1 else 1.0
    return pre_fec_ber * float(binom.sf(cfg.t - 1, cfg.n - 1, p_symbol))


def fec_measurements(
    pre_fec_ber: float,
    gsnr_db: float,
    fmt: str | ModulationFormat,
    cfg: FecConfig,
) -> dict[str, float]:
    """The coding-layer KPIs that follow from pre-FEC BER and GSNR.

    Returns:
        Dict with keys post-fec-ber-threshold, post-fec-ber-hd,
        gmi-bits-per-symbol and ngmi.
    """
    gmi = gsnr_to_gmi(gsnr_db, fmt)
    return {
        "post-fec-ber-threshold": post_fec_ber_threshold(pre_fec_ber, cfg.threshold),
        "post-fec-ber-hd": post_fec_ber_hard_decision(pre_fec_ber, cfg.hard_decision),
        "gmi-bits-per-symbol": gmi,
        "ngmi": gmi / get_params(fmt).bits_per_symbol,
    }
