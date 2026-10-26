"""Drive soft failures on a running twin: gradual parameter drift.

A thin command-line front end to the twin's ``/config/soft-failures``
endpoints. The twin itself runs the ramps, so a scenario keeps going if this
script exits; ``--watch`` only follows progress.

Installed as the ``twinlight-soft-failure`` console command::

    # What would this path touch?
    twinlight-soft-failure params '/Link/roadm Brest/roadm Morlaix/*/*/*'

    # Raise one direction's span loss by 0.05 dB/km over ten minutes
    twinlight-soft-failure start '/Link/roadm Brest/roadm Morlaix/Fiber/*/loss_coef' \\
        --by 0.05 --duration 10m --profile saturating --watch

    # Pump degradation: amp gain down 3 dB at 0.5 dB/min, then recover
    twinlight-soft-failure start '/Edfa/east*/gain_target' --by 3 \\
        --direction decrease --rate 0.5/min --on-complete reverse

    # Several at once, from a file (see examples/soft_failure_scenario.yaml)
    twinlight-soft-failure run examples/soft_failure_scenario.yaml --watch

    twinlight-soft-failure list
    twinlight-soft-failure pause sf-1 | resume sf-1 | stop sf-1
    twinlight-soft-failure delete sf-1 --revert

Durations accept plain seconds or a ``h``/``m``/``s`` suffix (``90``,
``90s``, ``1h30m``); rates accept ``<units>/s``, ``/min`` or ``/h``.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

BASE = "/config/soft-failures"
_TERMINAL = {"completed", "stopped", "failed"}

_DURATION_RE = re.compile(
    r"^\s*(?:(?P<h>\d+(?:\.\d+)?)h)?\s*(?:(?P<m>\d+(?:\.\d+)?)m)?"
    r"\s*(?:(?P<s>\d+(?:\.\d+)?)s?)?\s*$"
)
_RATE_UNITS = {"s": 1.0, "sec": 1.0, "min": 60.0, "m": 60.0, "h": 3600.0, "hour": 3600.0}

# Scenario-file keys that take a human duration, mapped onto the API field.
_DURATION_KEYS = {
    "duration": "duration_s",
    "interval": "interval_s",
    "start_delay": "start_delay_s",
}


class ScenarioError(ValueError):
    """A command-line value or scenario file entry is malformed."""


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


def normalise_spec(entry: dict[str, Any]) -> dict[str, Any]:
    """Scenario/CLI form → API request body (human durations and rates)."""
    spec = dict(entry)
    for human, api in _DURATION_KEYS.items():
        if human in spec:
            if api in spec:
                raise ScenarioError(f"Give {human!r} or {api!r}, not both")
            spec[api] = parse_duration(spec.pop(human))
    if "rate" in spec:
        spec["rate"] = parse_rate(spec["rate"])
    return spec


def load_scenario(path: Path) -> list[dict[str, Any]]:
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
        specs.append(normalise_spec({**defaults, **entry}))
    return specs


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


# -- Output ------------------------------------------------------------------


def _fmt(v: Any) -> str:
    return f"{v:.6g}" if isinstance(v, (int, float)) else str(v)


def _print_job(job: dict[str, Any]) -> None:
    ident = job.get("id") or "(plan)"
    name = f" {job['name']!r}" if job.get("name") else ""
    print(
        f"{ident}{name}: {job['state']}  {job['progress']:6.1%}  "
        f"{job['elapsed_s']:.0f}/{job['duration_s']:.0f}s  {job['path']['canonical']}"
    )
    for t in job["targets"]:
        print(
            f"    {t['type']:<5} {t['uid']}.{t['attribute']}: "
            f"{_fmt(t['start'])} → {_fmt(t['end'])} {t['unit']}  "
            f"(now {_fmt(t['current'])})"
        )
    for s in job.get("skipped", []):
        print(f"    skipped {s['uid']}.{s['attribute']}: {s['reason']}")
    if job.get("affected_services"):
        print(f"    affects services: {', '.join(job['affected_services'])}")
    if job.get("error"):
        print(f"    error: {job['error']}")


def watch(
    client: httpx.Client, ids: list[str], poll_s: float = 2.0,
    sleep: Any = time.sleep,
) -> int:
    """Print progress until every job in ``ids`` reaches a terminal state."""
    while True:
        jobs = [_check(client.get(f"{BASE}/{i}")) for i in ids]
        for job in jobs:
            _print_job(job)
        if all(j["state"] in _TERMINAL for j in jobs):
            return 1 if any(j["state"] == "failed" for j in jobs) else 0
        print(flush=True)
        sleep(poll_s)


# -- Commands ----------------------------------------------------------------


def _start(client: httpx.Client, specs: list[dict[str, Any]], dry_run: bool) -> list[str]:
    ids: list[str] = []
    for spec in specs:
        job = _check(client.post(BASE, json=spec, params={"dry_run": dry_run}))
        _print_job(job)
        if job.get("id"):
            ids.append(job["id"])
    return ids


def _spec_from_args(args: argparse.Namespace) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": args.path}
    for key in ("name", "to", "by", "by_percent", "direction", "profile", "shape",
                "steps", "on_complete"):
        value = getattr(args, key)
        if value is not None:
            entry[key] = value
    for key in ("duration", "interval", "start_delay", "rate"):
        value = getattr(args, key)
        if value is not None:
            entry[key] = value
    if args.clamp:
        entry["out_of_bounds"] = "clamp"
    return normalise_spec(entry)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="twinlight-soft-failure",
        description="Start and control gradual parameter drift (soft failures) "
        "on a running TwinLight instance.",
    )
    parser.add_argument(
        "--rest-url", default="http://localhost:8080",
        help="TwinLight REST base URL (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("params", help="list the parameters a path resolves to")
    p.add_argument("path", nargs="?", default="/*/*/*")

    p = sub.add_parser("start", help="start one soft failure")
    p.add_argument("path", help="parameter path, e.g. /Fiber/*/loss_coef")
    change = p.add_mutually_exclusive_group(required=True)
    change.add_argument("--to", type=float, help="absolute end value")
    change.add_argument("--by", type=float, help="additive change")
    change.add_argument("--by-percent", type=float, help="relative change in %%")
    p.add_argument("--direction", choices=["increase", "decrease"],
                   help="treat --by/--by-percent as a magnitude in this direction")
    speed = p.add_mutually_exclusive_group(required=True)
    speed.add_argument("--duration", help="ramp duration, e.g. 10m")
    speed.add_argument("--rate", help="ramp speed in units per s/min/h, e.g. 0.5/min")
    p.add_argument("--profile",
                   choices=["linear", "accelerating", "saturating", "sigmoid", "step"])
    p.add_argument("--shape", type=float, help="steepness of non-linear profiles")
    p.add_argument("--steps", type=int, help="increments of the step profile")
    p.add_argument("--interval", help="time between writes (default 1s)")
    p.add_argument("--start-delay", help="wait before the ramp starts")
    p.add_argument("--on-complete", choices=["hold", "revert", "reverse"])
    p.add_argument("--clamp", action="store_true",
                   help="stop at the attribute's bound instead of refusing")
    p.add_argument("--name")
    p.add_argument("--dry-run", action="store_true", help="validate and show the plan")
    p.add_argument("--watch", action="store_true", help="follow progress")

    p = sub.add_parser("run", help="start every soft failure in a scenario file")
    p.add_argument("scenario", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--watch", action="store_true")

    sub.add_parser("list", help="list soft failures")
    for name in ("status", "pause", "resume", "stop"):
        sub.add_parser(name, help=f"{name} a soft failure").add_argument("id")
    p = sub.add_parser("delete", help="delete a soft failure")
    p.add_argument("id")
    p.add_argument("--revert", action="store_true",
                   help="write the start values back first")
    p = sub.add_parser("watch", help="follow soft failures until they finish")
    p.add_argument("ids", nargs="*", help="default: every active job")
    return parser


def run_command(args: argparse.Namespace, client: httpx.Client) -> int:
    cmd = args.command
    if cmd == "params":
        body = _check(client.get("/config/parameters", params={"path": args.path}))
        for prm in body["parameters"]:
            print(
                f"{prm['path']}  = {_fmt(prm['value'])} {prm['unit']}  "
                f"[{prm['min']}, {prm['max']}]"
            )
        return 0
    if cmd in ("start", "run"):
        specs = [_spec_from_args(args)] if cmd == "start" else load_scenario(args.scenario)
        ids = _start(client, specs, args.dry_run)
        if args.watch and ids:
            print(flush=True)
            return watch(client, ids)
        return 0
    if cmd == "list":
        for job in _check(client.get(BASE))["soft-failures"]:
            _print_job(job)
        return 0
    if cmd == "watch":
        ids = args.ids or [
            j["id"] for j in _check(client.get(BASE))["soft-failures"]
            if j["state"] not in _TERMINAL
        ]
        if not ids:
            print("No active soft failures.")
            return 0
        return watch(client, ids)
    if cmd == "status":
        _print_job(_check(client.get(f"{BASE}/{args.id}")))
        return 0
    if cmd in ("pause", "resume", "stop"):
        _print_job(_check(client.post(f"{BASE}/{args.id}/{cmd}")))
        return 0
    if cmd == "delete":
        _print_job(_check(client.delete(
            f"{BASE}/{args.id}", params={"revert": args.revert},
        )))
        return 0
    raise AssertionError(f"unhandled command {cmd!r}")


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
    except KeyboardInterrupt:
        # The twin keeps running the jobs; only the watch stops.
        print("\nStopped watching (soft failures keep running on the twin).")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
