"""TAPI Common API endpoints.

GET /data/tapi-common:context
GET /data/tapi-common:context/service-interface-point
GET /data/tapi-common:context/service-interface-point={uuid}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/data", tags=["tapi-common"])


@router.get("/tapi-common:context")
async def get_context(request: Request) -> dict:
    """Return the full TAPI context."""
    ctx = request.app.state.context
    return {
        "tapi-common:context": {
            "uuid": "tapi-context",
            "service-interface-point": [
                sip.model_dump(by_alias=True) for sip in ctx.get_sips()
            ],
            "tapi-topology:topology-context": ctx.topology_context.model_dump(
                by_alias=True
            ),
        }
    }


@router.get("/tapi-common:context/service-interface-point")
async def get_sips(request: Request) -> dict:
    """Return all service interface points."""
    ctx = request.app.state.context
    return {
        "tapi-common:context": {
            "service-interface-point": [
                sip.model_dump(by_alias=True) for sip in ctx.get_sips()
            ],
        }
    }


@router.get("/tapi-common:context/service-interface-point={uuid}")
async def get_sip(uuid: str, request: Request) -> dict:
    """Return a specific service interface point."""
    ctx = request.app.state.context
    sip = ctx.get_sip(uuid)
    if sip is None:
        raise HTTPException(status_code=404, detail=f"SIP {uuid} not found")
    return {"tapi-common:context": {"service-interface-point": [sip.model_dump(by_alias=True)]}}
