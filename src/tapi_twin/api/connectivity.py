"""TAPI Connectivity API endpoints.

POST /data/tapi-connectivity:connectivity-context/connectivity-service
GET  /data/tapi-connectivity:connectivity-context/connectivity-service
GET  /data/tapi-connectivity:connectivity-context/connectivity-service={uuid}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from tapi_twin.models.connectivity import (
    ConnectivityService,
    UpdateConnectivityServiceRequest,
)
from tapi_twin.state.context import (
    InsufficientQoTError,
    InsufficientSpectrumError,
    NoPathError,
    RmsaError,
)

router = APIRouter(prefix="/data", tags=["tapi-connectivity"])

_BASE = "/tapi-connectivity:connectivity-context"


@router.post(
    f"{_BASE}/connectivity-service",
    status_code=status.HTTP_201_CREATED,
)
async def create_service(body: dict, request: Request) -> dict:
    """Create a new connectivity service between two SIPs."""
    ctx = request.app.state.context

    raw = body.get("tapi-connectivity:connectivity-service", body)
    svc = ConnectivityService.model_validate(raw)

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
    """Replace a connectivity service by UUID (full replacement)."""
    ctx = request.app.state.context
    raw = body.get("tapi-connectivity:connectivity-service", body)
    svc = ConnectivityService.model_validate(raw)
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
    ctx.delete_service(uuid)
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
    ctx = request.app.state.context
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
