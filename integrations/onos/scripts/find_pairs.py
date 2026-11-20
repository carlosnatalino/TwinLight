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

**A probe is not the admission decision.** ``/internal/path-info`` reports
the GSNR an OPM read would give now: the GNPy baseline *after* the twin's
time-varying transient models (PDL and SOP drift, phase noise, EDFA
dynamics). Admission gates on the pristine baseline, with no transient term
(integrations/onos/README.md, "Why the live GSNR differs from the admission
figure"). The two differ by a few tenths of a dB, more on paths with many
ROADMs, in either direction and with the time of the probe. So a pair whose
pristine GSNR lies just below a threshold can probe above it and be refused
at create: San_Jose -> Louisville on CORONET probed at 10.3 dB against
DP-QPSK's 10.0 dB, and admission found 9.9 dB. The line load of the pairs
seeded before it moved that figure by less than 0.1 dB. No twin endpoint
gives the pristine figure without provisioning, so the search finds up to
``SPARES`` extra pairs per format (default 1), printed after that format's
winners. ``seed-demo.sh`` provisions a spare only when a winner is refused,
and removes the refused flow.

``SEED_RANDOM_SEED`` fixes the sequence of pairs drawn. The search is then
repeatable up to that same transient term: a pair probing within a few
tenths of a dB of a threshold can land on either side of it on another run,
and since a winner takes its A-end, what is probed after it can differ too.
Against the same twin state at the same moment, it is the same search.

**The admission margin is the twin's.** A format's threshold is its required
GSNR plus ``rmsa.qot_margin_db``, which is a runtime setting (``/config/set``
and the transparent controller change it). So each search starts by reading
it from the twin it probes, at ``/transparent/data/Config/rmsa/qot_margin_db``:
the transparent controller is the interface the twin documents for reading
its settings (docs/CONFIGURATION.md, docs/TRANSPARENT_CONTROLLER.md), and it
answers the effective value, which ``/config/get`` does not (it lists only
overrides) and ``/metrics`` exports only for monitoring. The request carries
``TWINLIGHT_API_TOKEN`` as the other scripts' requests to the twin do. A
twin that cannot answer (one that predates ``/transparent``, or an answer
without the value) stops the search with the reason rather than falling
back to a default: a guessed margin is the silent mismatch this replaced,
seeding refusals or a short format with nothing to say why.

The required GSNR per format is still this file's copy of the twin's
modulation table (``REQUIRED_GSNR_DB``). The twin publishes it read-only at
``/transparent/data/ModulationList/<format>/req_gsnr_db``; it changes only
with the twin's code, not at run time.

Only the standard library, matching the rest of the demo scripts.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import urllib.error
from collections.abc import Callable

from tapi_fields import twin_data_root, twin_urlopen  # same directory: on sys.path when run

# Required GSNR per format, before the margin. Ordered high to low: a probe is
# filed under the best format it supports. Kept in step with
# physics/modulation.py MODULATION_TABLE (the twin's ModulationList).
REQUIRED_GSNR_DB: list[tuple[str, float]] = [
    ("DP-64QAM", 20.5),
    ("DP-16QAM", 14.5),
    ("DP-QPSK", 8.5),
]

# Where the twin answers its admission margin (see the module docstring).
MARGIN_PATH = "/transparent/data/Config/rmsa/qot_margin_db"


class MarginUnavailableError(RuntimeError):
    """The twin did not say what its admission margin is."""


def thresholds(margin_db: float) -> list[tuple[str, float]]:
    """(format, GSNR the twin admits it at), high to low, for a margin."""
    return [(fmt, required + margin_db) for fmt, required in REQUIRED_GSNR_DB]


def _as_margin(value: object) -> float | None:
    """A margin in dB from the leaf's JSON value, or None.

    A number, or a numeric string: RFC 7951 §6.1 encodes a YANG decimal64 as
    a string, should the leaf ever be typed so.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        margin = float(value)
    elif isinstance(value, str):
        try:
            margin = float(value)
        except ValueError:
            return None
    else:
        return None
    return margin if math.isfinite(margin) and margin >= 0 else None


def read_margin(base: str) -> float:
    """The twin's ``rmsa.qot_margin_db`` now, in dB.

    Raises :class:`MarginUnavailableError` with the reason when the twin
    cannot be asked or does not say; a refused token exits through
    :func:`twin_urlopen` with its own explanation.
    """
    url = f"{base.rstrip('/')}{MARGIN_PATH}"
    try:
        body = _get(url)
    except urllib.error.HTTPError as exc:
        raise MarginUnavailableError(
            f"GET {url}: HTTP {exc.code}; a twin without the transparent "
            "controller predates this script"
        ) from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise MarginUnavailableError(f"GET {url}: {exc}") from None
    rmsa = (
        ((body.get("twinlight-transparent:data") or {}).get("Config") or {}).get("rmsa")
        if isinstance(body, dict) else None
    )
    margin = _as_margin(rmsa.get("qot_margin_db")) if isinstance(rmsa, dict) else None
    if margin is None:
        raise MarginUnavailableError(
            f"GET {url}: the answer carries no usable qot_margin_db: {json.dumps(body)[:200]}"
        )
    return margin


def _get(url: str) -> dict:
    with twin_urlopen(url, timeout=120) as response:
        return json.load(response)


def _cities(base: str) -> dict[str, str]:
    """City name -> SIP UUID, from the twin's T-API context."""
    payload = _get(f"{twin_data_root(base)}/tapi-common:context/service-interface-point")
    # The list is the body's one member (RFC 8040 §4.3); without it, a KeyError
    # rather than "no SIPs".
    sips = payload["tapi-common:service-interface-point"]
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


Found = dict[str, list[tuple[str, str, float, float]]]


def search(
    rng: random.Random,
    names: list[str],
    adjacent: list[tuple[str, str]],
    probe: Callable[[str, str], tuple[float, float] | None],
    wanted: dict[str, int],
    spares: int,
    attempts: int,
    margin_db: float,
) -> tuple[Found, int]:
    """Draw and probe pairs; per format, up to ``wanted`` + ``spares`` winners.

    A format's threshold is its required GSNR plus ``margin_db``, the
    twin's admission margin (:func:`read_margin`); the search itself asks
    the twin nothing but the probes.
    ``probe(a, z)`` returns (GSNR dB, path km) or None. Winners are kept in
    the order found, so the first ``wanted`` of a format are its pairs and
    the rest its spares. ``names`` and ``adjacent`` must be in a fixed order
    for a seeded ``rng`` to repeat the draw. Returns the winners and how many
    distinct pairs were drawn.
    """
    limit = {fmt: wanted[fmt] + spares for fmt in wanted}
    found: Found = {fmt: [] for fmt in wanted}
    tried: set[tuple[str, str]] = set()
    # ONOS keys an OLS flow rule on its selector, the in-port, and not on the
    # out-port: a second lightpath from the same A-end gets the first one's
    # flow id and *replaces* it in ONOS, while the twin keeps both services.
    # So each winner needs an A-end of its own; a pair whose A-end is taken is
    # turned round when its Z-end is still free.
    sources: set[str] = set()

    for attempt in range(attempts):
        if all(len(found[f]) >= limit[f] for f in wanted):
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
        if a in sources:
            if z in sources:
                continue
            a, z = z, a

        result = probe(a, z)
        if result is None:
            continue
        gsnr, km = result

        # File under the best format this pair supports and still needs.
        for fmt, threshold in thresholds(margin_db):
            if fmt in found and gsnr >= threshold and len(found[fmt]) < limit[fmt]:
                found[fmt].append((a, z, gsnr, km))
                sources.add(a)
                break
    return found, len(tried)


def main() -> int:
    base = os.environ.get("TWIN_URL", "http://localhost:8080").rstrip("/")
    wanted = {
        "DP-64QAM": int(os.environ.get("Q64_WANTED", "2")),
        "DP-16QAM": int(os.environ.get("Q16_WANTED", "3")),
        "DP-QPSK": int(os.environ.get("QPSK_WANTED", "3")),
    }
    spares = int(os.environ.get("SPARES", "1"))
    attempts = int(os.environ.get("ATTEMPTS", "40"))
    seed = os.environ.get("SEED_RANDOM_SEED") or None
    rng = random.Random(seed) if seed else random.Random()

    try:
        margin = read_margin(base)
    except MarginUnavailableError as exc:
        print(f"cannot read the twin's admission margin: {exc}", file=sys.stderr)
        return 2
    print(
        f"admission margin {margin:g} dB, read from the twin: thresholds "
        + ", ".join(f"{fmt} {gsnr:g} dB" for fmt, gsnr in thresholds(margin)),
        file=sys.stderr,
    )

    cities = _cities(base)
    if len(cities) < 2:
        print("twin reported fewer than two SIPs", file=sys.stderr)
        return 1

    found, tried = search(
        rng,
        sorted(cities),
        _adjacent(base, cities),
        lambda a, z: _probe(base, cities[a], cities[z]),
        wanted,
        spares,
        attempts,
        margin,
    )

    for fmt, _required in REQUIRED_GSNR_DB:
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
            f"probed {tried} pairs; short of target: {', '.join(short)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
