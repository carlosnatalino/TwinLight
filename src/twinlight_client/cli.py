"""Interactive CLI: subscribe to a service's live OPM stream over gNMI.

Flow:
  1. Connect to the T-API REST service and list connectivity services.
  2. Let the user pick one.
  3. Subscribe to that service's OPM path over the gNMI streaming API
     at a 5-second sample interval and print every update.

The OPM values streamed include the transient fluctuations of the signal
quality (EDFA reservoir, polarization/PDL, phase noise, environmental),
since the gNMI server resolves the `.../connectivity-service[uuid=X]/opm`
path to the twin's transient-aware OPM endpoint.

Installed as the ``twinlight-client`` console command (see pyproject.toml):

    twinlight-client
    twinlight-client --rest-url URL --gnmi-target HOST:PORT

Equivalently runnable as a module: ``python -m twinlight_client``.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import sys
from typing import Any

from twinlight_client.client import TapiClient
from twinlight_client.streaming import GnmiConsumer

SAMPLE_INTERVAL_S = 5.0

# (measurement key, column header, formatter kind) — display order.
# kind: "f" → fixed-point with 3 decimals, "e" → scientific with 2 decimals.
_METRICS: list[tuple[str, str, str]] = [
    ("osnr-db", "OSNR(dB)", "f"),
    ("gsnr-db", "GSNR(dB)", "f"),
    ("q-factor-db", "Q(dB)", "f"),
    ("pre-fec-ber", "preFEC-BER", "e"),
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


def _header_line() -> str:
    """Build the one-time header row, aligned to the value columns."""
    cols = [f"{'#':>{_NUM_W}}", f"{'time':>{_TIME_W}}"]
    cols += [f"{header:>{_COL_W}}" for _, header, _ in _METRICS]
    return "  ".join(cols)


def _value_line(count: int, when: str, measurements: dict[str, Any]) -> str:
    """Build a values-only row, aligned under _header_line()."""
    cols = [f"{f'#{count:03d}':>{_NUM_W}}", f"{when:>{_TIME_W}}"]
    for key, _, kind in _METRICS:
        if key in measurements:
            value = measurements[key]
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
            f"{svc.get('modulation-format', '?'):<10} {svc.get('uuid', '')}"
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
    gnmi_target: str, service: dict[str, Any]
) -> None:
    """Subscribe to the selected service's OPM stream and print each update."""
    uuid = service["uuid"]
    path = (
        "tapi-connectivity:connectivity-context/"
        f"connectivity-service[uuid={uuid}]/opm"
    )

    print(f"\nSubscribing to '{_service_name(service)}'  ({uuid})")
    print(
        f"gNMI STREAM @ {gnmi_target}  ·  sample interval "
        f"{SAMPLE_INTERVAL_S:.0f}s  ·  Ctrl-C to stop\n",
        flush=True,
    )

    header = _header_line()
    print(header, flush=True)
    print("-" * len(header), flush=True)

    async with GnmiConsumer(gnmi_target) as gnmi:
        count = 0
        async for update in gnmi.subscribe_stream(
            [path], sample_interval_s=SAMPLE_INTERVAL_S
        ):
            count += 1
            timestamp = update.get("timestamp")
            when = (
                # tz-aware, then rendered in the operator's local time.
                _dt.datetime.fromtimestamp(timestamp, tz=_dt.UTC)
                .astimezone()
                .strftime("%H:%M:%S")
                if isinstance(timestamp, (int, float))
                else "--:--:--"
            )
            measurements = update.get("measurements", {})
            # flush each line so the stream is visible when piped/redirected.
            print(_value_line(count, when, measurements), flush=True)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, pick a service, and stream its OPM measurements."""
    parser = argparse.ArgumentParser(
        prog="twinlight-client",
        description=(
            "Subscribe to a digital-twin connectivity service's live, "
            "transient-aware OPM stream over the gNMI streaming API."
        ),
    )
    parser.add_argument(
        "--rest-url",
        default="http://localhost:8080",
        help="T-API REST base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--gnmi-target",
        default="localhost:50051",
        help="gNMI streaming target host:port (default: %(default)s)",
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
        asyncio.run(_stream_service(args.gnmi_target, service))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    except Exception as exc:  # noqa: BLE001 — surface any streaming error
        print(f"Streaming error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
