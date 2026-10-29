"""Tests for immediate, path-addressed parameter changes (state/parameter_ops.py)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tests.test_state.test_config_overrides import _add, _two_sips
from twinlight.state.context import TapiContext
from twinlight.state.parameter_ops import (
    ParameterChange,
    ParameterOpError,
    apply,
    plan_changes,
    plan_set,
)

FWD_FIBER = "fiber (Brest → Morlaix)-F"
REV_FIBER = "fiber (Morlaix → Brest)-F"
EAST_AMP = "east edfa in Brest to Morlaix"
FWD_LOSS = f'/Fiber/"{FWD_FIBER}"/loss_coef'


def _read(ctx: TapiContext, uid: str, attr: str) -> Any:
    return ctx._backend.read_attribute(uid, attr)


def _change(**kw: Any) -> ParameterChange:
    return ParameterChange(**kw)


class TestChangeModel:
    def test_exactly_one_change(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            _change(path="/Fiber/*/loss_coef")
        with pytest.raises(ValidationError, match="exactly one"):
            _change(path="/Fiber/*/loss_coef", to=1, by=1)

    def test_direction_rules(self) -> None:
        with pytest.raises(ValidationError, match="magnitude"):
            _change(path="/x/y/z", by=-1, direction="decrease")
        with pytest.raises(ValidationError, match="implies"):
            _change(path="/x/y/z", to=1, direction="increase")
        with pytest.raises(ValidationError):
            _change(path="/x/y/z", by=True)

    @pytest.mark.parametrize(("kw", "expected"), [
        ({"to": 3.0}, 3.0),
        ({"by": 0.5}, 2.5),
        ({"by": -0.5}, 1.5),
        ({"by": 0.5, "direction": "decrease"}, 1.5),
        ({"by_percent": 10}, 2.2),
        ({"by_percent": 10, "direction": "decrease"}, 1.8),
    ])
    def test_target_value(self, kw: dict, expected: float) -> None:
        assert _change(path="/x/y/z", **kw).target_value(2.0) == pytest.approx(expected)


class TestPlanSet:
    def test_resolves_and_reports_previous(self, gnpy_context: TapiContext) -> None:
        plan = plan_set(gnpy_context, {"/Fiber/*/loss_coef": 0.3})
        assert [a.ref.uid for a in plan] == sorted([FWD_FIBER, REV_FIBER])
        assert all(a.previous == pytest.approx(0.2) and a.value == 0.3 for a in plan)
        # Planning writes nothing.
        assert _read(gnpy_context, FWD_FIBER, "loss_coef") == pytest.approx(0.2)

    def test_collects_every_error(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(ParameterOpError) as exc:
            plan_set(gnpy_context, {
                "/Fiber/*/loss_coef": 5.0,          # out of range (x2)
                "/Fiber/nope/loss_coef": 0.1,      # matches nothing
                "/Edfa/*/gain_target": "high",     # not a number
                "/Fiber/*": 0.1,                   # malformed
            })
        msg = str(exc.value)
        assert msg.count("outside") == 2
        assert "matches no parameter" in msg
        assert "must be a number" in msg
        assert "Expected /<Kind>" in msg

    def test_same_parameter_from_two_paths_is_refused(
        self, gnpy_context: TapiContext,
    ) -> None:
        with pytest.raises(ParameterOpError, match="targeted by both"):
            plan_set(gnpy_context, {"/Fiber/*/loss_coef": 0.3, FWD_LOSS: 0.3})

    def test_wildcard_attribute_refused(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(ParameterOpError, match="one attribute"):
            plan_set(gnpy_context, {"/Fiber/*/*": 0.3})


class TestPlanChanges:
    def test_relative_changes_use_each_targets_own_value(
        self, gnpy_context: TapiContext,
    ) -> None:
        gnpy_context.apply_element_overrides({REV_FIBER: {"loss_coef": 0.3}})
        [plan] = plan_changes(gnpy_context, [_change(path="/Fiber/*/loss_coef", by=0.05)])
        values = {a.ref.uid: a.value for a in plan.assignments}
        assert values == {
            FWD_FIBER: pytest.approx(0.25), REV_FIBER: pytest.approx(0.35),
        }

    def test_clamp_and_reject(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(ParameterOpError, match="out_of_bounds='clamp'"):
            plan_changes(gnpy_context, [_change(path=FWD_LOSS, by=5)])
        [plan] = plan_changes(
            gnpy_context, [_change(path=FWD_LOSS, by=5, out_of_bounds="clamp")],
        )
        assert plan.assignments[0].value == 2.0

    def test_unset_attribute_skipped_for_relative_change(
        self, gnpy_context: TapiContext,
    ) -> None:
        # GNPy allows an amplifier with no tilt at all; "by 1 dB" of
        # nothing is undefined.
        gnpy_context._backend.uid_map[EAST_AMP].tilt_target = None
        with pytest.raises(ParameterOpError, match="no parameter it matches"):
            plan_changes(
                gnpy_context, [_change(path=f'/Edfa/"{EAST_AMP}"/tilt_target', by=1)],
            )

    def test_two_changes_on_one_parameter_refused(
        self, gnpy_context: TapiContext,
    ) -> None:
        with pytest.raises(ParameterOpError, match="change #0 .* and change #1"):
            plan_changes(gnpy_context, [
                _change(path="/Fiber/*/loss_coef", by=0.1),
                _change(path=FWD_LOSS, to=0.3),
            ])

    def test_errors_name_the_entry(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(ParameterOpError, match="change #1: .*matches no parameter"):
            plan_changes(gnpy_context, [
                _change(path=FWD_LOSS, by=0.1),
                _change(path="/Fiber/nope/loss_coef", by=0.1),
            ])


class TestApply:
    async def test_applies_and_invalidates(self, gnpy_context: TapiContext) -> None:
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = await _add(gnpy_context, sip_a, sip_z)
        before = await gnpy_context.get_or_compute_baseline(svc)
        plans = plan_changes(gnpy_context, [
            _change(path="/Fiber/*/loss_coef", by=0.1),
            _change(path=f'/Edfa/"{EAST_AMP}"/tilt_target', to=0.5),
        ])
        invalidated = apply(gnpy_context, [a for p in plans for a in p.assignments])
        assert invalidated == {svc.uuid}
        assert _read(gnpy_context, FWD_FIBER, "loss_coef") == pytest.approx(0.3)
        assert _read(gnpy_context, EAST_AMP, "tilt_target") == 0.5
        assert gnpy_context.get_element_overrides()[FWD_FIBER] == {"loss_coef": 0.3}
        after = await gnpy_context.get_or_compute_baseline(svc)
        assert before is not None and after is not None
        assert after.gsnr_db < before.gsnr_db

    def test_failure_mid_batch_rolls_back(
        self, gnpy_context: TapiContext, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A write failing despite validation leaves the twin unchanged."""
        backend = gnpy_context._backend
        real_write = backend.write_attribute
        calls = {"n": 0}

        def flaky(uid: str, attr: str, value: Any) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("element went away")
            real_write(uid, attr, value)

        monkeypatch.setattr(backend, "write_attribute", flaky)
        plan = plan_set(gnpy_context, {"/Fiber/*/loss_coef": 0.3})
        with pytest.raises(RuntimeError, match="went away"):
            apply(gnpy_context, plan)
        assert _read(gnpy_context, FWD_FIBER, "loss_coef") == pytest.approx(0.2)
        assert _read(gnpy_context, REV_FIBER, "loss_coef") == pytest.approx(0.2)
        assert gnpy_context.get_element_overrides() == {}
