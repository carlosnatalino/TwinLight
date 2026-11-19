"""TAPI Common API endpoints.

GET {root}/data/tapi-common:context
GET {root}/data/tapi-common:context/service-interface-point
GET {root}/data/tapi-common:context/service-interface-point={uuid}

``{root}`` is ``server.restconf_root`` (``/restconf`` by default): app.py
mounts every T-API router under it, and only there.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from twinlight.api.connectivity import connectivity_context_payload
from twinlight.api.equipment import physical_context_payload
from twinlight.api.oam import oam_context_payload
from twinlight.api.path_computation import path_computation_context_payload
from twinlight.api.streaming import stream_context_payload
from twinlight.models.yang_numbers import SIP_PATH, encode_64bit_leaves

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

    Its band edges are uint64 Hz, JSON strings per RFC 7951 §6.1.
    """
    payload = sip.model_dump(by_alias=True)
    payload[PHOTONIC_SIP_SPEC] = ctx.sip_photonic_spec(sip.uuid)
    encoded: dict = encode_64bit_leaves(payload, SIP_PATH)
    return encoded


@router.get("/tapi-common:context")
async def get_context(request: Request) -> dict:
    """Return the full TAPI context, with every augment the twin serves.

    A RESTCONF GET of a container returns its whole subtree, augments
    included (RFC 8040 §4.3; the default depth is unbounded, §4.8.2), and
    a T-API client reads it once for its initial sync. Each augment is
    built by the same code as its own module's GET, so the two views
    cannot disagree.
    """
    ctx = request.app.state.context
    # The only await, so it goes first: everything below runs without
    # yielding, and a service admitted concurrently cannot appear in the
    # SIPs' available spectrum but not in the connectivity services.
    connectivity = await connectivity_context_payload(ctx)
    return {
        "tapi-common:context": {
            "uuid": "tapi-context",
            "service-interface-point": [
                _sip_payload(ctx, sip) for sip in ctx.get_sips()
            ],
            "tapi-topology:topology-context": ctx.topology_context.model_dump(
                by_alias=True
            ),
            "tapi-connectivity:connectivity-context": connectivity,
            "tapi-path-computation:path-computation-context": (
                path_computation_context_payload(ctx)
            ),
            "tapi-equipment:physical-context": physical_context_payload(ctx),
            "tapi-oam:oam-context": oam_context_payload(ctx),
            "tapi-streaming:stream-context": stream_context_payload(request),
        }
    }


#: The SIP list's member name. RFC 8040 §4.3 returns the target resource
#: itself, and RFC 7951 §4 names a top-level member with its module:
#: "A namespace-qualified member name MUST be used for all members of a
#: top-level JSON object".
SIP_KEY = "tapi-common:service-interface-point"


@router.get("/tapi-common:context/service-interface-point")
async def get_sips(request: Request) -> dict:
    """Every service interface point, as the body's one member (RFC 8040 §4.3)."""
    ctx = request.app.state.context
    return {SIP_KEY: [_sip_payload(ctx, sip) for sip in ctx.get_sips()]}


@router.get("/tapi-common:context/service-interface-point={uuid}")
async def get_sip(uuid: str, request: Request) -> dict:
    """One service interface point: the list entry, an array of one.

    RFC 7951 §5.4: "A list instance is encoded as a name/array pair", with
    no exception for a single entry (RFC 8040 §4.5 shows the same for an
    entry it PUTs).
    """
    ctx = request.app.state.context
    sip = ctx.get_sip(uuid)
    if sip is None:
        raise HTTPException(status_code=404, detail=f"SIP {uuid} not found")
    return {SIP_KEY: [_sip_payload(ctx, sip)]}
