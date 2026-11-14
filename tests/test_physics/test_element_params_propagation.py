"""Every writable GNPy attribute must actually move the propagated result.

An allow-listed knob that propagation never reads would let a client "change"
the network and see nothing happen — or worse, believe a what-if that was
never evaluated. Each attribute exposed for writing is therefore checked
end to end here: write it on the live element, propagate the Brest → Morlaix
path, and compare with the propagation before the write.

Also covers the capture/restore tokens the transparent controller uses to
revert to startup values, including attributes GNPy loaded as unset.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from twinlight.physics.element_params import ALLOWED, capture_attr, restore_attr
from twinlight.state.context import TapiContext

FWD_FIBER = "fiber (Brest → Morlaix)-F"
EAST_AMP = "east edfa in Brest to Morlaix"


def _baseline(ctx: TapiContext) -> Any:
    by_trx = {ctx.get_gnpy_uid(s.uuid): s.uuid for s in ctx.sips}
    path = ctx.get_service_path(by_trx["trx Brest_1"], by_trx["trx Morlaix_1"])
    assert path and FWD_FIBER in path and EAST_AMP in path
    baseline = asyncio.run(ctx._backend.compute_baseline(
        path, "DP-QPSK", centre_frequency_hz=193.1e12, media_channel_width_hz=56.25e9,
        launch_power_dbm=ctx.default_launch_power_dbm,
    ))
    assert baseline is not None
    return baseline


FWD_BOOSTER = "Edfa_booster_roadm Brest_to_fiber (Brest → Morlaix)-F"
ROADM_A = "roadm Brest"

# (uid, attribute, new value, metric, expected direction of the metric; 0 =
# it must move, in a direction that depends on the operating point)
CASES = [
    (FWD_FIBER, "loss_coef", 0.25, "gsnr_db", -1),
    (FWD_FIBER, "att_in", 3.0, "gsnr_db", -1),
    (FWD_BOOSTER, "gain_target", 14.0, "gsnr_db", 0),
    (FWD_BOOSTER, "tilt_target", 1.0, "gsnr_db", 0),
    (FWD_BOOSTER, "out_voa", 3.0, "gsnr_db", 0),
    (EAST_AMP, "nf_offset", 2.0, "gsnr_db", -1),
    (ROADM_A, "target_pch_out_db", -10.0, "gsnr_db", 0),
    (FWD_FIBER, "con_in", 3.0, "gsnr_db", -1),
    (FWD_FIBER, "con_out", 3.0, "gsnr_db", -1),
    (FWD_FIBER, "pmd_coef", 0.4, "pmd_ps", +1),
    (FWD_FIBER, "dispersion", 4.0, "cd_ps_nm", -1),
    (EAST_AMP, "in_voa", 5.0, "gsnr_db", -1),
    (EAST_AMP, "p_max", 5.0, "gsnr_db", -1),
    (EAST_AMP, "pmd", 2.0, "pmd_ps", +1),
]


@pytest.mark.parametrize(("uid", "attr", "value", "metric", "sign"), CASES)
def test_writable_attribute_reaches_propagation(
    gnpy_context: TapiContext, uid: str, attr: str, value: float,
    metric: str, sign: int,
) -> None:
    before = getattr(_baseline(gnpy_context), metric)
    gnpy_context._backend.write_attribute(uid, attr, value)
    assert gnpy_context._backend.read_attribute(uid, attr) == pytest.approx(value)
    after = getattr(_baseline(gnpy_context), metric)
    if sign:
        assert (after - before) * sign > 1e-6, (attr, before, after)
    else:
        assert abs(after - before) > 1e-6, (attr, before, after)


def test_units_are_user_facing(gnpy_context: TapiContext) -> None:
    """Displayed values are in datasheet units, not GNPy's SI storage."""
    read = gnpy_context._backend.read_attribute
    assert read(FWD_FIBER, "pmd_coef") == pytest.approx(0.04, rel=1e-3)
    assert read(FWD_FIBER, "dispersion") == pytest.approx(16.7)
    assert read(FWD_FIBER, "con_in") == pytest.approx(0.5)
    assert read(EAST_AMP, "p_max") == pytest.approx(23.0)
    assert read(EAST_AMP, "pmd") == pytest.approx(0.0)


