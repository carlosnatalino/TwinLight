"""Soft failures: gradual parameter drift, driven from outside the twin.

A *hard* failure is a step — a fiber cut. A *soft* failure is a slow
degradation that leaves the lightpath up while its margin erodes: a span's
loss creeping up as a connector contaminates, an amplifier's gain sagging as
its pump ages. Detecting and localising those before they become outages is
the point of a twin, so it helps to be able to produce them on demand.

The twin has no clock and no notion of a ramp: it applies concrete values
the moment it is told to. Everything time-dependent lives here. This script

1. plans the whole scenario in one ``POST /config/parameters/changes``
   dry run, which resolves every parameter path, checks every end value
   against the attribute's range, and returns each target's start and end
   value — so a bad entry is refused before anything moves;
2. then, on each tick, computes every due ramp's current value and sends
   them all in one ``POST /config/parameters`` call (``{"set": {...}}``),
   and sends entries without a duration — instant steps — through
   ``POST /config/parameters/changes`` when their start delay is reached.

Every write invalidates the affected services' cached baselines on the twin,
so OPM, gNMI and Prometheus follow the drift.

Installed as the ``twinlight-soft-failure`` console command::

    # What would this path touch?
    twinlight-soft-failure params '/Link/Site_A/Site_B/*/*/*'

    # Immediate changes (no ramp): absolute values, or relative changes
    twinlight-soft-failure set '/Fiber/*/loss_coef=0.22' '/Edfa/Edfa1/tilt_target=0.5'
    twinlight-soft-failure change '/Edfa/*/gain_target' --by 1 --direction decrease

    # Raise the span loss by 0.05 dB/km over ten minutes
    twinlight-soft-failure ramp '/Fiber/Span1/loss_coef' --by 0.05 --duration 10m \\
        --profile saturating

    # Several ramps and steps on one timeline (examples/soft_failure_scenario.yaml)
    twinlight-soft-failure run examples/soft_failure_scenario.yaml --dry-run
    twinlight-soft-failure run examples/soft_failure_scenario.yaml --revert-on-interrupt

Durations accept plain seconds or a ``h``/``m``/``s`` suffix (``90``,
``90s``, ``1h30m``); rates accept ``<units>/s``, ``/min`` or ``/h``. Stopping
a run with Ctrl+C leaves the parameters where they are, unless
``--revert-on-interrupt`` is given.

The profiles are scenario shapes, not physical models of a particular
degradation mechanism. Trajectories are pure functions of elapsed time, so
the value written at a given instant does not depend on when the loop
happened to wake.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from twinlight.state.parameter_ops import ParameterChange

SET_URL = "/config/parameters"
CHANGES_URL = "/config/parameters/changes"

_CHANGE_FIELDS = set(ParameterChange.model_fields)


class ScenarioError(ValueError):
    """A command-line value, scenario entry, or twin response is unusable."""


# -- Units -------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"^\s*(?:(?P<h>\d+(?:\.\d+)?)h)?\s*(?:(?P<m>\d+(?:\.\d+)?)m)?"
    r"\s*(?:(?P<s>\d+(?:\.\d+)?)s?)?\s*$"
)
_RATE_UNITS = {"s": 1.0, "sec": 1.0, "min": 60.0, "m": 60.0, "h": 3600.0, "hour": 3600.0}

# Keys that make an entry a ramp; without any of them it is an instant step.
_SPEED_KEYS = {"duration", "duration_s", "rate"}

# Scenario-file keys that take a human duration, mapped onto the spec field.
_DURATION_KEYS = {
    "duration": "duration_s",
    "interval": "interval_s",
    "start_delay": "start_delay_s",
}


def parse_duration(value: str | float) -> float:
    """``"1h30m"`` → 5400.0; numbers pass through as seconds."""
    if isinstance(value, bool):
        raise ScenarioError(f"Not a duration: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION_RE.match(value)
    if not m or not any(m.groupdict().values()):
        raise ScenarioError(f"Not a duration: {value!r} (try 90, 90s, 10m, 1h30m)")
    h, mins, s = (float(m.group(k) or 0) for k in ("h", "m", "s"))
    return h * 3600 + mins * 60 + s


def parse_rate(value: str | float) -> float:
    """``"0.5/min"`` → 0.008333… units per second; numbers are per second."""
    if isinstance(value, bool):
        raise ScenarioError(f"Not a rate: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    amount, _, unit = value.partition("/")
    try:
        number = float(amount)
    except ValueError:
        raise ScenarioError(f"Not a rate: {value!r} (try 0.01/s, 0.5/min)") from None
    per = _RATE_UNITS.get(unit.strip() or "s")
    if per is None:
        raise ScenarioError(f"Unknown rate unit in {value!r}: use /s, /min or /h")
    return number / per


# -- Specification -----------------------------------------------------------


class Profile(str, Enum):
    LINEAR = "linear"
    ACCELERATING = "accelerating"
    SATURATING = "saturating"
    SIGMOID = "sigmoid"
    STEP = "step"


class OnComplete(str, Enum):
    HOLD = "hold"
    REVERT = "revert"
    REVERSE = "reverse"


class SoftFailureSpec(ParameterChange):
    """One scenario entry: a twin-side change plus how to get there over time.

    The inherited fields (``path``, ``to``/``by``/``by_percent``,
    ``direction``, ``out_of_bounds``) are sent to the twin as-is and define
    where each parameter ends up. The fields below are this script's alone.
    Without ``duration_s`` or ``rate`` the entry is an instant step.
    """

    name: str | None = None
    duration_s: float | None = Field(default=None, gt=0)
    rate: float | None = Field(
        default=None, gt=0,
        description="Average speed in attribute units per second; each "
        "target's duration is |end - start| / rate.",
    )
    profile: Profile = Profile.LINEAR
    shape: float = Field(default=5.0, gt=0)
    steps: int = Field(default=5, ge=1, le=10_000)
    interval_s: float = Field(default=1.0, ge=0.05)
    start_delay_s: float = Field(default=0.0, ge=0)
    on_complete: OnComplete = OnComplete.HOLD

    @field_validator("duration_s", "rate", "shape", "interval_s", "start_delay_s",
                     mode="before")
    @classmethod
    def _timing_no_bools(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("expected a number, got bool")  # noqa: TRY004
        return v

    @model_validator(mode="after")
    def _timing(self) -> SoftFailureSpec:
        if self.duration_s is not None and self.rate is not None:
            raise ValueError("give 'duration_s' or 'rate', not both")
        if self.is_step and self.on_complete is not OnComplete.HOLD:
            raise ValueError(
                "'on_complete' needs a ramp: give 'duration_s' or 'rate'"
            )
        return self

    @property
    def is_step(self) -> bool:
        return self.duration_s is None and self.rate is None

    @property
    def label(self) -> str:
        return self.name or self.path

    def change(self) -> dict[str, Any]:
        """The part of the entry the twin understands."""
        return self.model_dump(mode="json", include=_CHANGE_FIELDS, exclude_none=True)


def normalise_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Scenario/CLI form (human durations and rates) → spec fields."""
    spec = dict(entry)
    for human, key in _DURATION_KEYS.items():
        if human in spec:
            if key in spec:
                raise ScenarioError(f"Give {human!r} or {key!r}, not both")
            spec[key] = parse_duration(spec.pop(human))
    if "rate" in spec:
        spec["rate"] = parse_rate(spec["rate"])
    return spec


