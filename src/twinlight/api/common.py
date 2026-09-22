"""TAPI Common API endpoints.

GET /data/tapi-common:context
GET /data/tapi-common:context/service-interface-point
GET /data/tapi-common:context/service-interface-point={uuid}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/data", tags=["tapi-common"])

# tapi-photonic-media augments /tapi-common:context/service-interface-point
# with this container. Module-qualified per RFC 7951 §4, because it is
# defined outside tapi-common.
PHOTONIC_SIP_SPEC = (
    "tapi-photonic-media:photonic-media-service-interface-point-spec"
)


def _sip_payload(ctx, sip) -> dict:
    """Serialise a SIP with its photonic-media augment.

    The augment carries live spectrum occupancy, so it is assembled per
    request rather than stored on the model — a SIP that cached it would
    hand out a stale ``available-spectrum`` after the next service is
    admitted, and would carry it into snapshots.
    """
    payload = sip.model_dump(by_alias=True)
    payload[PHOTONIC_SIP_SPEC] = ctx.sip_photonic_spec(sip.uuid)
    return payload


@router.get("/tapi-common:context")
async def get_context(request: Request) -> dict:
    """Return the full TAPI context."""
    ctx = request.app.state.context
    return {
        "tapi-common:context": {
            "uuid": "tapi-context",
            "service-interface-point": [
                _sip_payload(ctx, sip) for sip in ctx.get_sips()
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
                _sip_payload(ctx, sip) for sip in ctx.get_sips()
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
    return {
        "tapi-common:context": {
            "service-interface-point": [_sip_payload(ctx, sip)]
        }
    }
