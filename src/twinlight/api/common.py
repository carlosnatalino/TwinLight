"""TAPI Common API endpoints.

GET {root}/data/tapi-common:context
PUT {root}/data/tapi-common:context
GET {root}/data/tapi-common:context/service-interface-point
GET {root}/data/tapi-common:context/service-interface-point={uuid}

``{root}`` is ``server.restconf_root`` (``/restconf`` by default): app.py
mounts every T-API router under it, and only there.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from twinlight.api import connectivity, path_computation
from twinlight.api.connectivity import connectivity_context_payload
from twinlight.api.equipment import physical_context_payload
from twinlight.api.oam import oam_context_payload
from twinlight.api.path_computation import path_computation_context_payload
from twinlight.api.restconf import RestconfError, refuse_unknown_members, target_member
from twinlight.api.streaming import stream_context_payload
from twinlight.models import tapi_schema
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


CONTEXT_PATH = "/tapi-common:context"
CONTEXT_SCHEMA = tapi_schema.node(CONTEXT_PATH)

#: The context's configuration the twin derives rather than takes from a
#: client: its uuid, the topologies (from the GNPy topology), the OAM jobs
#: (one per service, made and removed with it) and the physical and stream
#: contexts (config true containers that hold only state). The other config
#: true members are the two lists a client writes.
DERIVED = (
    "uuid",
    "name",
    "tapi-topology:topology-context",
    "tapi-oam:oam-context",
    "tapi-equipment:physical-context",
    "tapi-streaming:stream-context",
)


async def replace_context(request: Request, body: Any) -> None:
    """Replace the context's configuration with ``body``, or change nothing.

    The body is the complete new context (RFC 8040 §4.5). What a client
    writes in it is the service and path-comp-service lists, replaced as a
    PUT of their containers replaces them, both in one critical section
    (``connectivity.replace_lists``). The configuration the twin derives
    (``DERIVED``) may be repeated, and must be repeated unchanged: RFC 8040
    §4.5 answers a client "not authorized to create or replace the target
    resource" with 403 ``access-denied``, and RFC 8341 §3.4.5 decides what a
    replace asks of each node — "unaltered data nodes are ignored by the
    server and do not require any access rights", while one the operation
    would modify or delete (an omitted one included) is ``access-denied``.
    Configuration is compared without its state (``tapi_schema.config_view``),
    so the context as its GET returned it passes; an OAM job made or removed
    with a service the same PUT adds or deletes is not the client's write.
    Config-false members (the SIPs, the computed paths, states) are ignored.

    Raises:
        RestconfError: 400 for a member v2.6.0 does not define, 403 for a
            change to derived configuration, and whatever a PUT of either
            list container would answer.
    """
    ctx = request.app.state.context
    if not isinstance(body, dict):
        raise RestconfError(400, "bad-element", "the context must be a JSON object",
                            error_path=CONTEXT_PATH)
    refuse_unknown_members(body, CONTEXT_SCHEMA, CONTEXT_PATH)
    current = (await get_context(request))["tapi-common:context"]

    def derived(context: dict) -> Any:
        return tapi_schema.config_view(
            {m: context[m] for m in DERIVED if m in context}, CONTEXT_SCHEMA,
        )

    sent, held = derived(body), derived(current)
    if sent != held:
        changed = [m for m in DERIVED if sent.get(m) != held.get(m)]
        raise RestconfError(
            403, "access-denied",
            "a PUT may repeat the configuration the twin derives, unchanged, but "
            f"not change or omit it: {', '.join(changed)} differ from what the twin "
            "holds. Send them as a GET returns them, or PUT the "
            "tapi-connectivity:connectivity-context or "
            "tapi-path-computation:path-computation-context container instead",
            error_path=f"{CONTEXT_PATH}/{changed[0]}",
        )
    services, keep = connectivity.plan_connectivity_context(
        ctx, body.get("tapi-connectivity:connectivity-context", {}),
    )
    pcs, keep_pcs = path_computation.plan_path_computation_context(
        ctx, body.get("tapi-path-computation:path-computation-context", {}),
    )
    await connectivity.replace_lists(
        ctx, services=services, keep_services=keep,
        path_comp_services=pcs, keep_path_comp_services=keep_pcs,
    )


@router.put("/tapi-common:context", status_code=status.HTTP_204_NO_CONTENT)
async def put_context(body: dict, request: Request) -> Response:
    """Replace the whole context (RFC 8040 §4.5): every connectivity-service
    and path-comp-service, all or nothing (see :func:`replace_context`).
    204: the context always exists."""
    await replace_context(request, target_member(body, "tapi-common:context"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
