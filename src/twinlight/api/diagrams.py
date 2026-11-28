"""Diagram generation endpoints: eye diagram and constellation diagram.

Statistical synthesis from current OPM measurements:
- Constellation: GMM-style with noise variance from GSNR, phase noise
- Eye diagram: Raised-cosine pulses with GSNR noise, PMD jitter

References:
    - Sequeira et al., "OCATA: A Deep-Learning-Based Digital Twin," ECOC 2023
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/internal", tags=["diagrams"])


async def _get_measurements_and_modulation(
    service_uuid: str, request: Request
) -> tuple[dict[str, float], str, float]:
    """Fetch current OPM measurements and modulation format for a service.

    The measurements are the OPM endpoints' and the measurement stream's
    own (``sample_service_opm``), so a diagram is drawn from the GSNR they
    report at the same instant.

    Returns:
        Tuple of (measurements dict, modulation_format string, linewidth_hz).

    Raises:
        HTTPException: 404 for an unknown service; 409 when the service
            carries no signal (its path is cut, or its channel lies outside
            the amplifier band), which leaves nothing to draw.
    """
    from twinlight.api.internal import sample_service_opm, wall_clock

    ctx = request.app.state.context
    cfg = request.app.state.config

    svc = ctx.get_service(service_uuid)
    if svc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service {service_uuid} not found",
        )

    entry = await sample_service_opm(ctx, cfg, svc, wall_clock())
    if (status := entry.get("status")) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Service {service_uuid} carries no signal to draw ({status})",
        )
    measurements = entry["measurements"]

    pn_cfg = cfg.transients.phase_noise
    linewidth_hz = pn_cfg.tx_linewidth_hz + pn_cfg.lo_linewidth_hz

    return measurements, svc.modulation_format.value, linewidth_hz


@router.get("/services/{service_uuid}/constellation")
async def get_constellation(
    service_uuid: str,
    request: Request,
    n_symbols: int = Query(
        default=10000, ge=100, le=100000, description="Symbols to generate"
    ),
) -> dict:
    """Generate a constellation diagram for a connectivity service.

    Returns I/Q coordinates of received symbols, synthesized from current OPM
    measurements. The constellation reflects GSNR-based noise and
    linewidth-based phase noise.

    Args:
        service_uuid: UUID of the connectivity service.
        n_symbols: Number of symbols to generate (100–100000).

    Returns:
        JSON with 'i' and 'q' arrays (real/imag), modulation format,
        and OPM measurements used.
    """
    from twinlight.output.constellation import (
        constellation_to_iq,
        synthesize_constellation,
    )

    measurements, modulation_format, linewidth_hz = (
        await _get_measurements_and_modulation(service_uuid, request)
    )

    symbols = synthesize_constellation(
        measurements=measurements,
        modulation_format=modulation_format,
        n_symbols=n_symbols,
        linewidth_hz=linewidth_hz,
    )

    i_coords, q_coords = constellation_to_iq(symbols)

    return {
        "service-uuid": service_uuid,
        "modulation-format": modulation_format,
        "n_symbols": n_symbols,
        "i": i_coords,
        "q": q_coords,
        "measurements": {
            "gsnr-db": measurements.get("gsnr-db"),
            "linewidth-hz": linewidth_hz,
        },
    }


@router.get("/services/{service_uuid}/eye-diagram")
async def get_eye_diagram(
    service_uuid: str,
    request: Request,
    n_traces: int = Query(
        default=200, ge=10, le=2000, description="Number of traces"
    ),
    samples_per_symbol: int = Query(
        default=64, ge=16, le=256, description="Time resolution"
    ),
) -> dict:
    """Generate an eye diagram for a connectivity service.

    Returns time-vs-amplitude traces synthesized from current OPM measurements.
    The eye reflects GSNR-based noise and PMD-based timing jitter.

    Args:
        service_uuid: UUID of the connectivity service.
        n_traces: Number of traces (10–2000).
        samples_per_symbol: Samples per symbol period (16–256).

    Returns:
        JSON with 'time_ns' array, 'traces' (list of amplitude arrays),
        symbol period, and OPM measurements used.
    """
    from twinlight.output.eye_diagram import (
        eye_to_json,
        synthesize_eye,
    )

    measurements, modulation_format, _ = await _get_measurements_and_modulation(
        service_uuid, request
    )

    eye_data = synthesize_eye(
        measurements=measurements,
        modulation_format=modulation_format,
        n_traces=n_traces,
        samples_per_symbol=samples_per_symbol,
    )

    result = eye_to_json(eye_data, max_traces=n_traces)
    result["service-uuid"] = service_uuid
    result["modulation-format"] = modulation_format
    result["measurements"] = {
        "gsnr-db": measurements.get("gsnr-db"),
        "pmd-ps": measurements.get("pmd-ps"),
    }

    return result
