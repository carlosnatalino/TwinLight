"""Immediate, path-addressed parameter changes for the ``/config`` plane.

Two operations, both resolved through parameter paths
(``state/parameter_path.py``) and both all-or-nothing:

* **set** — ``{path: value}``: every parameter a path matches takes that
  absolute value (``POST /config/parameters``);
* **changes** — a list of ``{path, to | by | by_percent, …}``: every
  parameter moves to an absolute value or by an additive or relative amount
  from its *own* current value (``POST /config/parameters/changes``).

The twin applies a change the moment it is asked to and keeps no clock: a
gradual drift (a soft failure) is a client issuing a sequence of these
calls — see ``twinlight_client/soft_failure.py``. A dry run of a ``changes``
batch returns every target's current and resulting value, which is exactly
the plan such a client needs before it starts ramping.

Every batch is validated in full before anything is written, and an error
names every offending parameter rather than the first one. The write itself
goes through ``TapiContext.apply_element_overrides_atomic``, so baselines are
invalidated as for ``/config/set``.
"""

from __future__ import annotations

from dataclasses import dataclass
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


class ParameterOpError(ValueError):
    """The batch is invalid against the live twin; nothing was written."""


class Direction(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"


class OutOfBounds(str, Enum):
    REJECT = "reject"
    CLAMP = "clamp"


class ParameterChange(BaseModel):
    """One entry of a ``POST /config/parameters/changes`` body."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Parameter path, e.g. /Fiber/*/loss_coef.")
    to: float | None = Field(
        default=None, description="Absolute value, in attribute units.",
    )
    by: float | None = Field(
        default=None,
        description="Additive change from each target's current value. "
        "Signed, unless 'direction' is given.",
    )
    by_percent: float | None = Field(
        default=None,
        description="Relative change from each target's current value, in %. "
        "Signed, unless 'direction' is given.",
    )
    direction: Direction | None = Field(
        default=None,
        description="Apply 'by'/'by_percent' as a magnitude in this direction.",
    )
    out_of_bounds: OutOfBounds = Field(
        default=OutOfBounds.REJECT,
        description="'reject' refuses a result outside the attribute's allowed "
        "range; 'clamp' stops at the bound.",
    )

    @field_validator("to", "by", "by_percent", mode="before")
    @classmethod
    def _no_bools(cls, v: Any) -> Any:
        # bool is an int subclass; ``true`` must not silently become 1.0.
        # ValueError, not TypeError: pydantic turns only ValueError into a
        # validation error (and so a 422) rather than a 500.
        if isinstance(v, bool):
            raise ValueError("expected a number, got bool")  # noqa: TRY004
        return v

    @model_validator(mode="after")
    def _one_change(self) -> ParameterChange:
        given = [f for f in ("to", "by", "by_percent") if getattr(self, f) is not None]
        if len(given) != 1:
            raise ValueError(
                f"give exactly one of 'to', 'by', 'by_percent' (got {given or 'none'})"
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

    def target_value(self, current: float) -> float:
        """The value this change moves a parameter at ``current`` to."""
        if self.to is not None:
            return self.to
        sign = -1.0 if self.direction is Direction.DECREASE else 1.0
        if self.by is not None:
            return current + sign * self.by
        assert self.by_percent is not None
        return current * (1.0 + sign * self.by_percent / 100.0)


@dataclass(frozen=True)
class Assignment:
    """One parameter and the value a batch gives it."""

    ref: ParameterRef
    previous: float | None
    value: float

    def describe(self) -> dict[str, Any]:
        return {
            "path": self.ref.path,
            "uid": self.ref.uid,
            "type": self.ref.kind,
            "attribute": self.ref.attribute,
            "unit": self.ref.spec.get("unit", ""),
            "min": self.ref.spec.get("min"),
            "max": self.ref.spec.get("max"),
            "previous": self.previous,
            "value": self.value,
        }


@dataclass(frozen=True)
class ChangePlan:
    """What one ``ParameterChange`` resolved to."""

    change: ParameterChange
    path: ParameterPath
    assignments: list[Assignment]
    skipped: list[dict[str, Any]]

    def describe(self) -> dict[str, Any]:
        return {
            "path": self.path.describe(),
            "change": self.change.model_dump(mode="json", exclude_none=True),
            "parameters": [a.describe() for a in self.assignments],
            "skipped": self.skipped,
        }


def _resolve_one_attribute(text: str, ctx: TapiContext) -> tuple[ParameterPath, list[ParameterRef]]:
    path = parse(text)
    if path.attribute_is_wildcard:
        raise ParameterPathError(
            f"{text!r}: a change needs one attribute, not '*'"
        )
    refs = resolve(path, ctx)
    if not refs:
        raise ParameterPathError(f"{text!r} matches no parameter")
    return path, refs


def _bounds_problem(ref: ParameterRef, value: float) -> str | None:
    lo, hi = ref.spec.get("min"), ref.spec.get("max")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        unit = ref.spec.get("unit", "")
        return f"{ref.uid}.{ref.attribute}={value:g} outside [{lo}, {hi}] {unit}".rstrip()
    return None


def _clamp(ref: ParameterRef, value: float) -> float:
    lo, hi = ref.spec.get("min"), ref.spec.get("max")
    if hi is not None:
        value = min(hi, value)
    if lo is not None:
        value = max(lo, value)
    return value


def _read(ctx: TapiContext, ref: ParameterRef) -> float | None:
    current = ctx._backend.read_attribute(ref.uid, ref.attribute)
    return None if current is None else float(current)


def _claim(
    claimed: dict[tuple[str, str], str], ref: ParameterRef, owner: str,
    errors: list[str],
) -> None:
    """Record that ``owner`` writes ``ref``; a second owner is an error.

    Two entries of one batch writing the same parameter would make the
    result depend on their order, so the batch is refused instead.
    """
    key = (ref.uid, ref.attribute)
    if key in claimed and claimed[key] != owner:
        errors.append(
            f"{ref.uid}.{ref.attribute} is targeted by both "
            f"{claimed[key]} and {owner}"
        )
    claimed[key] = owner


def plan_set(ctx: TapiContext, values: dict[str, Any]) -> list[Assignment]:
    """Resolve ``{path: value}`` without writing. Raises ``ParameterOpError``."""
    if not values:
        raise ParameterOpError("'set' is empty")
    errors: list[str] = []
    claimed: dict[tuple[str, str], str] = {}
    out: list[Assignment] = []
    for text, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{text!r}: value must be a number, got {value!r}")
            continue
        try:
            _path, refs = _resolve_one_attribute(text, ctx)
        except ParameterPathError as exc:
            errors.append(str(exc))
            continue
        for ref in refs:
            _claim(claimed, ref, repr(text), errors)
            problem = _bounds_problem(ref, float(value))
            if problem:
                errors.append(problem)
                continue
            out.append(Assignment(ref, _read(ctx, ref), float(value)))
    if errors:
        raise ParameterOpError("; ".join(errors))
    return out


def plan_changes(
    ctx: TapiContext, changes: list[ParameterChange],
) -> list[ChangePlan]:
    """Resolve a list of changes without writing. Raises ``ParameterOpError``.

    Every change is computed from the values *before* the batch, so the
    order of the list never matters.
    """
    if not changes:
        raise ParameterOpError("the list of changes is empty")
    errors: list[str] = []
    claimed: dict[tuple[str, str], str] = {}
    plans: list[ChangePlan] = []
    for i, change in enumerate(changes):
        owner = f"change #{i} ({change.path!r})"
        try:
            path, refs = _resolve_one_attribute(change.path, ctx)
        except ParameterPathError as exc:
            errors.append(f"change #{i}: {exc}")
            continue
        assignments: list[Assignment] = []
        skipped: list[dict[str, Any]] = []
        for ref in refs:
            _claim(claimed, ref, owner, errors)
            current = _read(ctx, ref)
            if current is None and change.to is None:
                # A relative change of an unset attribute (e.g. the tilt of
                # an amplifier GNPy loaded without one) is undefined.
                skipped.append({
                    "path": ref.path,
                    "reason": "attribute is unset, so a relative change is undefined",
                })
                continue
            value = change.target_value(current if current is not None else 0.0)
            if change.out_of_bounds is OutOfBounds.CLAMP:
                value = _clamp(ref, value)
            else:
                problem = _bounds_problem(ref, value)
                if problem:
                    errors.append(
                        f"change #{i}: {problem} (out_of_bounds='clamp' stops "
                        f"at the bound instead)"
                    )
                    continue
            assignments.append(Assignment(ref, current, round(value, 9)))
        if not assignments and not any(e.startswith(f"change #{i}:") for e in errors):
            errors.append(f"change #{i}: no parameter it matches can be changed")
        plans.append(ChangePlan(change, path, assignments, skipped))
    if errors:
        raise ParameterOpError("; ".join(errors))
    return plans


def apply(ctx: TapiContext, assignments: list[Assignment]) -> set[str]:
    """Write a validated batch atomically; return the invalidated services."""
    updates: dict[str, dict[str, Any]] = {}
    for a in assignments:
        updates.setdefault(a.ref.uid, {})[a.ref.attribute] = a.value
    invalidated = ctx.apply_element_overrides_atomic(updates)
    return set().union(*invalidated.values()) if invalidated else set()
