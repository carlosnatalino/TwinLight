"""TAPI Equipment API endpoints.

GET /data/tapi-equipment:equipment-context
GET /data/tapi-equipment:equipment-context/equipment
GET /data/tapi-equipment:equipment-context/equipment={uuid}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/data", tags=["tapi-equipment"])

_BASE = "/tapi-equipment:equipment-context"


@router.get(_BASE)
async def get_equipment_context(request: Request) -> dict:
    """Return equipment context with all equipment."""
    ctx = request.app.state.context
    equipment = ctx.get_equipment_list()
    return {
        "tapi-equipment:equipment-context": {
            "uuid": "equipment-context",
            "equipment": equipment,
        }
    }


@router.get(f"{_BASE}/equipment")
async def get_equipment_list(request: Request) -> dict:
    """Return all equipment (GNPy elements or parsed topology)."""
    ctx = request.app.state.context
    equipment = ctx.get_equipment_list()
    return {
        "tapi-equipment:equipment-context": {
            "equipment": equipment,
        }
    }


@router.get(f"{_BASE}/equipment={{uuid}}")
async def get_equipment(uuid: str, request: Request) -> dict:
    """Return a single equipment by UUID (GNPy element UID)."""
    ctx = request.app.state.context
    equipment = ctx.get_equipment_list()
    for eq in equipment:
        if eq.get("uuid") == uuid:
            return {"tapi-equipment:equipment": [eq]}
    raise HTTPException(status_code=404, detail=f"Equipment {uuid} not found")
