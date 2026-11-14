"""Interactive CLI: follow one service's measurements on the T-API stream.

Flow (TR-548 ST-0.2, ST-6.2, ST-6.4):
  1. List the connectivity services over RESTCONF and let the user pick one.
  2. Discover the measurement stream from tapi-streaming:stream-context and
     find the oam-job that measures the chosen service.
  3. Subscribe over gNMI, decode each tapi.MeasurementDetails record of that
     job, and print it. After a dropped connection, resubscribe from the last
     record's timestamp so no sample is lost.

The values are the twin's live OPM, transient fluctuations included (EDFA
reservoir, polarization/PDL, phase noise, environmental) — the same values
GET /internal/opm reports at the same instant.

Installed as the ``twinlight-client`` console command (see pyproject.toml):

    twinlight-client
    twinlight-client --rest-url URL [--gnmi-target HOST:PORT] [--token TOKEN]

Equivalently runnable as a module: ``python -m twinlight_client``.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import os
import sys
from typing import Any

from twinlight_client.client import TapiClient
from twinlight_client.streaming import (
    TapiStreamClient,
    discover_stream,
    oam_job_for_service,
)

TOKEN_ENV = "TWINLIGHT_STREAM_TOKEN"

# (measurement key, column header, formatter kind) — display order.
# kind: "f" → fixed-point with 3 decimals, "e" → scientific with 2 decimals.
_METRICS: list[tuple[str, str, str]] = [
    ("osnr-db", "OSNR(dB)", "f"),
    ("gsnr-db", "GSNR(dB)", "f"),
    ("q-factor-db", "Q(dB)", "f"),
    ("pre-fec-ber", "preFEC-BER", "e"),
    ("post-fec-ber-threshold", "postFEC-thr", "e"),
    ("post-fec-ber-hd", "postFEC-HD", "e"),
    # GMI itself is left out to keep the row narrow: for one service it is
    # NGMI times the format's bits per symbol.
    ("ngmi", "NGMI", "f"),
    ("chromatic-dispersion-ps-per-nm", "CD(ps/nm)", "f"),
    ("pmd-ps", "PMD(ps)", "f"),
]

_NUM_W = 5    # width of the sample-number column
_TIME_W = 10  # width of the timestamp column
_COL_W = 12   # width of each metric column


def _service_name(svc: dict[str, Any]) -> str:
    """Extract the human-readable service name from the T-API name list."""
    for entry in svc.get("name", []):
        if entry.get("value-name") == "service-name":
            return entry.get("value", "(unnamed)")
    return "(unnamed)"


def _service_modulation(svc: dict[str, Any]) -> str:
    """Extract the modulation format from a connectivity-service.

    T-API v2.6.0 carries it as a ``tapi-photonic-media`` augment on the
    end-point rather than a field on the service, so it takes some digging.
    """
    from twinlight.models.connectivity import OTSIA_CSEP_SPEC
    from twinlight.physics.modulation import MT_TO_MODULATION

    for end_point in svc.get("end-point", []):
        for constraint in end_point.get("layer-protocol-constraint", []):
            spec = constraint.get(OTSIA_CSEP_SPEC) or {}
            for cfg in spec.get("otsi-config", []):
                identity = cfg.get("modulation", {}).get(
                    "standard-modulation-technique", ""
                )
                fmt = MT_TO_MODULATION.get(identity.split(":")[-1])
                if fmt is not None:
                    return fmt.value
    return "?"


def _header_line() -> str:
    """Build the one-time header row, aligned to the value columns."""
    cols = [f"{'#':>{_NUM_W}}", f"{'time':>{_TIME_W}}"]
    cols += [f"{header:>{_COL_W}}" for _, header, _ in _METRICS]
    return "  ".join(cols)


def _value_line(count: int, when: str, measurements: dict[str, Any]) -> str:
    """Build a values-only row, aligned under _header_line()."""
    cols = [f"{f'#{count:03d}':>{_NUM_W}}", f"{when:>{_TIME_W}}"]
    for key, _, kind in _METRICS:
        value = measurements.get(key)
        # None is a MISSING measure (a link-failed service reports no values).
        if value is not None:
            cell = f"{value:.2e}" if kind == "e" else f"{value:.3f}"
        else:
            cell = "-"
        cols.append(f"{cell:>{_COL_W}}")
    return "  ".join(cols)


async def _fetch_services(rest_url: str) -> list[dict[str, Any]]:
    """Connect to the T-API service and return its connectivity services."""
    async with TapiClient(rest_url) as rest:
        return await rest.get_connectivity_services()


def _prompt_for_service(
    services: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Print the service list and read the user's selection from stdin."""
    print("\nAvailable connectivity services:\n")
    for idx, svc in enumerate(services, start=1):
        print(
            f"  [{idx}] {_service_name(svc):<26} "
            f"{_service_modulation(svc):<10} {svc.get('uuid', '')}"
        )

    while True:
        raw = input(
            f"\nSelect a service [1-{len(services)}], or q to quit: "
        ).strip()
        if raw.lower() in {"q", "quit", "exit"}:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(services):
            return services[int(raw) - 1]
        print("  Invalid selection — enter a number from the list.")


