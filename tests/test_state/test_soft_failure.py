"""Tests for the soft-failure engine (state/soft_failure.py).

The manager is clock-injected, so every test drives it with a fake clock
and explicit ``tick()`` calls — no sleeps, no event loop, no flakiness.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest
from pydantic import ValidationError

from tests.test_state.test_config_overrides import _add, _two_sips
from twinlight.state.context import TapiContext
from twinlight.state.soft_failure import (
    JobState,
    Profile,
    SoftFailureConflict,
    SoftFailureError,
    SoftFailureManager,
    SoftFailureSpec,
    shape_fraction,
)

FWD_FIBER = "fiber (Brest → Morlaix)-F"
EAST_AMP = "east edfa in Brest to Morlaix"
FWD_LOSS = f'/Fiber/"{FWD_FIBER}"/loss_coef'


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def mgr(gnpy_context: TapiContext, clock: FakeClock) -> SoftFailureManager:
    gnpy_context.soft_failures.clock = clock
    return gnpy_context.soft_failures


def _spec(**kw: Any) -> SoftFailureSpec:
    return SoftFailureSpec(**{"path": FWD_LOSS, **kw})


def _at(mgr: SoftFailureManager, clock: FakeClock, t: float) -> None:
    clock.now = 1000.0 + t
    mgr.tick()


def _loss(ctx: TapiContext) -> float:
    return float(ctx._backend.read_attribute(FWD_FIBER, "loss_coef"))


class TestSpecValidation:
    def test_needs_exactly_one_change_and_one_speed(self) -> None:
        with pytest.raises(ValidationError, match="exactly one of 'to'"):
            SoftFailureSpec(path="/Fiber/*/loss_coef", duration_s=1)
        with pytest.raises(ValidationError, match="exactly one of 'to'"):
            SoftFailureSpec(path="/Fiber/*/loss_coef", to=1, by=1, duration_s=1)
        with pytest.raises(ValidationError, match="'duration_s', 'rate'"):
            SoftFailureSpec(path="/Fiber/*/loss_coef", by=1)
        with pytest.raises(ValidationError, match="'duration_s', 'rate'"):
            SoftFailureSpec(path="/Fiber/*/loss_coef", by=1, duration_s=1, rate=1)

    def test_direction_rules(self) -> None:
        with pytest.raises(ValidationError, match="magnitude"):
            SoftFailureSpec(path="/x/y/z", by=-1, direction="decrease", duration_s=1)
        with pytest.raises(ValidationError, match="implies"):
            SoftFailureSpec(path="/x/y/z", to=1, direction="increase", duration_s=1)

    def test_rejects_bool_and_nonpositive_speed(self) -> None:
        with pytest.raises(ValidationError):
            SoftFailureSpec(path="/x/y/z", by=True, duration_s=1)
        with pytest.raises(ValidationError):
            SoftFailureSpec(path="/x/y/z", by=1, duration_s=0)
        with pytest.raises(ValidationError):
            SoftFailureSpec(path="/x/y/z", by=1, duration_s=1, interval_s=0.001)


class TestShapes:
    @pytest.mark.parametrize("profile", list(Profile))
    def test_endpoints_and_monotonic(self, profile: Profile) -> None:
        xs = [i / 100 for i in range(101)]
        ys = [shape_fraction(profile, x, 5.0, 4) for x in xs]
        assert ys[0] == pytest.approx(0.0)
        assert ys[-1] == pytest.approx(1.0)
        assert all(b >= a - 1e-12 for a, b in pairwise(ys))

    def test_shapes_differ_where_expected(self) -> None:
        mid = {p: shape_fraction(p, 0.5, 5.0, 4) for p in Profile}
        assert mid[Profile.LINEAR] == pytest.approx(0.5)
        assert mid[Profile.SIGMOID] == pytest.approx(0.5)
        assert mid[Profile.ACCELERATING] < 0.5 < mid[Profile.SATURATING]
        assert shape_fraction(Profile.STEP, 0.49, 5.0, 4) == 0.25


class TestRamp:
    def test_linear_by_with_duration(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        job = mgr.create(_spec(by=0.1, duration_s=10, interval_s=1))
        assert job.id == "sf-1"
        # Nothing written at t=0: the value is already the start value, and
        # writing it would only invalidate baselines for nothing.
        assert FWD_FIBER not in gnpy_context.get_element_overrides()
        _at(mgr, clock, 2.5)
        assert _loss(gnpy_context) == pytest.approx(start + 0.025)
        _at(mgr, clock, 10)
        assert _loss(gnpy_context) == pytest.approx(start + 0.1)
        assert job.state is JobState.COMPLETED
        # Hold: the override stays recorded at the end value.
        assert gnpy_context.get_element_overrides()[FWD_FIBER]["loss_coef"] == (
            pytest.approx(start + 0.1)
        )

    def test_writes_only_when_interval_elapsed(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        mgr.create(_spec(by=0.1, duration_s=10, interval_s=4))
        _at(mgr, clock, 1)  # not due yet
        assert _loss(gnpy_context) == pytest.approx(start)
        _at(mgr, clock, 4)
        assert _loss(gnpy_context) == pytest.approx(start + 0.04)

    def test_tick_reports_next_due_and_lands_on_end(
        self, mgr: SoftFailureManager, clock: FakeClock,
    ) -> None:
        mgr.create(_spec(by=0.1, duration_s=10, interval_s=4))
        clock.now = 1008.0
        # Next regular write would be at t=12, past the end — the engine
        # schedules the final write for t=10 instead.
        assert mgr.tick() == pytest.approx(2.0)
        clock.now = 1010.0
        assert mgr.tick() is None  # finished, nothing left to run

    def test_absolute_target_with_rate(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        job = mgr.create(_spec(to=start + 0.2, rate=0.01))
        assert job.targets[0].duration == pytest.approx(20.0)
        _at(mgr, clock, 5)
        assert _loss(gnpy_context) == pytest.approx(start + 0.05)

    def test_direction_and_percent(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = float(gnpy_context._backend.read_attribute(EAST_AMP, "gain_target"))
        mgr.create(SoftFailureSpec(
            path=f'/Edfa/"{EAST_AMP}"/gain_target',
            by_percent=10, direction="decrease", duration_s=1, interval_s=1,
        ))
        _at(mgr, clock, 1)
        assert float(gnpy_context._backend.read_attribute(EAST_AMP, "gain_target")) == (
            pytest.approx(start * 0.9)
        )

    def test_step_profile_writes_only_at_steps(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        mgr.create(_spec(by=0.1, duration_s=10, interval_s=1, profile="step", steps=2))
        _at(mgr, clock, 4)
        assert FWD_FIBER not in gnpy_context.get_element_overrides()
        _at(mgr, clock, 5)
        assert _loss(gnpy_context) == pytest.approx(start + 0.05)

    def test_start_delay(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        job = mgr.create(_spec(by=0.1, duration_s=10, start_delay_s=5))
        assert mgr.describe(job)["state"] == "scheduled"
        _at(mgr, clock, 5)
        assert _loss(gnpy_context) == pytest.approx(start)
        _at(mgr, clock, 10)
        assert _loss(gnpy_context) == pytest.approx(start + 0.05)
        assert mgr.describe(job)["state"] == "running"


class TestOnComplete:
    def test_reverse_retraces_and_drops_override(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        job = mgr.create(_spec(by=0.1, duration_s=10, on_complete="reverse"))
        _at(mgr, clock, 10)
        assert _loss(gnpy_context) == pytest.approx(start + 0.1)
        _at(mgr, clock, 15)
        assert _loss(gnpy_context) == pytest.approx(start + 0.05)
        _at(mgr, clock, 20)
        assert _loss(gnpy_context) == pytest.approx(start)
        assert job.state is JobState.COMPLETED
        assert FWD_FIBER not in gnpy_context.get_element_overrides()

    def test_revert_snaps_back(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        mgr.create(_spec(by=0.1, duration_s=10, on_complete="revert"))
        _at(mgr, clock, 9)
        assert _loss(gnpy_context) == pytest.approx(start + 0.09)
        _at(mgr, clock, 10)
        assert _loss(gnpy_context) == pytest.approx(start)

    def test_revert_keeps_a_pre_existing_override(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        gnpy_context.apply_element_overrides({FWD_FIBER: {"loss_coef": 0.25}})
        mgr.create(_spec(by=0.1, duration_s=10, on_complete="revert"))
        _at(mgr, clock, 10)
        assert gnpy_context.get_element_overrides()[FWD_FIBER]["loss_coef"] == 0.25


class TestBounds:
    def test_reject_out_of_range(self, mgr: SoftFailureManager) -> None:
        with pytest.raises(SoftFailureError, match="out of range"):
            mgr.create(_spec(to=5.0, duration_s=10))
        assert mgr.list() == []

    def test_clamp_stops_at_bound(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        job = mgr.create(_spec(to=5.0, duration_s=10, out_of_bounds="clamp"))
        assert job.targets[0].end == 2.0
        _at(mgr, clock, 10)
        assert _loss(gnpy_context) == pytest.approx(2.0)


class TestControl:
    def test_pause_freezes_time_and_resume_continues(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        job = mgr.create(_spec(by=0.1, duration_s=10, interval_s=10))
        clock.now = 1004.0
        mgr.pause(job.id)  # lands the value at the pause instant
        assert _loss(gnpy_context) == pytest.approx(start + 0.04)
        _at(mgr, clock, 100)  # paused: no progress
        assert _loss(gnpy_context) == pytest.approx(start + 0.04)
        clock.now = 1100.0
        mgr.resume(job.id)
        _at(mgr, clock, 106)
        assert job.state is JobState.COMPLETED
        assert _loss(gnpy_context) == pytest.approx(start + 0.1)

    def test_stop_holds_value(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        job = mgr.create(_spec(by=0.1, duration_s=10))
        clock.now = 1003.0
        mgr.stop(job.id)
        _at(mgr, clock, 10)
        assert job.state is JobState.STOPPED
        assert _loss(gnpy_context) == pytest.approx(start + 0.03)
        with pytest.raises(SoftFailureConflict):
            mgr.resume(job.id)

    def test_delete_with_revert(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        start = _loss(gnpy_context)
        job = mgr.create(_spec(by=0.1, duration_s=10))
        _at(mgr, clock, 6)
        mgr.delete(job.id, revert=True)
        assert _loss(gnpy_context) == pytest.approx(start)
        assert FWD_FIBER not in gnpy_context.get_element_overrides()
        with pytest.raises(KeyError):
            mgr.get(job.id)

    def test_conflicting_job_rejected_until_first_finishes(
        self, mgr: SoftFailureManager, clock: FakeClock,
    ) -> None:
        mgr.create(_spec(by=0.1, duration_s=10))
        with pytest.raises(SoftFailureConflict, match="sf-1"):
            mgr.create(SoftFailureSpec(path="/Fiber/*/loss_coef", by=0.1, duration_s=5))
        # A different attribute on the same element is fine.
        mgr.create(SoftFailureSpec(
            path=f'/Fiber/"{FWD_FIBER}"/att_in', by=1, duration_s=5,
        ))
        _at(mgr, clock, 10)
        mgr.create(_spec(by=0.1, duration_s=10))

    def test_write_failure_marks_job_failed_and_keeps_others(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        bad = mgr.create(_spec(by=0.1, duration_s=10))
        good = mgr.create(SoftFailureSpec(
            path=f'/Edfa/"{EAST_AMP}"/tilt_target', by=1, duration_s=10,
        ))
        # Someone pushes the target out of reach mid-ramp: the next write
        # (start + 0.05) stays legal, so break it at the backend instead.
        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("element went away")
        real = gnpy_context.apply_element_overrides
        gnpy_context.apply_element_overrides = (  # type: ignore[method-assign]
            lambda updates, **kw: boom() if FWD_FIBER in updates else real(updates, **kw)
        )
        _at(mgr, clock, 5)
        assert bad.state is JobState.FAILED
        assert "went away" in (bad.error or "")
        assert good.state is JobState.RUNNING

    def test_unset_attribute_is_skipped_not_driven(
        self, mgr: SoftFailureManager,
    ) -> None:
        # nf0 is unset on equipment-library amps that model NF via
        # nf_min/nf_max; a path matching only those cannot be driven.
        with pytest.raises(SoftFailureError, match="cannot be driven"):
            mgr.create(SoftFailureSpec(
                path=f'/Edfa/"{EAST_AMP}"/nf0', by=1, duration_s=10,
            ))

    def test_bad_path_and_empty_match(self, mgr: SoftFailureManager) -> None:
        with pytest.raises(SoftFailureError, match="starts with"):
            mgr.create(SoftFailureSpec(path="Fiber", by=1, duration_s=1))
        with pytest.raises(SoftFailureError, match="matches no parameter"):
            mgr.create(SoftFailureSpec(path="/Fiber/nope/loss_coef", by=1, duration_s=1))
        with pytest.raises(SoftFailureError, match="one attribute"):
            mgr.create(SoftFailureSpec(path="/Fiber/*/*", by=1, duration_s=1))


class TestTwinIntegration:
    async def test_ramp_invalidates_baseline_and_lowers_gsnr(
        self, mgr: SoftFailureManager, clock: FakeClock, gnpy_context: TapiContext,
    ) -> None:
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = await _add(gnpy_context, sip_a, sip_z)
        before = await gnpy_context.get_or_compute_baseline(svc)
        assert before is not None

        job = mgr.create(SoftFailureSpec(
            path=f"/Service/{svc.uuid}/Fiber/*/loss_coef", by=0.1, duration_s=10,
        ))
        assert mgr.describe(job)["affected_services"] == [svc.uuid]
        _at(mgr, clock, 5)
        assert svc.uuid not in gnpy_context._baseline_cache
        mid = await gnpy_context.get_or_compute_baseline(svc)
        _at(mgr, clock, 10)
        after = await gnpy_context.get_or_compute_baseline(svc)
        assert mid is not None and after is not None
        assert before.gsnr_db > mid.gsnr_db > after.gsnr_db

    def test_restore_clears_jobs(
        self, mgr: SoftFailureManager, gnpy_context: TapiContext, tmp_path: Any,
    ) -> None:
        snap = gnpy_context.snapshot(tmp_path / "snap.json")
        mgr.create(_spec(by=0.1, duration_s=10))
        gnpy_context.restore_from(snap)
        assert mgr.list() == []
