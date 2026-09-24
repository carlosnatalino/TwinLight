"""Internal OPM endpoints used by the gNMI adapter.

Not part of the TAPI specification. These are queried in-process
by the gNMI server via httpx ASGITransport.
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

# ---------------------------------------------------------------------------
# Service info endpoint
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/internal", tags=["internal"])

# Per-metric: (baseline, amplitude, period_seconds)
_METRIC_PARAMS: dict[str, tuple[float, float, float]] = {
    "osnr-db": (25.0, 2.0, 60.0),
    "gsnr-db": (20.0, 1.5, 80.0),
    "q-factor-db": (10.0, 1.0, 70.0),
    "chromatic-dispersion-ps-per-nm": (800.0, 50.0, 120.0),
    "pmd-ps": (0.5, 0.08, 90.0),
}


def _phase(uuid: str, metric: str) -> float:
    """Deterministic phase offset in [0, 2π) derived from UUID + metric."""
    digest = hashlib.md5(
        f"{uuid}:{metric}".encode(), usedforsecurity=False
    ).hexdigest()
    return (int(digest, 16) % 100_000) / 100_000.0 * 2 * math.pi


def _mock_path_factor(service_uuid: str) -> float:
    """Deterministic factor in [0.5, 1.5] from service UUID.

    Used so mock baselines differ per service (simulating different path
    lengths). Longer "path" = lower OSNR/GSNR/Q, higher CD/PMD.
    """
    digest = hashlib.md5(
        f"{service_uuid}:path_factor".encode(), usedforsecurity=False
    ).hexdigest()
    return 0.5 + (int(digest[:8], 16) % 1000) / 1000.0


def _link_failed_measurements() -> dict[str, Any]:
    """OPM payload for a service whose path crosses a failed fiber.

    Returns the standard six-metric dict with ``None`` for every numeric
    value plus a worst-case ``pre-fec-ber`` of 1.0, so UI charts can render
    a clear "service down" line rather than zeros that look like a real
    measurement. The caller adds a top-level ``"status": "link-failed"``.
    """
    return {
        "osnr-db": None,
        "osnr-01nm-db": None,
        "gsnr-db": None,
        "q-factor-db": None,
        "chromatic-dispersion-ps-per-nm": None,
        "pmd-ps": None,
        "pre-fec-ber": 1.0,
    }


def _mock_measurements(
    service_uuid: str, t: float, modulation_format: str = "DP-QPSK"
) -> dict:
    """Return time-varying sinusoidal mock measurements (fallback).

    Each service gets a unique path factor so that metric ranges differ
    between services (simulating different path lengths when GNPy is
    unavailable). Phase offsets per (UUID, metric) keep waveforms distinct.
    BER uses a multiplicative form to stay strictly positive.

    The modulation format is needed only to re-reference OSNR to 0.1 nm,
    so that a UI reading mock data sees the same pair of fields, in the
    same relationship, as one reading real physics.
    """
    path_factor = _mock_path_factor(service_uuid)
    # Longer path: worse OSNR/GSNR/Q (subtract), higher CD/PMD (multiply)
    measurements: dict[str, float] = {}
    for metric, (baseline, amp, period) in _METRIC_PARAMS.items():
        phase = _phase(service_uuid, metric)
        if metric in ("osnr-db", "gsnr-db", "q-factor-db"):
            adj_baseline = baseline - (path_factor - 0.5) * baseline * 0.4
            measurements[metric] = adj_baseline + amp * math.sin(
                2 * math.pi * t / period + phase
            )
        else:
            adj_baseline = baseline * path_factor
            measurements[metric] = adj_baseline + amp * math.sin(
                2 * math.pi * t / period + phase
            )

    ber_phase = _phase(service_uuid, "pre-fec-ber")
    ber_base = 1e-3 * path_factor
    measurements["pre-fec-ber"] = ber_base * (
        1 + 0.5 * math.sin(2 * math.pi * t / 45.0 + ber_phase)
    )

    from twinlight.physics.modulation import osnr_to_01nm_db

    measurements["osnr-01nm-db"] = osnr_to_01nm_db(
        measurements["osnr-db"], modulation_format
    )
    return measurements


@router.get("/opm")
async def get_all_opm(request: Request) -> dict:
    """Return physics-based OPM data for every known connectivity service.

    Falls back to sinusoidal mock data if GNPy is unavailable or the
    baseline cannot be computed for a specific service.
    """
    ctx = request.app.state.context
    cfg = request.app.state.config
    t = time.time()
    results = []

    for svc in ctx.get_services():
        baseline = await ctx.get_or_compute_baseline(svc)
        entry: dict[str, Any] = {"service-uuid": svc.uuid, "timestamp": t}
        if baseline is not None and baseline.status == "link-failed":
            entry["status"] = "link-failed"
            entry["measurements"] = _link_failed_measurements()
        elif baseline is None:
            entry["measurements"] = _mock_measurements(
                svc.uuid, t, svc.modulation_format.value
            )
        else:
            from twinlight.physics.transients.cascade import apply_all_transients

            entry["measurements"] = apply_all_transients(
                baseline,
                svc.uuid,
                svc.modulation_format.value,
                t,
                cfg.transients,
                edfa_tracker=ctx.edfa_tracker,
            )
        results.append(entry)

    return {"services": results, "timestamp": t}


@router.get("/opm/{service_uuid}")
async def get_service_opm(service_uuid: str, request: Request) -> dict:
    """Return physics-based OPM data for a specific service.

    Falls back to sinusoidal mock data if GNPy is unavailable.
    """
    ctx = request.app.state.context
    cfg = request.app.state.config
    t = time.time()

    svc = ctx.get_service(service_uuid)
    if svc is None:
        raise HTTPException(status_code=404, detail=f"Service {service_uuid} not found")

    baseline = await ctx.get_or_compute_baseline(svc)
    response: dict[str, Any] = {
        "service-uuid": service_uuid,
        "timestamp": t,
    }
    if baseline is not None and baseline.status == "link-failed":
        response["status"] = "link-failed"
        response["measurements"] = _link_failed_measurements()
    elif baseline is None:
        response["measurements"] = _mock_measurements(
            service_uuid, t, svc.modulation_format.value
        )
    else:
        from twinlight.physics.transients.cascade import apply_all_transients

        response["measurements"] = apply_all_transients(
            baseline,
            svc.uuid,
            svc.modulation_format.value,
            t,
            cfg.transients,
            edfa_tracker=ctx.edfa_tracker,
        )

    return response


@router.get("/services/{service_uuid}")
async def get_service_info(service_uuid: str, request: Request) -> dict:
    """Return metadata and path topology for a connectivity service.

    The ``hops`` list contains Transceiver and Roadm elements in path order,
    with the accumulated fiber distance (km) to the next hop.  Inline EDFAs
    are excluded from the hop list.  Returns null for ``hops`` when GNPy is
    unavailable or the path cannot be resolved.
    """
    ctx = request.app.state.context
    svc = ctx.get_service(service_uuid)
    if svc is None:
        raise HTTPException(status_code=404, detail=f"Service {service_uuid} not found")

    # Extract human-readable name from the name list (first "service-name" entry)
    name = next(
        (n.value for n in svc.name if n.value_name == "service-name"), None
    )

    hops = ctx.get_service_path_hops(svc)
    total_fiber_km = None
    if hops:
        distances = [h["distance_km_to_next"] for h in hops if h["distance_km_to_next"] is not None]
        total_fiber_km = round(sum(distances), 2) if distances else None

    return {
        "service-uuid": service_uuid,
        "name": name,
        "modulation-format": svc.modulation_format.value,
        "hops": hops,
        "total-fiber-km": total_fiber_km,
    }


# ---------------------------------------------------------------------------
# Path info (for Path computation UI: hops + distance + GSNR estimate)
# ---------------------------------------------------------------------------

@router.get("/path-info")
async def get_path_info(
    request: Request,
    sip_a: str = "",
    sip_z: str = "",
    modulation: str = "DP-QPSK",
) -> dict:
    """Return path topology (hops, total-fiber-km) and optional GSNR/OPM.

    Used by the Path computation UI: same visualization as Services
    detail (path & distance) and estimated GSNR/OPM via GNPy. Not T-API.

    Query params: sip_a, sip_z (required), modulation (optional).
    """
    if not sip_a or not sip_z:
        raise HTTPException(
            status_code=422,
            detail="Query parameters sip_a and sip_z are required",
        )
    ctx = request.app.state.context
    if ctx.get_sip(sip_a) is None:
        raise HTTPException(status_code=404, detail=f"SIP {sip_a!r} not found")
    if ctx.get_sip(sip_z) is None:
        raise HTTPException(status_code=404, detail=f"SIP {sip_z!r} not found")

    hops = ctx.get_path_hops_between_sips(sip_a, sip_z)
    total_fiber_km = None
    if hops:
        distances = [
            h["distance_km_to_next"]
            for h in hops
            if h.get("distance_km_to_next") is not None
        ]
        total_fiber_km = round(sum(distances), 2) if distances else None

    measurements = None
    baseline = await ctx.get_path_baseline(sip_a, sip_z, modulation)
    if baseline is not None:
        cfg = request.app.state.config
        t = time.time()
        from twinlight.physics.transients.cascade import apply_all_transients

        measurements = apply_all_transients(
            baseline,
            f"path-info:{sip_a[:8]}:{sip_z[:8]}",
            modulation,
            t,
            cfg.transients,
            edfa_tracker=ctx.edfa_tracker,
        )

    return {
        "sip-a": sip_a,
        "sip-z": sip_z,
        "modulation-format": modulation,
        "hops": hops or [],
        "total-fiber-km": total_fiber_km,
        "measurements": measurements,
    }


# ---------------------------------------------------------------------------
# ROADM–ROADM links (for UI focus; T-API topology includes TRX–ROADM too)
# ---------------------------------------------------------------------------

@router.get("/links")
async def get_roadm_to_roadm_links(request: Request) -> dict:
    """Return only links between two ROADMs (excludes TRX–ROADM access links).

    Use for link-focused UI: spectrum grid, dashboard link count, etc.
    """
    ctx = request.app.state.context
    links = ctx.get_roadm_to_roadm_links()
    return {"links": links}


# ---------------------------------------------------------------------------
# Spectrum grid (link×slot occupancy; path not in T-API)
# ---------------------------------------------------------------------------

@router.get("/spectrum-grid")
async def get_spectrum_grid(request: Request) -> dict:
    """Return link×slot grid for spectrum visualization.

    Rows = links (T-API topology order), columns = slots.
    occupancy[link_idx][slot_idx] = service_uuid or null.
    Requires internal API because T-API does not expose per-link, per-slot
    assignment or path (list of links) on connectivity-service.
    """
    ctx = request.app.state.context
    return ctx.get_spectrum_grid_data()


@router.get("/spectrum-context")
async def get_spectrum_context(request: Request) -> dict:
    """Return the flexible-grid parameters this twin is configured with.

    Lives under ``/internal/`` because it is twin *configuration*, not a
    T-API resource: ``num-slots``, ``slot-width-ghz`` and
    ``nominal-central-frequency-thz`` are names this project invented, and
    T-API v2.6.0 defines no ``spectrum-context`` container to put them in.
    Publishing them under the ``tapi-photonic-media`` prefix, as an earlier
    release did, gave a client no way to tell they were not standard.

    The standard view of the same grid is the per-SIP
    ``spectrum-capability-pac`` on each service-interface-point, which
    reports real bands in Hz.
    """
    spec = request.app.state.config.spectrum
    return {
        "num-slots": spec.num_slots,
        "slot-width-ghz": spec.slot_width_ghz,
        "nominal-central-frequency-thz": spec.center_frequency_thz,
    }
