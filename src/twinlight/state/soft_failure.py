"""Soft-failure engine: gradual, scheduled drift of physical-layer parameters.

A *hard* failure is a step — a fiber cut, ``failed: true`` on the
``/config`` plane. A *soft* failure is a slow degradation that leaves the
lightpath up while its margin erodes: a span's loss creeping up as a
connector contaminates or a splice ages, an amplifier's noise figure rising
as its pump degrades, a ROADM's per-channel target drifting. Detecting and
localising those before they become outages is the point of a twin, so the
twin needs a way to *produce* them on demand.

A soft-failure *job* binds three things:

* **what** — a parameter path (``state/parameter_path.py``) resolving to
  one or more (element, attribute) targets, e.g. ``/Fiber/*/loss_coef``;
* **where to** — an absolute end value (``to``), an additive change
  (``by``), or a relative one (``by_percent``), optionally with an explicit
  ``direction``;
* **how fast** — a ``duration_s`` or a ``rate`` (attribute units per
  second), a profile shaping the trajectory, the write cadence
  (``interval_s``), a ``start_delay_s``, and what happens at the end
  (``on_complete``: hold, revert, or reverse back along the same path).

Every write goes through ``TapiContext.apply_element_overrides`` — the same
validated path ``POST /config/set`` uses — so the allow-list bounds, the
override bookkeeping and the per-service baseline invalidation all apply
unchanged. A job is therefore exactly equivalent to an operator issuing a
sequence of ``/config/set`` calls, just with better timing.

Trajectories are pure functions of the job's elapsed active time, so the
values are reproducible regardless of when the runner happens to wake.
The profiles are scenario shapes, not physical models of any particular
degradation mechanism; pick the one that matches the failure you want to
rehearse (e.g. ``accelerating`` for a pump that fails faster as it ages,
``saturating`` for a connector that settles at a worse loss).

The engine is clock-injected. In production ``run()`` drives it from an
asyncio task started by the app's lifespan; tests call ``tick(now)`` with a
fake clock and need neither sleeps nor an event loop.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from twinlight.state.parameter_path import (
    ParameterPath,
    ParameterPathError,
    ParameterRef,
    parse,
    resolve,
)

if TYPE_CHECKING:
    from twinlight.state.context import TapiContext

logger = logging.getLogger(__name__)


class SoftFailureError(ValueError):
    """The job definition is invalid against the live twin (→ HTTP 400)."""


class SoftFailureConflict(RuntimeError):
    """The request clashes with another job or the job's state (→ HTTP 409)."""


# -- Specification -----------------------------------------------------------


class Profile(str, Enum):
    LINEAR = "linear"
    ACCELERATING = "accelerating"
    SATURATING = "saturating"
    SIGMOID = "sigmoid"
    STEP = "step"