def build_spec(entry: dict[str, Any], where: str = "") -> SoftFailureSpec:
    try:
        return SoftFailureSpec.model_validate(normalise_entry(entry))
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'entry'}: {e['msg']}"
            for e in exc.errors()
        )
        raise ScenarioError(f"{where}{details}") from None


def load_scenario(path: Path) -> list[SoftFailureSpec]:
    """Read a scenario file: ``defaults`` merged under each entry."""
    doc = yaml.safe_load(path.read_text()) or {}
    if not isinstance(doc, dict) or not isinstance(doc.get("soft_failures"), list):
        raise ScenarioError(f"{path}: expected a top-level 'soft_failures' list")
    defaults = doc.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ScenarioError(f"{path}: 'defaults' must be a mapping")
    specs = []
    for i, entry in enumerate(doc["soft_failures"]):
        if not isinstance(entry, dict):
            raise ScenarioError(f"{path}: soft_failures[{i}] must be a mapping")
        merged = {**defaults, **entry}
        if _SPEED_KEYS & entry.keys():
            # An entry's own speed replaces the default one rather than
            # clashing with it (a default duration plus an entry's rate).
            for key in _SPEED_KEYS - entry.keys():
                merged.pop(key, None)
        specs.append(build_spec(merged, f"{path}: soft_failures[{i}]: "))
    return specs


