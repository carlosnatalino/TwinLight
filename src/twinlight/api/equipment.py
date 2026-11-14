"""T-API v2.6.0 physical inventory (``tapi-equipment:physical-context``).

GET /data/tapi-equipment:physical-context
GET …/device
GET …/device={uuid}
GET …/device={uuid}/equipment={uuid}
GET …/physical-span
GET …/physical-span={uuid}

v2.6.0 has no ``equipment-context``; ``tapi-equipment`` augments the context
with ``physical-context``. How GNPy elements map onto devices, equipment and
spans is described in ``models/equipment.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/data", tags=["tapi-equipment"])

_BASE = "/tapi-equipment:physical-context"


def _dump(model) -> dict:
    return model.model_dump(by_alias=True)


@router.get(_BASE)
async def get_physical_context(request: Request) -> dict:
    """Every device (with its equipment) and every physical span."""
    return {"tapi-equipment:physical-context": _dump(request.app.state.context.physical_context())}


@router.get(f"{_BASE}/device")
async def get_devices(request: Request) -> dict:
    devices = request.app.state.context.physical_context().device
    return {"tapi-equipment:device": [_dump(d) for d in devices]}


@router.get(f"{_BASE}/device={{uuid}}")
async def get_device(uuid: str, request: Request) -> dict:
    for device in request.app.state.context.physical_context().device:
        if device.uuid == uuid:
            return {"tapi-equipment:device": _dump(device)}
    raise HTTPException(status_code=404, detail=f"device {uuid} not found")


@router.get(f"{_BASE}/device={{device_uuid}}/equipment={{uuid}}")
async def get_equipment(device_uuid: str, uuid: str, request: Request) -> dict:
    for device in request.app.state.context.physical_context().device:
        if device.uuid != device_uuid:
            continue
        for equipment in device.equipment:
            if equipment.uuid == uuid:
                return {"tapi-equipment:equipment": _dump(equipment)}
        raise HTTPException(status_code=404, detail=f"equipment {uuid} not found")
    raise HTTPException(status_code=404, detail=f"device {device_uuid} not found")


@router.get(f"{_BASE}/physical-span")
async def get_physical_spans(request: Request) -> dict:
    spans = request.app.state.context.physical_context().physical_span
    return {"tapi-equipment:physical-span": [_dump(s) for s in spans]}


@router.get(f"{_BASE}/physical-span={{uuid}}")
async def get_physical_span(uuid: str, request: Request) -> dict:
    for span in request.app.state.context.physical_context().physical_span:
        if span.uuid == uuid:
            return {"tapi-equipment:physical-span": _dump(span)}
    raise HTTPException(status_code=404, detail=f"physical-span {uuid} not found")
