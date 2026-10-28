"""Tests for the post-FEC BER models and their configuration."""

import itertools
import math

import numpy as np
import pytest
from pydantic import ValidationError
from scipy.optimize import brentq
from scipy.special import erfcinv

from twinlight.config import (
    FecConfig,
    FecThresholdConfig,
    HardDecisionFecConfig,
    TwinConfig,
)
from twinlight.physics.fec import (
    fec_measurements,
    post_fec_ber_hard_decision,
    post_fec_ber_threshold,
)
from twinlight.physics.gmi import gsnr_to_gmi


class TestThresholdModel:
    cfg = FecThresholdConfig(pre_fec_ber_threshold=3.8e-3, post_fec_ber_floor=1e-15)

    def test_below_threshold_reports_the_floor(self):
        assert post_fec_ber_threshold(1e-3, self.cfg) == 1e-15

    def test_threshold_itself_is_still_corrected(self):
        assert post_fec_ber_threshold(3.8e-3, self.cfg) == 1e-15

    def test_above_threshold_errors_pass_through(self):
        assert post_fec_ber_threshold(4e-3, self.cfg) == 4e-3

    def test_never_reports_more_errors_than_came_in(self):
        assert post_fec_ber_threshold(1e-20, self.cfg) == 1e-20


def _direct_sum(p: float, n: int, t: int, m: int) -> float:
    """Sklar's decoded symbol error rate term by term, at the channel ratio.

    Each term is formed in log space: C(1023, 511) alone exceeds a float.
    """
    p_s = 1 - (1 - p) ** m

    def log_term(j: int) -> float:
        log_comb = math.lgamma(n + 1) - math.lgamma(j + 1) - math.lgamma(n - j + 1)
        return math.log(j) + log_comb + j * math.log(p_s) + (n - j) * math.log1p(-p_s)

    p_e = sum(math.exp(log_term(j)) for j in range(t + 1, n + 1)) / n
    return p_e * p / p_s


def _net_coding_gain_db(cfg: HardDecisionFecConfig, ber_out: float) -> float:
    """NCG = 20·log10(erfcinv(2·BER_out) / erfcinv(2·BER_in)) + 10·log10(k/n)."""
    log_ber_in = brentq(
        lambda x: math.log10(post_fec_ber_hard_decision(10**x, cfg)) - math.log10(ber_out),
        -8, -1,
    )
    return (
        20 * math.log10(erfcinv(2 * ber_out) / erfcinv(2 * 10**log_ber_in))
        + 10 * math.log10(cfg.k / cfg.n)
    )


class TestHardDecisionModel:
    rs = HardDecisionFecConfig()  # RS(255,239), t = 8, GF(2^8)

    @pytest.mark.parametrize("p", [1e-4, 1e-3, 5e-3, 2e-2])
    def test_reed_solomon_matches_the_direct_sum(self, p):
        assert post_fec_ber_hard_decision(p, self.rs) == pytest.approx(
            _direct_sum(p, 255, 8, 8), rel=1e-9
        )

    @pytest.mark.parametrize("p", [1e-4, 1e-3, 1e-2])
    def test_binary_code_matches_the_direct_sum(self, p):
        bch = HardDecisionFecConfig(n=1023, k=993, t=3, symbol_bits=1)
        assert post_fec_ber_hard_decision(p, bch) == pytest.approx(
            _direct_sum(p, 1023, 3, 1), rel=1e-9
        )

    def test_reproduces_the_published_rs_coding_gain(self):
        """Smith et al., JLT 30(1), 2012: RS(255,239) has 6.2 dB NCG at 1e-15."""
        assert _net_coding_gain_db(self.rs, 1e-15) == pytest.approx(6.2, abs=0.05)

    def test_output_never_exceeds_input(self):
        for p in np.logspace(-8, 0, 81):
            assert post_fec_ber_hard_decision(p, self.rs) <= p

    def test_tends_to_the_input_once_decoding_always_fails(self):
        assert post_fec_ber_hard_decision(0.2, self.rs) == pytest.approx(0.2, rel=1e-6)

    def test_rises_with_pre_fec_ber(self):
        out = [post_fec_ber_hard_decision(p, self.rs) for p in np.logspace(-6, -1, 51)]
        assert all(a < b for a, b in itertools.pairwise(out))

    def test_error_free_input_stays_error_free(self):
        assert post_fec_ber_hard_decision(0.0, self.rs) == 0.0

    @pytest.mark.parametrize("p", [-0.1, 1.5])
    def test_rejects_a_ber_outside_unit_interval(self, p):
        with pytest.raises(ValueError):
            post_fec_ber_hard_decision(p, self.rs)


class TestHardDecisionConfig:
    def test_default_is_the_g709_reed_solomon_code(self):
        cfg = HardDecisionFecConfig()
        assert (cfg.n, cfg.k, cfg.t, cfg.symbol_bits) == (255, 239, 8, 8)

    def test_rejects_k_not_below_n(self):
        with pytest.raises(ValidationError):
            HardDecisionFecConfig(n=255, k=255, t=1)

    def test_rejects_t_beyond_the_singleton_bound(self):
        with pytest.raises(ValidationError):
            HardDecisionFecConfig(n=255, k=239, t=9)

    def test_rejects_a_reed_solomon_code_longer_than_its_field(self):
        with pytest.raises(ValidationError):
            HardDecisionFecConfig(n=300, k=284, t=8, symbol_bits=8)

    def test_binary_codes_are_not_bound_by_the_field_size(self):
        HardDecisionFecConfig(n=1023, k=993, t=3, symbol_bits=1)


class TestFecSectionOfTwinConfig:
    def test_defaults_when_omitted(self):
        cfg = TwinConfig.model_validate({"gnpy": {"topology": "t.json"}})
        assert cfg.fec == FecConfig()

    def test_reads_a_partial_section(self):
        cfg = TwinConfig.model_validate({
            "gnpy": {"topology": "t.json"},
            "fec": {"threshold": {"pre_fec_ber_threshold": 2.0e-2}},
        })
        assert cfg.fec.threshold.pre_fec_ber_threshold == 2.0e-2
        assert cfg.fec.hard_decision == HardDecisionFecConfig()

    def test_rejects_unknown_keys(self):
        with pytest.raises(ValidationError):
            TwinConfig.model_validate({
                "gnpy": {"topology": "t.json"},
                "fec": {"soft_decision": {}},
            })


class TestFecMeasurements:
    def test_each_kpi_comes_from_its_own_model(self):
        cfg = FecConfig()
        out = fec_measurements(1e-3, 15.0, "DP-16QAM", cfg)
        assert out == {
            "post-fec-ber-threshold": post_fec_ber_threshold(1e-3, cfg.threshold),
            "post-fec-ber-hd": post_fec_ber_hard_decision(1e-3, cfg.hard_decision),
            "gmi-bits-per-symbol": gsnr_to_gmi(15.0, "DP-16QAM"),
            "ngmi": gsnr_to_gmi(15.0, "DP-16QAM") / 4,
        }

    def test_configured_threshold_is_honoured(self):
        """1e-2 fails the 7% HD-FEC limit but is corrected by oFEC's 2e-2."""
        hd_fec = FecConfig()
        ofec = FecConfig(threshold=FecThresholdConfig(pre_fec_ber_threshold=2e-2))
        assert fec_measurements(1e-2, 10.0, "DP-QPSK", hd_fec)["post-fec-ber-threshold"] == 1e-2
        assert fec_measurements(1e-2, 10.0, "DP-QPSK", ofec)["post-fec-ber-threshold"] == 1e-15