def test_frequency_dependent_dispersion_is_not_flattened(
    gnpy_context: TapiContext,
) -> None:
    import numpy as np

    from twinlight.physics.element_params import ParamValidationError

    fiber = gnpy_context._backend.uid_map[FWD_FIBER]
    fiber.params._dispersion = np.array([1.6e-5, 1.7e-5])
    assert gnpy_context._backend.read_attribute(FWD_FIBER, "dispersion") is None
    with pytest.raises(ParamValidationError, match="frequency-dependent"):
        gnpy_context._backend.write_attribute(FWD_FIBER, "dispersion", 10.0)


@pytest.mark.parametrize(
    ("uid", "attr"),
    [(FWD_FIBER, a) for a in ("loss_coef", "con_in", "pmd_coef", "dispersion")]
    + [(EAST_AMP, a) for a in ("gain_target", "nf_offset", "p_max", "pmd")],
)
def test_capture_restore_round_trips(
    gnpy_context: TapiContext, uid: str, attr: str,
) -> None:
    backend = gnpy_context._backend
    original = backend.read_attribute(uid, attr)
    token = backend.capture_attribute(uid, attr)
    spec = ALLOWED[type(backend.uid_map[uid])][attr]
    backend.write_attribute(uid, attr, spec.max_value)
    assert backend.read_attribute(uid, attr) != pytest.approx(original)
    backend.restore_attribute(uid, attr, token)
    assert backend.read_attribute(uid, attr) == pytest.approx(original)


def test_restore_puts_back_an_unset_value(gnpy_context: TapiContext) -> None:
    """A tilt GNPy loaded as None comes back as None, not as a number."""
    amp = gnpy_context._backend.uid_map[EAST_AMP]
    amp.tilt_target = None
    amp.operational.tilt_target = None
    token = capture_attr(amp, "tilt_target")
    gnpy_context._backend.write_attribute(EAST_AMP, "tilt_target", 0.5)
    restore_attr(amp, "tilt_target", token)
    assert amp.tilt_target is None and amp.operational.tilt_target is None


def test_capture_does_not_alias_arrays(gnpy_context: TapiContext) -> None:
    """The token must survive an in-place change to the stored ndarray."""
    fiber = gnpy_context._backend.uid_map[FWD_FIBER]
    token = capture_attr(fiber, "loss_coef")
    fiber.params._loss_coef *= 2  # in place
    restore_attr(fiber, "loss_coef", token)
    assert gnpy_context._backend.read_attribute(FWD_FIBER, "loss_coef") == pytest.approx(0.2)


def test_roadm_target_moves_inherited_degrees_only(gnpy_context: TapiContext) -> None:
    """Regression: on a designed network the ROADM-level target alone is
    never read — every egress degree carries its own copy."""
    roadm = gnpy_context._backend.uid_map[ROADM_A]
    [degree] = roadm.per_degree_pch_out_dbm
    token = capture_attr(roadm, "target_pch_out_db")
    roadm.per_degree_pch_out_dbm["explicit degree"] = -15.0
    gnpy_context._backend.write_attribute(ROADM_A, "target_pch_out_db", -10.0)
    assert roadm.per_degree_pch_out_dbm == {degree: -10.0, "explicit degree": -15.0}
    assert roadm.params.per_degree_pch_out_db is roadm.per_degree_pch_out_dbm
    restore_attr(roadm, "target_pch_out_db", token)
    assert roadm.per_degree_pch_out_dbm == {degree: -20}
    assert roadm.params.per_degree_pch_out_db is roadm.per_degree_pch_out_dbm