# -- Trajectories ------------------------------------------------------------


def shape_fraction(profile: Profile, x: float, shape: float, steps: int) -> float:
    """Map normalised time ``x`` ∈ [0, 1] to normalised progress ∈ [0, 1].

    Every profile satisfies f(0) = 0 and f(1) = 1 and is monotone, so the
    trajectory never overshoots, and the twin's range check on the end value
    (made in the planning dry run) bounds every intermediate write too.
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


@dataclass
class Target:
    """One parameter a ramp drives, as planned by the twin."""

    path: str
    label: str
    unit: str
    start: float
    end: float
    duration: float
    commanded: float = field(init=False)

    def __post_init__(self) -> None:
        self.commanded = self.start

    def value_at(self, tau: float, spec: SoftFailureSpec) -> float:
        """The value ``tau`` seconds into the ramp."""
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
        if spec.on_complete is OnComplete.REVERT and tau >= d:
            frac = 0.0
        # Rounded so float noise (0.22000000000000003) does not leak into
        # the twin's override record; 1e-9 is far below any meaningful step.
        return round(self.start + (self.end - self.start) * frac, 9)

    def total(self, spec: SoftFailureSpec) -> float:
        return self.duration * (2 if spec.on_complete is OnComplete.REVERSE else 1)


@dataclass
class Entry:
    spec: SoftFailureSpec
    targets: list[Target]
    skipped: list[dict[str, Any]]
    due: float = 0.0
    done: bool = False

    @property
    def total(self) -> float:
        return max((t.total(self.spec) for t in self.targets), default=0.0)


# -- HTTP --------------------------------------------------------------------


def _check(r: httpx.Response) -> Any:
    if r.is_success:
        return r.json()
    try:
        errors = r.json()["ietf-restconf:errors"]["error"]
        msg = "; ".join(e.get("error-message", "") for e in errors)
    except (ValueError, KeyError, TypeError):
        msg = r.text
    raise ScenarioError(f"HTTP {r.status_code}: {msg}")


def post_set(client: httpx.Client, values: dict[str, float], dry_run: bool = False) -> Any:
    return _check(client.post(SET_URL, json={"set": values}, params={"dry_run": dry_run}))


def post_changes(
    client: httpx.Client, changes: list[dict[str, Any]], dry_run: bool = False,
) -> Any:
    return _check(client.post(CHANGES_URL, json=changes, params={"dry_run": dry_run}))


# -- Runner ------------------------------------------------------------------


def _fmt(v: Any) -> str:
    return f"{v:.6g}" if isinstance(v, (int, float)) else str(v)


class SoftFailureRunner:
    """Drives a list of soft failures against a twin on one timeline.

    ``clock`` and ``sleep`` are injectable so tests run a ten-minute
    scenario in microseconds.
    """

    def __init__(
        self,
        client: httpx.Client,
        specs: list[SoftFailureSpec],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        out: Callable[[str], None] = print,
        report_s: float = 10.0,
    ) -> None:
        if not specs:
            raise ScenarioError("nothing to run")
        self.client = client
        self.specs = specs
        self.clock = clock
        self.sleep = sleep
        self.out = out
        self.report_s = report_s
        self.entries: list[Entry] = []

    def plan(self) -> list[Entry]:
        """Validate the whole scenario on the twin in one dry run."""
        body = post_changes(self.client, [s.change() for s in self.specs], dry_run=True)
        self.entries = []
        for spec, plan in zip(self.specs, body["changes"], strict=True):
            targets: list[Target] = []
            for p in plan["parameters"]:
                start = p["previous"] if p["previous"] is not None else p["value"]
                end = p["value"]
                if spec.duration_s is not None:
                    duration = spec.duration_s
                elif spec.rate is not None:
                    duration = abs(end - start) / spec.rate
                else:
                    duration = 0.0
                targets.append(Target(
                    path=p["path"], label=f"{p['uid']}.{p['attribute']}",
                    unit=p["unit"], start=float(start), end=float(end),
                    duration=duration,
                ))
            self.entries.append(Entry(spec, targets, plan["skipped"]))
        return self.entries

    def describe_plan(self) -> None:
        for e in self.entries:
            s = e.spec
            if s.is_step:
                when = "step"
            else:
                when = f"{s.profile.value} over {e.total:g}s"
                if s.on_complete is not OnComplete.HOLD:
                    when += f", then {s.on_complete.value}"
            if s.start_delay_s:
                when += f", at +{s.start_delay_s:g}s"
            self.out(f"{s.label}: {when}")
            for t in e.targets:
                self.out(f"    {t.label}: {_fmt(t.start)} → {_fmt(t.end)} {t.unit}".rstrip())
            for sk in e.skipped:
                self.out(f"    skipped {sk['path']}: {sk['reason']}")

    def run(self) -> None:
        """Run every entry to completion. Plans first if not yet planned."""
        if not self.entries:
            self.plan()
        t0 = self.clock()
        for e in self.entries:
            e.due = t0 + e.spec.start_delay_s
        next_report = t0
        while not all(e.done for e in self.entries):
            now = self.clock()
            self._tick(now, t0)
            if now >= next_report or all(e.done for e in self.entries):
                self._report(now - t0)
                next_report = now + self.report_s
            pending = [e.due for e in self.entries if not e.done]
            if pending:
                self.sleep(max(0.0, min(pending) - self.clock()))

    def _tick(self, now: float, t0: float) -> None:
        values: dict[str, float] = {}
        steps: list[Entry] = []
        writes: list[tuple[Target, float]] = []
        for e in self.entries:
            if e.done or now < e.due:
                continue
            if e.spec.is_step:
                steps.append(e)
                continue
            tau = now - t0 - e.spec.start_delay_s
            for t in e.targets:
                v = t.value_at(min(tau, t.total(e.spec)), e.spec)
                if v != t.commanded:
                    values[t.path] = v
                    writes.append((t, v))
            if tau >= e.total:
                e.done = True
            else:
                # Land the last write on the end of the ramp, not up to one
                # interval after it.
                e.due = now + min(e.spec.interval_s, e.total - tau)
        if steps:
            # Steps go through /changes so a relative step is taken from the
            # value at that moment, not from the one planned at the start.
            body = post_changes(self.client, [e.spec.change() for e in steps])
            for e, plan in zip(steps, body["changes"], strict=True):
                e.done = True
                for p in plan["parameters"]:
                    self.out(
                        f"step {e.spec.label}: {p['uid']}.{p['attribute']} "
                        f"{_fmt(p['previous'])} → {_fmt(p['value'])} {p['unit']}".rstrip()
                    )
        if values:
            post_set(self.client, values)
            for t, v in writes:
                t.commanded = v

    def _report(self, elapsed: float) -> None:
        parts = []
        for e in self.entries:
            if e.spec.is_step:
                continue
            tau = elapsed - e.spec.start_delay_s
            if e.done:
                state = "done"
            elif tau < 0:
                state = f"starts in {-tau:.0f}s"
            else:
                state = f"{min(1.0, tau / e.total) if e.total else 1.0:.0%}"
            now_vals = ", ".join(f"{t.label}={_fmt(t.commanded)}" for t in e.targets)
            parts.append(f"{e.spec.label} [{state}] {now_vals}")
        if parts:
            self.out(f"[{elapsed:7.1f}s] " + " | ".join(parts))

    def revert(self) -> None:
        """Put every ramped parameter back at its start value, in one call."""
        values = {
            t.path: t.start for e in self.entries for t in e.targets
            if not e.spec.is_step and t.commanded != t.start
        }
        if values:
            post_set(self.client, values)
            for e in self.entries:
                for t in e.targets:
                    if t.path in values:
                        t.commanded = t.start
        self.out(f"Reverted {len(values)} parameter(s) to their start values.")


# -- Command line ------------------------------------------------------------


def _add_change_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="parameter path, e.g. /Fiber/*/loss_coef")
    change = p.add_mutually_exclusive_group(required=True)
    change.add_argument("--to", type=float, help="absolute value")
    change.add_argument("--by", type=float, help="additive change")
    change.add_argument("--by-percent", type=float, help="relative change in %%")
    p.add_argument("--direction", choices=["increase", "decrease"],
                   help="treat --by/--by-percent as a magnitude in this direction")
    p.add_argument("--clamp", action="store_true",
                   help="stop at the attribute's bound instead of refusing")
    p.add_argument("--dry-run", action="store_true",
                   help="validate and show the plan without changing anything")


def _add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--revert-on-interrupt", action="store_true",
                   help="on Ctrl+C, put ramped parameters back at their start values")
    p.add_argument("--report", default="10s",
                   help="progress report interval (default %(default)s)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="twinlight-soft-failure",
        description="Change TwinLight parameters immediately, or drift them "
        "gradually (soft failures) on a timeline this script keeps.",
    )
    parser.add_argument(
        "--rest-url", default="http://localhost:8080",
        help="TwinLight REST base URL (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("params", help="list the parameters a path resolves to")
    p.add_argument("path", nargs="?", default="/*/*/*")

    p = sub.add_parser("set", help="set absolute values now (POST /config/parameters)")
    p.add_argument("assignments", nargs="+", metavar="PATH=VALUE")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser(
        "change", help="apply one change now (POST /config/parameters/changes)",
    )
    _add_change_args(p)

    p = sub.add_parser("ramp", help="drift one parameter path gradually")
    _add_change_args(p)
    speed = p.add_mutually_exclusive_group(required=True)
    speed.add_argument("--duration", help="ramp duration, e.g. 10m")
    speed.add_argument("--rate", help="ramp speed in units per s/min/h, e.g. 0.5/min")
    p.add_argument("--profile", choices=[x.value for x in Profile])
    p.add_argument("--shape", type=float, help="steepness of non-linear profiles")
    p.add_argument("--steps", type=int, help="increments of the step profile")
    p.add_argument("--interval", help="time between writes (default 1s)")
    p.add_argument("--start-delay", help="wait before the ramp starts")
    p.add_argument("--on-complete", choices=[x.value for x in OnComplete])
    p.add_argument("--name")
    _add_run_args(p)

    p = sub.add_parser("run", help="run every entry of a scenario file")
    p.add_argument("scenario", type=Path)
    p.add_argument("--dry-run", action="store_true")
    _add_run_args(p)
    return parser


def _entry_from_args(args: argparse.Namespace) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": args.path}
    keys = ("to", "by", "by_percent", "direction", "name", "profile", "shape", "steps",
            "on_complete", "duration", "rate", "interval", "start_delay")
    for key in keys:
        value = getattr(args, key, None)
        if value is not None:
            entry[key] = value
    if args.clamp:
        entry["out_of_bounds"] = "clamp"
    return entry


def _parse_assignment(text: str) -> tuple[str, float]:
    path, sep, value = text.rpartition("=")
    if not sep or not path:
        raise ScenarioError(f"Expected PATH=VALUE, got {text!r}")
    try:
        return path, float(value)
    except ValueError:
        raise ScenarioError(f"Not a number in {text!r}") from None


def _print_parameters(params: list[dict[str, Any]], out: Callable[[str], None]) -> None:
    for p in params:
        out(f"{p['path']}  {_fmt(p['previous'])} → {_fmt(p['value'])} {p['unit']}".rstrip())


def run_command(
    args: argparse.Namespace,
    client: httpx.Client,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    out = print
    cmd = args.command
    if cmd == "params":
        body = _check(client.get(SET_URL, params={"path": args.path}))
        for prm in body["parameters"]:
            out(f"{prm['path']}  = {_fmt(prm['value'])} {prm['unit']}  "
                f"[{prm['min']}, {prm['max']}]")
        return 0
    if cmd == "set":
        values = dict(_parse_assignment(a) for a in args.assignments)
        body = post_set(client, values, dry_run=args.dry_run)
        _print_parameters(body["parameters"], out)
        return 0
    if cmd == "change":
        spec = build_spec(_entry_from_args(args))
        body = post_changes(client, [spec.change()], dry_run=args.dry_run)
        _print_parameters(body["changes"][0]["parameters"], out)
        return 0

    specs = (
        [build_spec(_entry_from_args(args))] if cmd == "ramp"
        else load_scenario(args.scenario)
    )
    runner = SoftFailureRunner(
        client, specs, clock=clock, sleep=sleep, out=out,
        report_s=parse_duration(args.report),
    )
    runner.plan()
    runner.describe_plan()
    if args.dry_run:
        return 0
    try:
        runner.run()
    except KeyboardInterrupt:
        out("\nInterrupted.")
        if args.revert_on_interrupt:
            runner.revert()
        else:
            out("Parameters keep the values they reached.")
        return 130
    out("Done.")
    return 0


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if client is not None:
            return run_command(args, client)
        with httpx.Client(base_url=args.rest_url, timeout=30.0) as http:
            return run_command(args, http)
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"error: cannot reach {args.rest_url}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
