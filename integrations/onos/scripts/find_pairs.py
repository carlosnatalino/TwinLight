#!/usr/bin/env python3
"""Find endpoint pairs the twin will admit, one per modulation format.

Probes random SIP pairs through ``/internal/path-info``, which runs the real
propagation without provisioning anything, and files each probe under the
highest modulation format its GSNR supports. Prints the winners as

    A-city:Z-city:DP-16QAM:label

lines for ``seed-demo.sh`` to provision through ONOS.

Same idea as ``examples/demo_services.py``, which retries random pairs until
one is admissible — but the pairs are screened before any flow rule is
pushed, because a refused ONOS flow costs seconds of asynchronous round trip
whereas a probe is a single GET.

Reading each probe against *every* threshold rather than a single target is
what keeps the search cheap: a long-haul probe that cannot carry DP-16QAM
still fills a DP-QPSK slot, so almost no probe is wasted.

Candidates are drawn from two pools, because uniformly random pairs do not
find the high-order formats. On CORONET only metro-distance hops reach
DP-64QAM, and they are a handful of the ~2 800 possible pairs — 40 uniform
probes find none. So half the draws come from pairs that are *adjacent* in
the topology (``/internal/links``), which is where the short paths are, and
half from anywhere. Both halves are still random; the adjacency pool only
makes the short end of the reach distribution reachable in a sane number of
probes.

Only the standard library, matching the rest of the demo scripts.
"""

from __future__ import annotations

import json
import os
import random
import sys
import urllib.error
import urllib.request

# Required GSNR per format, plus the default rmsa.qot_margin_db of 1.5 dB.
# Ordered high to low: a probe is filed under the best format it supports.
# Kept in step with physics/modulation.py MODULATION_TABLE.
THRESHOLDS: list[tuple[str, float]] = [
    ("DP-64QAM", 20.5 + 1.5),
    ("DP-16QAM", 14.5 + 1.5),
    ("DP-QPSK", 8.5 + 1.5),
]


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.load(response)


def _cities(base: str) -> dict[str, str]:
    """City name -> SIP UUID, from the twin's T-API context."""
    payload = _get(f"{base}/data/tapi-common:context/service-interface-point")
    sips = payload["tapi-common:context"]["service-interface-point"]
    out: dict[str, str] = {}
    for sip in sips:
        for name in sip.get("name", []):
            if name.get("value-name") == "node-name":
                out[name["value"].replace("trx ", "")] = sip["uuid"]
    return out


def _adjacent(base: str, cities: dict[str, str]) -> list[tuple[str, str]]:
    """City pairs one hop apart, from the twin's ROADM-to-ROADM links.

    Returns an empty list if the endpoint is unavailable — the search then
    falls back to uniform sampling rather than failing.
    """
    try:
        links = _get(f"{base}/internal/links")["links"]
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError):
        return []
    pairs: set[tuple[str, str]] = set()
    for link in links:
        label = str(link.get("label", ""))
        if "->" not in label:
            continue
        a, _, z = label.partition("->")
        a = a.strip().removeprefix("roadm ").strip()
        z = z.strip().removeprefix("roadm ").strip()
        if a in cities and z in cities and a != z:
            pairs.add((a, z) if a < z else (z, a))
    return sorted(pairs)


def _probe(base: str, sip_a: str, sip_z: str) -> tuple[float, float] | None:
    """(GSNR dB, path km) for a pair, or None if it cannot be evaluated."""
    url = (
        f"{base}/internal/path-info"
        f"?sip_a={sip_a}&sip_z={sip_z}&modulation=DP-QPSK"
    )
    try:
        info = _get(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None
    measurements = info.get("measurements") or {}
    gsnr = measurements.get("gsnr-db")
    if not isinstance(gsnr, (int, float)):
        return None
    return float(gsnr), float(info.get("total-fiber-km") or 0.0)


def main() -> int:
    base = os.environ.get("TWIN_URL", "http://localhost:8080").rstrip("/")
    wanted = {
        "DP-64QAM": int(os.environ.get("Q64_WANTED", "2")),
        "DP-16QAM": int(os.environ.get("Q16_WANTED", "3")),
        "DP-QPSK": int(os.environ.get("QPSK_WANTED", "3")),
    }
    attempts = int(os.environ.get("ATTEMPTS", "40"))
    seed = os.environ.get("SEED_RANDOM_SEED") or None
    rng = random.Random(seed) if seed else random.Random()

    cities = _cities(base)
    if len(cities) < 2:
        print("twin reported fewer than two SIPs", file=sys.stderr)
        return 1

    names = sorted(cities)
    adjacent = _adjacent(base, cities)
    found: dict[str, list[tuple[str, str, float, float]]] = {
        fmt: [] for fmt in wanted
    }
    tried: set[tuple[str, str]] = set()

    for attempt in range(attempts):
        if all(len(found[f]) >= wanted[f] for f in wanted):
            break
        # Alternate the two pools so both the short and the long end of the
        # reach distribution get probed within the budget.
        if adjacent and attempt % 2 == 0:
            a, z = rng.choice(adjacent)
        else:
            a, z = rng.sample(names, 2)
        key = (a, z) if a < z else (z, a)
        if key in tried:
            continue
        tried.add(key)

        result = _probe(base, cities[a], cities[z])
        if result is None:
            continue
        gsnr, km = result

        # File under the best format this pair supports and still needs.
        for fmt, threshold in THRESHOLDS:
            if gsnr >= threshold and len(found[fmt]) < wanted[fmt]:
                found[fmt].append((a, z, gsnr, km))
                break

    for fmt, _threshold in THRESHOLDS:
        for a, z, gsnr, km in found[fmt]:
            label = f"{km:.0f} km, {gsnr:.1f} dB"
            print(f"{a}:{z}:{fmt}:{label}")

    short = [
        f"{f} {len(found[f])}/{wanted[f]}"
        for f in wanted
        if len(found[f]) < wanted[f]
    ]
    if short:
        # Not fatal: a topology may simply have nothing short enough for
        # DP-64QAM. Say so rather than silently seeding fewer lightpaths.
        print(
            f"probed {len(tried)} pairs; short of target: {', '.join(short)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