async def _stream_service(
    rest_url: str,
    gnmi_target: str | None,
    token: str | None,
    service: dict[str, Any],
) -> None:
    """Follow the selected service's records on the measurement stream."""
    uuid = service["uuid"]
    async with TapiClient(rest_url) as rest:
        endpoint = await discover_stream(rest)
        job_uuid = await oam_job_for_service(rest, uuid)
    target = gnmi_target or endpoint.target

    print(f"\nFollowing '{_service_name(service)}'  ({uuid})")
    print(f"oam-job {job_uuid}")
    print(
        f"T-API stream {endpoint.connection_address} "
        f"[{endpoint.stream_state.rsplit('_', 1)[-1]}] via {target}  ·  Ctrl-C to stop\n",
        flush=True,
    )

    header = _header_line()
    print(header, flush=True)
    print("-" * len(header), flush=True)

    async with TapiStreamClient(target, token) as stream:
        count = 0
        async for record in stream.follow(job_uuid=job_uuid):
            count += 1
            when = (
                # tz-aware, then rendered in the operator's local time.
                _dt.datetime.fromtimestamp(record.sampled_ms / 1000, tz=_dt.UTC)
                .astimezone()
                .strftime("%H:%M:%S")
            )
            # flush each line so the stream is visible when piped/redirected.
            print(_value_line(count, when, record.measurements), flush=True)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, pick a service, and stream its OPM measurements."""
    parser = argparse.ArgumentParser(
        prog="twinlight-client",
        description=(
            "Follow a connectivity service's live, transient-aware OPM on the "
            "twin's T-API performance-measurement stream (TR-548, over gNMI)."
        ),
    )
    parser.add_argument(
        "--rest-url",
        default="http://localhost:8080",
        help="T-API REST base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--gnmi-target",
        default=None,
        help=(
            "gNMI host:port, overriding the connection-address the twin "
            "advertises (useful behind NAT or a port mapping)"
        ),
    )
    parser.add_argument(
        "--token",
        default=os.environ.get(TOKEN_ENV),
        help=f"bearer token for the gNMI stream (default: ${TOKEN_ENV})",
    )
    args = parser.parse_args(argv)

    try:
        services = asyncio.run(_fetch_services(args.rest_url))
    except Exception as exc:  # noqa: BLE001 — surface any connection error
        print(
            f"Failed to fetch services from {args.rest_url}: {exc}",
            file=sys.stderr,
        )
        return 1

    if not services:
        print(f"No connectivity services found on {args.rest_url}.")
        return 0

    service = _prompt_for_service(services)
    if service is None:
        return 0

    try:
        asyncio.run(_stream_service(args.rest_url, args.gnmi_target, args.token, service))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    except Exception as exc:  # noqa: BLE001 — surface any streaming error
        print(f"Streaming error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