class Direction(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"


class OnComplete(str, Enum):
    HOLD = "hold"
    REVERT = "revert"
    REVERSE = "reverse"


class OutOfBounds(str, Enum):
    REJECT = "reject"
    CLAMP = "clamp"


class SoftFailureSpec(BaseModel):
    """Request body of ``POST /config/soft-failures``."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    name: str | None = Field(default=None, description="Free-form label.")
    path: str = Field(
        description="Parameter path, e.g. /Fiber/*/loss_coef. See "
        "docs/API.md#parameter-paths.",
    )
    to: float | None = Field(
        default=None, description="Absolute end value, in attribute units.",
    )
    by: float | None = Field(
        default=None,
        description="Additive change from each target's start value. Signed, "
        "unless 'direction' is given, in which case it is a magnitude.",
    )
    by_percent: float | None = Field(
        default=None,
        description="Relative change from each target's start value, in %. "
        "Signed, unless 'direction' is given.",
    )
    direction: Direction | None = Field(
        default=None,
        description="Apply 'by'/'by_percent' as a magnitude in this direction.",
    )
    duration_s: float | None = Field(
        default=None, gt=0, description="Ramp duration in seconds.",
    )
    rate: float | None = Field(
        default=None, gt=0,
        description="Average ramp speed in attribute units per second. Each "
        "target's duration is |end - start| / rate.",
    )
    profile: Profile = Profile.LINEAR
    shape: float = Field(
        default=5.0, gt=0,
        description="Steepness of the accelerating/saturating/sigmoid profiles.",
    )
    steps: int = Field(
        default=5, ge=1, le=10_000,
        description="Number of equal increments of the step profile.",
    )
    interval_s: float = Field(
        default=1.0, ge=0.05, description="Seconds between parameter writes.",
    )
    start_delay_s: float = Field(default=0.0, ge=0)
    on_complete: OnComplete = OnComplete.HOLD
    out_of_bounds: OutOfBounds = Field(
        default=OutOfBounds.REJECT,
        description="'reject' refuses a job whose end value leaves the "
        "attribute's allowed range; 'clamp' stops at the bound.",
    )

    @field_validator(
        "to", "by", "by_percent", "duration_s", "rate", "shape",
        "interval_s", "start_delay_s", mode="before",
    )
    @classmethod
    def _no_bools(cls, v: Any) -> Any:
        # bool is an int subclass; ``true`` must not silently become 1.0.
        if isinstance(v, bool):
            # ValueError, not TypeError: pydantic only turns ValueError into
            # a validation error (and so a 422) rather than a 500.
            raise ValueError("expected a number, got bool")  # noqa: TRY004
        return v

    @model_validator(mode="after")
    def _one_of_each(self) -> SoftFailureSpec:
        changes = [f for f in ("to", "by", "by_percent") if getattr(self, f) is not None]
        if len(changes) != 1:
            raise ValueError(
                "give exactly one of 'to', 'by', 'by_percent' "
                f"(got {changes or 'none'})"
            )
        speeds = [f for f in ("duration_s", "rate") if getattr(self, f) is not None]
        if len(speeds) != 1:
            raise ValueError(
                f"give exactly one of 'duration_s', 'rate' (got {speeds or 'none'})"
            )
        if self.direction is not None:
            if self.to is not None:
                raise ValueError(
                    "'direction' applies to 'by'/'by_percent'; an absolute "
                    "'to' already implies it"
                )
            magnitude = self.by if self.by is not None else self.by_percent
            if magnitude is not None and magnitude < 0:
                raise ValueError(
                    "with 'direction', 'by'/'by_percent' is a magnitude and "
                    "must be >= 0"
                )
        return self


# -- Trajectory shapes -------------------------------------------------------


def shape_fraction(profile: Profile, x: float, shape: float, steps: int) -> float:
    """Map normalised time ``x`` ∈ [0, 1] to normalised progress ∈ [0, 1].

    Every profile satisfies f(0) = 0 and f(1) = 1 and is monotone, so the
    trajectory never overshoots the end value and the allow-list check on
    the end value bounds every intermediate write too.
    """
    x = min(1.0, max(0.0, x))
    if profile is Profile.LINEAR:
        return x
    if profile is Profile.STEP:
        return 1.0 if x >= 1.0 else math.floor(x * steps) / steps
    if profile is Profile.ACCELERATING:
        return math.expm1(shape * x) / math.expm1(shape)
    if profile is Profile.SATURATING:
        return -math.expm1(-shape * x) / -math.expm1(-shape)
    # SIGMOID: logistic centred on x = 0.5, rescaled to hit 0 and 1 exactly.
    def logistic(u: float) -> float:
        return 1.0 / (1.0 + math.exp(-shape * (u - 0.5)))
    lo, hi = logistic(0.0), logistic(1.0)
    return (logistic(x) - lo) / (hi - lo)


# -- Runtime state -----------------------------------------------------------


class JobState(str, Enum):
    PLANNED = "planned"       # dry run: validated, never registered
    SCHEDULED = "scheduled"   # running, but start_delay_s has not elapsed
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


_ACTIVE = {JobState.RUNNING, JobState.PAUSED}


@dataclass
class _Target:
    ref: ParameterRef
    start: float
    end: float
    duration: float
    # Whether /config already carried an override for (uid, attr) before the
    # job started — decides whether returning to ``start`` should also drop
    # the override entry, leaving /config/get exactly as it was.
    had_override: bool
    # Last value this job wrote. Starts at ``start`` so the first tick does
    # not write (and invalidate baselines for) a value that is already set.
    commanded: float = 0.0

    def __post_init__(self) -> None:
        self.commanded = self.start

    def value_at(self, tau: float, spec: SoftFailureSpec) -> float:
        """Commanded value ``tau`` seconds into the ramp."""
        d = self.duration
        if spec.on_complete is OnComplete.REVERSE:
            if d <= 0 or tau >= 2 * d:
                frac = 0.0
            else:
                # Mirror in time: the recovery retraces the degradation.
                t = tau if tau <= d else 2 * d - tau
                frac = shape_fraction(spec.profile, t / d, spec.shape, spec.steps)
        elif d <= 0:
            frac = 1.0
        else:
            frac = shape_fraction(spec.profile, tau / d, spec.shape, spec.steps)
        # Rounded so float noise (0.22000000000000003) does not leak into
        # /config/get and snapshots; 1e-9 is far below any meaningful step.
        return round(self.start + (self.end - self.start) * frac, 9)

    def total(self, spec: SoftFailureSpec) -> float:
        return self.duration * (2 if spec.on_complete is OnComplete.REVERSE else 1)


@dataclass
class SoftFailureJob:
    id: str
    spec: SoftFailureSpec
    path: ParameterPath
    targets: list[_Target]
    skipped: list[dict[str, Any]]
    created_at: datetime
    state: JobState = JobState.PLANNED
    error: str | None = None
    # Active (unpaused) time is ``accum + (now - resumed_at)`` while running.
    accum: float = 0.0
    resumed_at: float = 0.0
    next_due: float = 0.0
    finished_at: datetime | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> float:
        return max((t.total(self.spec) for t in self.targets), default=0.0)

    def elapsed(self, now: float) -> float:
        if self.state is JobState.RUNNING:
            return self.accum + (now - self.resumed_at)
        return self.accum

    def tau(self, now: float) -> float:
        """Seconds into the ramp (negative while the start delay runs)."""
        return self.elapsed(now) - self.spec.start_delay_s

    def public_state(self, now: float) -> JobState:
        if self.state is JobState.RUNNING and self.tau(now) < 0:
            return JobState.SCHEDULED
        return self.state


# -- Manager -----------------------------------------------------------------


class SoftFailureManager:
    """Owns every soft-failure job and drives them against a ``TapiContext``."""

    def __init__(
        self,
        ctx: TapiContext,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ctx = ctx
        self.clock = clock
        self._jobs: dict[str, SoftFailureJob] = {}
        self._seq = 0
        self._wake: asyncio.Event | None = None

    # -- Creation ------------------------------------------------------------

    def plan(self, spec: SoftFailureSpec) -> SoftFailureJob:
        """Resolve and validate ``spec`` into a job without registering it.

        Raises:
            SoftFailureError: Bad path, no targets, or an end value outside
                the attribute's bounds under ``out_of_bounds="reject"``.
            SoftFailureConflict: A target is already driven by another
                active job.
        """
        try:
            path = parse(spec.path)
            refs = resolve(path, self._ctx)
        except ParameterPathError as exc:
            raise SoftFailureError(str(exc)) from exc
        if path.attribute_is_wildcard:
            raise SoftFailureError(
                "A soft failure drives one attribute; replace the trailing "
                "'*' in the path with an attribute name"
            )
        if not refs:
            raise SoftFailureError(f"Path {spec.path!r} matches no parameter")

        busy = {
            (t.ref.uid, t.ref.attribute): job.id
            for job in self._jobs.values() if job.state in _ACTIVE
            for t in job.targets
        }
        clashes = [
            f"{r.uid}.{r.attribute} (job {busy[(r.uid, r.attribute)]})"
            for r in refs if (r.uid, r.attribute) in busy
        ]
        if clashes:
            raise SoftFailureConflict(
                "Already driven by an active soft failure: " + ", ".join(clashes)
            )

        overrides = self._ctx.get_element_overrides()
        backend = self._ctx._backend
        targets: list[_Target] = []
        skipped: list[dict[str, Any]] = []
        out_of_range: list[str] = []
        for ref in refs:
            current = backend.read_attribute(ref.uid, ref.attribute)
            if current is None:
                skipped.append({
                    "uid": ref.uid, "attribute": ref.attribute,
                    "reason": "attribute is unset on this element",
                })
                continue
            start = float(current)
            end = self._end_value(spec, start)
            lo, hi = ref.spec.get("min"), ref.spec.get("max")
            if (lo is not None and end < lo) or (hi is not None and end > hi):
                if spec.out_of_bounds is OutOfBounds.CLAMP:
                    end = min(hi, end) if hi is not None else end
                    end = max(lo, end) if lo is not None else end
                else:
                    out_of_range.append(
                        f"{ref.uid}.{ref.attribute}: {start:g} → {end:g} "
                        f"outside [{lo}, {hi}] {ref.spec.get('unit', '')}".rstrip()
                    )
                    continue
            if spec.duration_s is not None:
                duration = spec.duration_s
            else:
                assert spec.rate is not None
                duration = abs(end - start) / spec.rate
            targets.append(_Target(
                ref=ref, start=start, end=end, duration=duration,
                had_override=ref.attribute in overrides.get(ref.uid, {}),
            ))
        if out_of_range:
            raise SoftFailureError(
                "End value out of range (set out_of_bounds='clamp' to stop at "
                "the bound instead): " + "; ".join(out_of_range)
            )
        if not targets:
            raise SoftFailureError(
                f"Path {spec.path!r} matched only parameters that cannot be "
                f"driven: {skipped!r}"
            )

        return SoftFailureJob(
            id="", spec=spec, path=path, targets=targets, skipped=skipped,
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _end_value(spec: SoftFailureSpec, start: float) -> float:
        if spec.to is not None:
            return spec.to
        sign = -1.0 if spec.direction is Direction.DECREASE else 1.0
        if spec.by is not None:
            return start + sign * spec.by
        assert spec.by_percent is not None
        return start * (1.0 + sign * spec.by_percent / 100.0)

    def create(self, spec: SoftFailureSpec) -> SoftFailureJob:
        """Plan, register and start a job. The first write happens at once."""
        job = self.plan(spec)
        self._seq += 1
        job.id = f"sf-{self._seq}"
        now = self.clock()
        job.state = JobState.RUNNING
        job.resumed_at = now
        job.next_due = now
        self._jobs[job.id] = job
        self._event(job, "created")
        self.tick(now)
        self._poke()
        return job

    # -- Control -------------------------------------------------------------

    def get(self, job_id: str) -> SoftFailureJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"No soft failure with id {job_id!r}") from None

    def list(self) -> list[SoftFailureJob]:
        return list(self._jobs.values())

    def pause(self, job_id: str) -> SoftFailureJob:
        job = self.get(job_id)
        if job.state is not JobState.RUNNING:
            raise SoftFailureConflict(f"{job_id} is {job.state.value}, not running")
        now = self.clock()
        self._advance(job, now)  # land the trajectory at the pause instant
        if job.state is JobState.RUNNING:
            job.accum = job.elapsed(now)
            job.state = JobState.PAUSED
            self._event(job, "paused")
        return job

    def resume(self, job_id: str) -> SoftFailureJob:
        job = self.get(job_id)
        if job.state is not JobState.PAUSED:
            raise SoftFailureConflict(f"{job_id} is {job.state.value}, not paused")
        now = self.clock()
        job.state = JobState.RUNNING
        job.resumed_at = now
        job.next_due = now
        self._event(job, "resumed")
        self.tick(now)
        self._poke()
        return job

    def stop(self, job_id: str) -> SoftFailureJob:
        """Freeze the job where it is; the parameters keep their values."""
        job = self.get(job_id)
        if job.state not in _ACTIVE:
            raise SoftFailureConflict(f"{job_id} is already {job.state.value}")
        now = self.clock()
        if job.state is JobState.RUNNING:
            self._advance(job, now)
        if job.state in _ACTIVE:
            job.accum = job.elapsed(now)
            self._finish(job, JobState.STOPPED, "stopped")
        return job

    def delete(self, job_id: str, revert: bool = False) -> SoftFailureJob:
        """Drop the job; with ``revert`` put every target back at its start."""
        job = self.get(job_id)
        if job.state in _ACTIVE:
            job.accum = job.elapsed(self.clock())
            self._finish(job, JobState.STOPPED, "stopped")
        if revert:
            self._write_start_values(job)
            self._event(job, "reverted")
        del self._jobs[job_id]
        return job

    def clear(self) -> None:
        """Forget every job without touching parameters (used by restore)."""
        self._jobs.clear()

    # -- Driving -------------------------------------------------------------

    def tick(self, now: float | None = None) -> float | None:
        """Write every due job's current values.

        Returns the seconds until the next job is due, or ``None`` when no
        job is running (the runner then sleeps until it is poked).
        """
        if now is None:
            now = self.clock()
        next_wake: float | None = None
        for job in list(self._jobs.values()):
            if job.state is not JobState.RUNNING:
                continue
            if now >= job.next_due:
                self._advance(job, now)
            if job.state is JobState.RUNNING:
                wait = max(0.0, job.next_due - now)
                next_wake = wait if next_wake is None else min(next_wake, wait)
        return next_wake

    def _advance(self, job: SoftFailureJob, now: float) -> None:
        tau = job.tau(now)
        interval = job.spec.interval_s
        if tau < 0:
            job.next_due = now + min(interval, -tau)
            return
        done = tau >= job.total
        updates: dict[str, dict[str, Any]] = {}
        for t in job.targets:
            value = t.value_at(min(tau, t.total(job.spec)), job.spec)
            if done and job.spec.on_complete is OnComplete.REVERT:
                value = t.start
            if value != t.commanded:
                updates.setdefault(t.ref.uid, {})[t.ref.attribute] = value
        if updates:
            try:
                self._ctx.apply_element_overrides(updates)
            except Exception as exc:  # noqa: BLE001 — job fails, runner lives
                logger.warning("Soft failure %s failed: %s", job.id, exc)
                job.error = str(exc)
                job.accum = job.elapsed(now)
                self._finish(job, JobState.FAILED, "failed")
                return
            for t in job.targets:
                attrs = updates.get(t.ref.uid, {})
                if t.ref.attribute in attrs:
                    t.commanded = attrs[t.ref.attribute]
        if done:
            job.accum = job.elapsed(now)
            if job.spec.on_complete is not OnComplete.HOLD:
                self._drop_new_overrides(job)
            self._finish(job, JobState.COMPLETED, "completed")
            return
        # Land the final write exactly on the end of the ramp rather than up
        # to one interval late.
        job.next_due = now + min(interval, job.total - tau)

    def _write_start_values(self, job: SoftFailureJob) -> None:
        updates: dict[str, dict[str, Any]] = {}
        for t in job.targets:
            updates.setdefault(t.ref.uid, {})[t.ref.attribute] = t.start
        self._ctx.apply_element_overrides(updates)
        for t in job.targets:
            t.commanded = t.start
        self._drop_new_overrides(job)

    def _drop_new_overrides(self, job: SoftFailureJob) -> None:
        """Back at the start value: forget overrides the job introduced."""
        for t in job.targets:
            if not t.had_override:
                self._ctx.forget_element_override(t.ref.uid, t.ref.attribute)

    def _finish(self, job: SoftFailureJob, state: JobState, event: str) -> None:
        job.state = state
        job.finished_at = datetime.now(UTC)
        self._event(job, event)

    @staticmethod
    def _event(job: SoftFailureJob, event: str) -> None:
        job.history.append({"event": event, "at": datetime.now(UTC).isoformat()})

    # -- Background runner ---------------------------------------------------

    def _poke(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def run(self) -> None:
        """Drive jobs until cancelled. Started by the app lifespan."""
        self._wake = asyncio.Event()
        try:
            while True:
                self._wake.clear()
                try:
                    delay = self.tick()
                except Exception:  # never let the runner die
                    logger.exception("Soft-failure tick failed")
                    delay = 1.0
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            self._wake = None

    # -- Serialisation -------------------------------------------------------

    def describe(self, job: SoftFailureJob, now: float | None = None) -> dict[str, Any]:
        if now is None:
            now = self.clock()
        tau = job.tau(now)
        total = job.total
        if job.state is JobState.COMPLETED:
            progress = 1.0
        elif total > 0:
            progress = min(1.0, max(0.0, tau / total))
        else:
            progress = 1.0 if tau >= 0 else 0.0
        if job.state is JobState.PLANNED:
            remaining = job.spec.start_delay_s + total
        elif job.state in _ACTIVE:
            remaining = max(0.0, total - max(0.0, tau))
        else:
            remaining = 0.0
        affected: set[str] = set()
        for t in job.targets:
            affected |= self._ctx.services_for_element(t.ref.uid)
        return {
            "id": job.id or None,
            "name": job.spec.name,
            "state": job.public_state(now).value,
            "error": job.error,
            "path": job.path.describe(),
            "spec": job.spec.model_dump(mode="json", exclude_none=True),
            "created_at": job.created_at.isoformat(),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "elapsed_s": max(0.0, tau),
            "duration_s": total,
            "remaining_s": remaining,
            "progress": progress,
            "targets": [
                {
                    "path": t.ref.path,
                    "uid": t.ref.uid,
                    "type": t.ref.kind,
                    "attribute": t.ref.attribute,
                    "unit": t.ref.spec.get("unit", ""),
                    "start": t.start,
                    "end": t.end,
                    "current": t.commanded,
                    "duration_s": t.duration,
                }
                for t in job.targets
            ],
            "skipped": job.skipped,
            "affected_services": sorted(affected),
            "history": job.history,
        }
