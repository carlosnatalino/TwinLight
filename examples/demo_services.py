#!/usr/bin/env python3
"""Populate a running TwinLight instance with demo connectivity services.

An empty twin has nothing to monitor. This script creates a configurable number
of services per modulation format between random endpoint pairs, so the
monitoring, spectrum and services views have something to show.

It uses only the Python standard library, so it runs against a twin hosted
anywhere -- including the Docker Compose stack -- without installing TwinLight.
Being a plain ``.py`` file (rather than a shell here-doc), it runs identically
on Windows, macOS and Linux::

    python examples/demo_services.py                 # one service per format
    python examples/demo_services.py -n 5            # five per format
    python examples/demo_services.py --base-url http://host:8080 --attempts 300

Endpoint pairs are retried because admission is genuinely allowed to fail: a
409 means no route, no contiguous spectrum, or a GSNR below the format's
threshold. Higher-order formats need more GSNR, so DP-64QAM usually takes more
attempts than DP-QPSK, and on a long-haul topology it may not be admissible at
all.
"""

from __future__ import annotations

import argparse
import json
import random
import urllib.request
from urllib.error import HTTPError

MODULATION_FORMATS = ("DP-QPSK", "DP-16QAM", "DP-64QAM")


def _create_service(base: str, sips: list, modulation: str, attempts: int) -> bool:
    """Try random endpoint pairs until one service of `modulation` is admitted.

    Returns True and prints the service's live OPM on success; returns False if
    no pair was admissible within `attempts` tries (or the twin returned a
    non-409 error, which is surfaced).
    """
    name = f"demo-{modulation}-{random.getrandbits(16):04x}"
    for attempt in range(1, attempts + 1):
        a, z = random.sample(sips, 2)
        body = {
            "tapi-connectivity:connectivity-service": {
                "name": [{"value-name": "service-name", "value": name}],
                "modulation-format": modulation,
                "end-point": [
                    {
                        "local-id": "a-end",
                        "service-interface-point": {"service-interface-point-uuid": a["uuid"]},
                    },
                    {
                        "local-id": "z-end",
                        "service-interface-point": {"service-interface-point-uuid": z["uuid"]},
                    },
                ],
            }
        }
        req = urllib.request.Request(
            f"{base}/data/tapi-connectivity:connectivity-context/connectivity-service",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            svc = json.load(urllib.request.urlopen(req))["tapi-connectivity:connectivity-service"]
        except HTTPError as exc:
            # 409 = not admissible on this pair (no route / no spectrum / QoT
            # below threshold). Anything else is a real error worth surfacing.
            if exc.code == 409:
                continue
            print(f"{modulation}: HTTP {exc.code} -- {exc.read().decode()[:200]}")
            return False
        uuid = svc["uuid"]
        opm = json.load(urllib.request.urlopen(f"{base}/internal/opm/{uuid}"))["measurements"]
        slot = svc.get("frequency-slot", {})
        print(f"{modulation:<9} {name}  attempt {attempt}")
        print(f"            uuid   {uuid}")
        print(f"            GSNR   {opm['gsnr-db']:.2f} dB   OSNR {opm['osnr-db']:.2f} dB")
        print(
            f"            slot   {slot.get('nominal-central-frequency')} THz / "
            f"{slot.get('slot-width')} GHz"
        )
        return True

    print(f"{modulation:<9} not admissible after {attempts} attempts")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "-u",
        "--base-url",
        default="http://localhost:8080",
        help="Base URL of the running twin (default: %(default)s)",
    )
    parser.add_argument(
        "-n",
        "--services-per-mf",
        type=int,
        default=1,
        help="Number of services to create per modulation format (default: %(default)s)",
    )
    parser.add_argument(
        "-a",
        "--attempts",
        type=int,
        default=300,
        help="Endpoint pairs to try per service before giving up (default: %(default)s)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    ctx = json.load(
        urllib.request.urlopen(f"{base}/data/tapi-common:context/service-interface-point")
    )
    sips = ctx["tapi-common:context"]["service-interface-point"]
    if len(sips) < 2:
        raise SystemExit("Need at least 2 service interface points")

    for modulation in MODULATION_FORMATS:
        admitted = 0
        for _ in range(args.services_per_mf):
            if _create_service(base, sips, modulation, args.attempts):
                admitted += 1
        if args.services_per_mf > 1:
            print(f"{modulation:<9} admitted {admitted}/{args.services_per_mf}")


if __name__ == "__main__":
    main()
