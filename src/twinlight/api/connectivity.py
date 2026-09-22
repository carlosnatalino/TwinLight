"""TAPI Connectivity API endpoints.

POST /data/tapi-connectivity:connectivity-context/connectivity-service
GET  /data/tapi-connectivity:connectivity-context/connectivity-service
GET  /data/tapi-connectivity:connectivity-context/connectivity-service={uuid}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from twinlight.models.connectivity import (
    ConnectivityService,
    UpdateConnectivityServiceRequest,
)
from twinlight.state.context import (
    InsufficientQoTError,
    InsufficientSpectrumError,
    NoPathError,
    RmsaError,
)

router = APIRouter(prefix="/data", tags=["tapi-connectivity"])

_BASE = "/tapi-connectivity:connectivity-context"


def _single_service(body: dict) -> dict:
    """Unwrap one connectivity-service from a RESTCONF request body.

    ``connectivity-service`` is a YANG *list*, so RFC 7951 §5.4 encodes it
    as a name/array pair and RFC 8040 Appendix B.2.1 creates a single entry
    with an array of one. A bare object is the other spelling seen in the
    wild. Both are accepted; more than one entry is not, because the caller
    below admits exactly one service.
    """
    raw = body.get("tapi-connectivity:connectivity-service", body)
    if isinstance(raw, list):
        if len(raw) != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Expected exactly one connectivity-service, got "
                    f"{len(raw)}"
                ),
            )
        raw = raw[0]
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="connectivity-service must be an object or a list of one",
        )
    return raw


def _parse_service(body: dict) -> ConnectivityService:
    """Unwrap and validate a connectivity-service request body.

    The body is validated by hand rather than through a FastAPI parameter
    annotation (the envelope key and the list-or-object spelling both have
    to be resolved first), so pydantic's error has to be mapped to a 422
    here — otherwise a malformed payload surfaces as a 500.
    """
    try:
        return ConnectivityService.model_validate(_single_service(body))
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(
                f"{'/'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}"
                for e in exc.errors()
            ),
        ) from exc


@router.post(
    f"{_BASE}/connectivity-service",
    status_code=status.HTTP_201_CREATED,
)
async def create_service(body: dict, request: Request) -> dict:
    """Create a new connectivity service between two SIPs."""
    ctx = request.app.state.context

    svc = _parse_service(body)

    # Validate that all referenced SIPs exist
    for ep in svc.end_point:
        sip_uuid = ep.service_interface_point.service_interface_point_uuid
        if ctx.get_sip(sip_uuid) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"SIP {sip_uuid!r} not found",
            )

    try:
        await ctx.add_service(svc)
    except NoPathError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except InsufficientSpectrumError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except InsufficientQoTError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except RmsaError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

    return {
        "tapi-connectivity:connectivity-service": _connectivity_service_payload(
            ctx, svc
        )
    }


def _connectivity_service_payload(ctx, svc) -> dict:
    """Build connectivity-service dict with optional frequency-slot (T-API spectrum)."""
    payload = svc.model_dump(by_alias=True)
    spectrum = ctx.get_service_spectrum(svc.uuid)
    if spectrum is not None:
        payload["frequency-slot"] = spectrum
    return payload


@router.get(f"{_BASE}/connectivity-service")
async def get_services(request: Request) -> dict:
    """Return all connectivity services (with frequency-slot when allocated)."""
    ctx = request.app.state.context
    return {
        "tapi-connectivity:connectivity-context": {
            "connectivity-service": [
                _connectivity_service_payload(ctx, s) for s in ctx.get_services()
            ]
        }
    }


@router.get(f"{_BASE}/connectivity-service={{uuid}}")
async def get_service(uuid: str, request: Request) -> dict:
    """Return a specific connectivity service (with frequency-slot when allocated)."""
    ctx = request.app.state.context
    svc = ctx.get_service(uuid)
    if svc is None:
        raise HTTPException(
            status_code=404, detail=f"Service {uuid} not found"
        )
    return {
        "tapi-connectivity:connectivity-service": _connectivity_service_payload(ctx, svc)
    }


@router.put(f"{_BASE}/connectivity-service={{uuid}}")
async def replace_service(uuid: str, body: dict, request: Request) -> dict:
    """Replace a connectivity service by UUID (full replacement).

    The existing service is captured before deletion and re-admitted if the
    replacement cannot be admitted (no path, no spectrum, or insufficient QoT),
    so a failed replace never silently tears down the service that was there.
    """
    ctx = request.app.state.context
    svc = _parse_service(body)
    if svc.uuid != uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body UUID must match path UUID",
        )
    for ep in svc.end_point:
        sip_uuid = ep.service_interface_point.service_interface_point_uuid
        if ctx.get_sip(sip_uuid) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"SIP {sip_uuid!r} not found",
            )
    # Keep a handle on the current allocation so a failed replacement can be
    # rolled back. delete_service frees exactly the resources add_service needs
    # to re-admit old_svc, so the rollback cannot itself fail on those grounds.
    old_svc = ctx.get_service(uuid)
    ctx.delete_service(uuid)
    try:
        await ctx.add_service(svc)
    except RmsaError as e:
        if old_svc is not None:
            await ctx.add_service(old_svc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    return {
        "tapi-connectivity:connectivity-service": _connectivity_service_payload(ctx, svc)
    }


@router.patch(f"{_BASE}/connectivity-service={{uuid}}")
async def update_service(
    uuid: str, body: UpdateConnectivityServiceRequest, request: Request
) -> dict:
    """Partially update (name, admin-state, lifecycle-state)."""
    ctx = request.app.state.context
    svc = ctx.get_service(uuid)
    if svc is None:
        raise HTTPException(
            status_code=404, detail=f"Service {uuid} not found"
        )
    if body.name is not None:
        svc.name = body.name
    if body.administrative_state is not None:
        svc.administrative_state = body.administrative_state
    if body.lifecycle_state is not None:
        svc.lifecycle_state = body.lifecycle_state
    return {
        "tapi-connectivity:connectivity-service": _connectivity_service_payload(
            ctx, svc
        )
    }


@router.delete(
    f"{_BASE}/connectivity-service={{uuid}}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_service(uuid: str, request: Request) -> None:
    """Delete a connectivity service."""
    ctx = request.app.state.context
    if not ctx.delete_service(uuid):
        raise HTTPException(
            status_code=404, detail=f"Service {uuid} not found"
        )
