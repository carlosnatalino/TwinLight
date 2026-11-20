"""T-API v2.6.0 physical inventory (``tapi-equipment:physical-context``).

GET {root}/data/tapi-common:context/tapi-equipment:physical-context
GET …/device
GET …/device={uuid}
GET …/device={uuid}/equipment={uuid}
GET …/physical-span
GET …/physical-span={uuid}

v2.6.0 has no ``equipment-context``; ``tapi-equipment`` augments the context
with ``physical-context``. How GNPy elements map onto devices, equipment and
spans is described in ``models/equipment.py``.

Being an augment of ``/tapi-common:context``, the container's RESTCONF path
is under the context (RFC 8040 §3.5.3).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from twinlight.models.yang_numbers import PHYSICAL_CONTEXT_PATH, encode_64bit_leaves

#: The container's RESTCONF path: an augment of tapi-common:context, so
#: module-qualified where the module changes (RFC 8040 §3.5.3).
PREFIX = "/data/tapi-common:context/tapi-equipment:physical-context"

router = APIRouter(prefix=PREFIX, tags=["tapi-equipment"])


#: Schema paths of the entries this module returns (RFC 7951 names, list
#: keys left out), for the 64-bit leaves under them.
DEVICE_PATH = f"{PHYSICAL_CONTEXT_PATH}/device"
EQUIPMENT_PATH = f"{DEVICE_PATH}/equipment"
SPAN_PATH = f"{PHYSICAL_CONTEXT_PATH}/physical-span"


def _dump(model, path: str) -> dict:
    """A fresh JSON-ready dict of ``model``, whose schema path is ``path``.

    The tree it comes from is shared by every request
    (``TapiContext.physical_context``) and immutable, its lists held as
    tuples, which JSON mode turns back into lists. An equipment's
    ``geolocation`` leaves are decimal64, so JSON strings (RFC 7951 §6.1).
    """
    encoded: dict = encode_64bit_leaves(model.model_dump(by_alias=True, mode="json"), path)
    return encoded


def physical_context_payload(ctx) -> dict:
    """The ``tapi-equipment:physical-context`` container.

    One function for both views of it, this module's GET and the full
    context GET (``api/common.py``), so the two cannot disagree.
    """
    return _dump(ctx.physical_context(), PHYSICAL_CONTEXT_PATH)


@router.get("")
async def get_physical_context(request: Request) -> dict:
    """Every device (with its equipment) and every physical span."""
    return {
        "tapi-equipment:physical-context": physical_context_payload(request.app.state.context)
    }


@router.get("/device")
async def get_devices(request: Request) -> dict:
    devices = request.app.state.context.physical_context().device
    return {"tapi-equipment:device": [_dump(d, DEVICE_PATH) for d in devices]}


@router.get("/device={uuid}")
async def get_device(uuid: str, request: Request) -> dict:
    for device in request.app.state.context.physical_context().device:
        if device.uuid == uuid:
            # A list entry is an array of one (RFC 7951 §5.4).
            return {"tapi-equipment:device": [_dump(device, DEVICE_PATH)]}
    raise HTTPException(status_code=404, detail=f"device {uuid} not found")


@router.get("/device={device_uuid}/equipment={uuid}")
async def get_equipment(device_uuid: str, uuid: str, request: Request) -> dict:
    for device in request.app.state.context.physical_context().device:
        if device.uuid != device_uuid:
            continue
        for equipment in device.equipment:
            if equipment.uuid == uuid:
                return {"tapi-equipment:equipment": [_dump(equipment, EQUIPMENT_PATH)]}
        raise HTTPException(status_code=404, detail=f"equipment {uuid} not found")
    raise HTTPException(status_code=404, detail=f"device {device_uuid} not found")


@router.get("/physical-span")
async def get_physical_spans(request: Request) -> dict:
    spans = request.app.state.context.physical_context().physical_span
    return {"tapi-equipment:physical-span": [_dump(s, SPAN_PATH) for s in spans]}


@router.get("/physical-span={uuid}")
async def get_physical_span(uuid: str, request: Request) -> dict:
    for span in request.app.state.context.physical_context().physical_span:
        if span.uuid == uuid:
            return {"tapi-equipment:physical-span": [_dump(span, SPAN_PATH)]}
    raise HTTPException(status_code=404, detail=f"physical-span {uuid} not found")
