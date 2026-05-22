"""TAPI Path Computation API endpoints.

POST .../path-computation-service/compute-path
GET  /data/tapi-path-computation:path-computation-context
GET  .../path-computation-service
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from tapi_twin.models.path_computation import (
    ComputePathRequest,
    LinkRef,
    NodeRef,
    PathCandidate,
)

router = APIRouter(prefix="/data", tags=["tapi-path-computation"])

_BASE = "/tapi-path-computation:path-computation-context"
_SVC = f"{_BASE}/path-computation-service"


@router.get(_BASE)
async def get_path_computation_context(_request: Request) -> dict:
    """Return path computation context (single default service)."""
    return {
        "tapi-path-computation:path-computation-context": {
            "uuid": "path-computation-context",
            "path-computation-service": [
                {
                    "uuid": "default-path-computation-service",
                    "name": [{"value-name": "service-name", "value": "default"}],
                }
            ],
        }
    }


@router.get(f"{_BASE}/path-computation-service")
async def get_path_computation_services(_request: Request) -> dict:
    """Return path computation services (default service)."""
    return {
        "tapi-path-computation:path-computation-context": {
            "path-computation-service": [
                {
                    "uuid": "default-path-computation-service",
                    "name": [{"value-name": "service-name", "value": "default"}],
                }
            ],
        }
    }


@router.post(f"{_SVC}/compute-path")
async def compute_path(body: dict, request: Request) -> dict:
    """Compute path(s) between two SIPs.

    Uses GNPy when available, else NetworkX k-shortest.
    """
    ctx = request.app.state.context

    raw = body.get("tapi-path-computation:input", body)
    req = ComputePathRequest.model_validate(raw)

    ep0 = req.end_point[0].service_interface_point.service_interface_point_uuid
    ep1 = req.end_point[1].service_interface_point.service_interface_point_uuid

    if ctx.get_sip(ep0) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"SIP {ep0!r} not found",
        )
    if ctx.get_sip(ep1) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"SIP {ep1!r} not found",
        )

    candidates = ctx.get_path_candidates(ep0, ep1, req.max_candidates)
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No path found between the specified end-points",
        )

    path_list = []
    for link_refs, node_refs in candidates:
        path_list.append(
            PathCandidate(
                link=[
                    LinkRef(topology_uuid=t, link_uuid=lk)
                    for t, lk in link_refs
                ],
                node=[
                    NodeRef(topology_uuid=t, node_uuid=n)
                    for t, n in node_refs
                ],
            )
        )

    return {
        "tapi-path-computation:output": {
            "path": [p.model_dump(by_alias=True) for p in path_list],
        }
    }
