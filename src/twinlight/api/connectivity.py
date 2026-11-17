"""T-API v2.6.0 connectivity (``tapi-connectivity:connectivity-context``).

GET    /data/tapi-common:context/tapi-connectivity:connectivity-context
GET    …/connectivity-service
POST   …/connectivity-service
GET    …/connectivity-service={uuid}
PUT    …/connectivity-service={uuid}
PATCH  …/connectivity-service={uuid}
DELETE …/connectivity-service={uuid}

``tapi-connectivity`` augments ``/tapi-common:context`` with
``connectivity-context``, so RFC 8040 §3.5.3 puts its data resource under
the context. Every route is also served, hidden from OpenAPI, at the short
``/data/tapi-connectivity:connectivity-context/…`` of earlier releases (see
``CONTAINER_PATHS``).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from twinlight.algorithms.path_constraints import ConstraintError
from twinlight.models.common import Direction, ForwardingDirection, LayerProtocolName
from twinlight.models.connectivity import (
    MCG_CSEP_SPEC,
    OTSIA_CSEP_SPEC,
    ConnectivityService,
    UpdateConnectivityServiceRequest,
)
from twinlight.state.context import (
    InsufficientQoTError,
    InsufficientSpectrumError,
    NoPathError,
    RmsaError,
    SpectrumRequestError,
)

router = APIRouter(prefix="/data", tags=["tapi-connectivity"])

#: Where the container is served, canonical first: the augment's RESTCONF
#: path (RFC 8040 §3.5.3: a node is module-qualified where its module
#: differs from its parent's), then the short alias earlier releases served.
#: The routes below are relative to the container and mounted at each, at the
#: end of this module; the alias is hidden from OpenAPI.
CONTAINER_PATHS = (
    "/tapi-common:context/tapi-connectivity:connectivity-context",
    "/tapi-connectivity:connectivity-context",
)

# The container's routes, relative to it; mounted on ``router`` below.
_container = APIRouter()


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


# What the twin implements of a connectivity-service request, level by level
# (tapi-connectivity and tapi-photonic-media v2.6.0). A standard leaf the twin
# does not act on is refused rather than ignored: admitting the service
# without it would hand the client a lightpath that does not honour what it
# asked for.
#
# _UNIMPLEMENTED: standard leaves refused whenever present.
# _FIXED: standard leaves the twin supports at one value only (two for the
#   end-point direction); any other value is refused. Each is reported at its
#   value, so a GET body sent back as a PUT passes.
# Leaves listed in neither are acted on (uuid, name, end-point, the routing
# and topology constraints, administrative-state, the channel leaves, …) or
# are config false (operational-state, lifecycle-state, connection, csep-role,
# connection-end-point): state the server reports and ignores on input.
_UNIMPLEMENTED: dict[str, tuple[str, ...]] = {
    "connectivity-service": (
        "layer-protocol-qualifier", "connectivity-constraint", "resilience-constraint",
        "connectivity-service", "internal-point", "connectivity-protection-service",
    ),
    "end-point": (
        "layer-protocol-qualifier", "role", "protection-role", "capacity",
        "administrative-state", "peer-fwd-connectivity-service-end-point",
        "server-connectivity-service-end-point",
        "protecting-connectivity-service-end-point",
        "assembled-connectivity-service-end-point",
        "profile", "sink-profile", "source-profile",
    ),
    "layer-protocol-constraint": (
        "layer-protocol-qualifier", "name",
        "tapi-photonic-media:otsi-mcg-connectivity-service-end-point-spec",
    ),
    OTSIA_CSEP_SPEC: (
        "total-power-warn-threshold-upper", "total-power-warn-threshold-lower",
    ),
    "otsi-config": (
        "name", "application-identifier", "laser-control",
        "otsi-threshold-power-config", "power-management-config-pac",
    ),
}
# A lightpath runs from its SOURCE end-point to its SINK over one-way fibres,
# so the service is UNIDIRECTIONAL (TapiContext.add_service). Each entry is
# (the values the twin supports, the values the YANG type admits): a value
# outside the type is invalid (422), a valid one the twin does not support is
# not implemented (501).
_LAYER_PROTOCOL_NAMES = frozenset(v.value for v in LayerProtocolName)
_FIXED: dict[str, dict[str, tuple[tuple[object, ...], Callable[[object], bool]]]] = {
    "connectivity-service": {
        "layer-protocol-name": (("PHOTONIC_MEDIA",), _LAYER_PROTOCOL_NAMES.__contains__),
        "direction": (("UNIDIRECTIONAL",), {v.value for v in ForwardingDirection}.__contains__),
    },
    "end-point": {
        "layer-protocol-name": (("PHOTONIC_MEDIA",), _LAYER_PROTOCOL_NAMES.__contains__),
        "direction": (("SOURCE", "SINK"), {v.value for v in Direction}.__contains__),
    },
    "layer-protocol-constraint": {
        "layer-protocol-name": (("PHOTONIC_MEDIA",), _LAYER_PROTOCOL_NAMES.__contains__),
    },
    OTSIA_CSEP_SPEC: {
        "number-of-otsi": (
            (1,), lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
        ),
    },
}


def _entries(value: object) -> list[dict]:
    """The object entries of a YANG list in a request (a lone object too)."""
    items = value if isinstance(value, list) else [value]
    return [item for item in items if isinstance(item, dict)]


def _unimplemented(raw: dict) -> tuple[list[str], list[str]]:
    """``(unimplemented, invalid)`` leaves of a connectivity-service request.

    Unimplemented: standard leaves the twin does not act on, and valid
    values the twin does not support (with the values it does). Invalid:
    values the leaf's YANG type does not admit.
    """
    found: list[str] = []
    invalid: list[str] = []

    def check(level: str, node: dict, at: str) -> None:
        found.extend(f"{at}{key}" for key in _UNIMPLEMENTED[level] if key in node)
        for key, (allowed, valid) in _FIXED.get(level, {}).items():
            if key not in node or node[key] in allowed:
                continue
            value = node[key]
            if not isinstance(value, (str, int)) or not valid(value):
                invalid.append(f"{at}{key}={value!r}")
            else:
                supported = " or ".join(str(v) for v in allowed)
                found.append(f"{at}{key}={value!r} (supported: {supported})")

    check("connectivity-service", raw, "")
    for ep in _entries(raw.get("end-point") or []):
        ep_at = f"end-point[{ep.get('local-id')}]/"
        check("end-point", ep, ep_at)
        for lpc in _entries(ep.get("layer-protocol-constraint") or []):
            lpc_at = f"{ep_at}layer-protocol-constraint[{lpc.get('local-id')}]/"
            check("layer-protocol-constraint", lpc, lpc_at)
            for spec in _entries(lpc.get(OTSIA_CSEP_SPEC) or []):
                spec_at = f"{lpc_at}{OTSIA_CSEP_SPEC}/"
                check(OTSIA_CSEP_SPEC, spec, spec_at)
                for cfg in _entries(spec.get("otsi-config") or []):
                    check("otsi-config", cfg, f"{spec_at}otsi-config[{cfg.get('local-id')}]/")
    return found, invalid


def _parse_service(body: dict) -> ConnectivityService:
    """Unwrap and validate a connectivity-service request body.

    The body is validated by hand rather than through a FastAPI parameter
    annotation (the envelope key and the list-or-object spelling both have
    to be resolved first), so pydantic's error has to be mapped to a 422
    here — otherwise a malformed payload surfaces as a 500.

    Raises:
        HTTPException: 501 (``operation-not-supported``) when the body
            carries a standard leaf the twin does not implement, naming
            each (see ``_UNIMPLEMENTED`` and ``_FIXED``); 422 when it does
            not validate.
    """
    raw = _single_service(body)
    refused, invalid = _unimplemented(raw)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid value: " + "; ".join(invalid),
        )
    if refused:
        hint = (
            ". A lightpath runs one way: mark the head end-point SOURCE and the "
            "tail SINK (or leave direction out: the first end-point is the "
            "head), and create a second service for the reverse direction"
            if any("direction='BIDIRECTIONAL'" in r for r in refused) else ""
        )
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "not implemented by this twin: " + "; ".join(refused)
                + ". Remove these from the connectivity-service request" + hint
            ),
        )
    # operational-state is config false and the twin's to decide (from the
    # QoT): a client value is ignored, not stored.
    raw = {key: value for key, value in raw.items() if key != "operational-state"}
    try:
        svc = ConnectivityService.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(
                f"{'/'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}"
                for e in exc.errors()
            ),
        ) from exc
    if len(svc.end_point) != 2:
        # A lightpath has a head and a tail; a third end-point would be
        # silently ignored by admission.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "a connectivity-service is a point-to-point lightpath: it needs "
                f"exactly two end-points, got {len(svc.end_point)}"
            ),
        )
    return svc


@_container.post(
    "/connectivity-service",
    status_code=status.HTTP_201_CREATED,
)
async def create_service(body: dict, request: Request) -> dict:
    """Create a new connectivity service between two SIPs."""
    ctx = request.app.state.context

    svc = _parse_service(body)
    if ctx.get_service(svc.uuid) is not None:
        # RFC 8040 §4.4.1: POST must not overwrite an existing resource.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"connectivity-service {svc.uuid} already exists; replace it "
                "with PUT or delete it first"
            ),
        )

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
    except (ConstraintError, SpectrumRequestError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
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
    """Serialise a connectivity-service with its assigned spectrum.

    Spectrum is live allocation state rather than something the client sent,
    so it is merged in here instead of being carried on the model — the same
    reason the SIP's spectrum capability is assembled per request.

    It goes where T-API v2.6.0 puts it, on the end-point's
    layer-protocol-constraint alongside the modulation augment;
    ``tapi-connectivity`` has no ``frequency-slot`` leaf of its own.
    """
    payload = svc.model_dump(by_alias=True)
    spec = ctx.service_spectrum_spec(svc.uuid)
    if spec is not None:
        for end_point in payload.get("end-point", []):
            for constraint in end_point.get("layer-protocol-constraint", []):
                constraint[MCG_CSEP_SPEC] = spec
    return payload


async def connectivity_context_payload(ctx) -> dict:
    """The ``tapi-connectivity:connectivity-context`` container.

    One function for both views of it, this module's list GET and the full
    context GET (``api/common.py``), so the two cannot disagree. Assembled
    per request: each service carries live spectrum and an
    operational-state that follows the current QoT.
    """
    # operational-state follows the current QoT: re-evaluate what a change
    # (a parameter write, a failure, a neighbour) invalidated.
    await ctx.refresh_operational_states()
    return {
        "connectivity-service": [
            _connectivity_service_payload(ctx, s) for s in ctx.get_services()
        ]
    }


@_container.get("")
async def get_connectivity_context(request: Request) -> dict:
    """The whole connectivity context: every service, with live spectrum."""
    ctx = request.app.state.context
    return {
        "tapi-connectivity:connectivity-context": await connectivity_context_payload(ctx)
    }


@_container.get("/connectivity-service")
async def get_services(request: Request) -> dict:
    """Return all connectivity services, with assigned spectrum when held.

    Answers with the container's envelope, the same body as the container
    GET, as it always has: the UI, the client library and the ONOS adapter
    read it that way.
    """
    return await get_connectivity_context(request)


@_container.get("/connectivity-service={uuid}")
async def get_service(uuid: str, request: Request) -> dict:
    """Return one connectivity service, with assigned spectrum when held."""
    ctx = request.app.state.context
    svc = ctx.get_service(uuid)
    if svc is None:
        raise HTTPException(
            status_code=404, detail=f"Service {uuid} not found"
        )
    await ctx.get_or_compute_baseline(svc)  # current operational-state
    return {
        "tapi-connectivity:connectivity-service": _connectivity_service_payload(ctx, svc)
    }


@_container.put("/connectivity-service={uuid}")
async def replace_service(uuid: str, body: dict, request: Request) -> dict:
    """Replace a connectivity service by UUID (full replacement).

    The replacement goes through admission with the old service's resources
    released. If it is refused, the old service is put back exactly as it
    was (``TapiContext.replace_service``): a failed replace never moves or
    tears down the service that was there.
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
    # A refused replacement leaves the old service exactly as it was (same
    # route and block); with no old service, a PUT creates one.
    try:
        if ctx.get_service(uuid) is None:
            await ctx.add_service(svc)
        else:
            await ctx.replace_service(svc)
    except (RmsaError, ConstraintError, SpectrumRequestError) as e:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
                if isinstance(e, (ConstraintError, SpectrumRequestError))
                else status.HTTP_409_CONFLICT
            ),
            detail=str(e),
        ) from e
    return {
        "tapi-connectivity:connectivity-service": _connectivity_service_payload(ctx, svc)
    }


@_container.patch("/connectivity-service={uuid}")
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
    await ctx.get_or_compute_baseline(svc)  # current operational-state
    return {
        "tapi-connectivity:connectivity-service": _connectivity_service_payload(
            ctx, svc
        )
    }


@_container.delete(
    "/connectivity-service={uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_service(uuid: str, request: Request) -> None:
    """Delete a connectivity service."""
    ctx = request.app.state.context
    # The lightpaths that shared a fiber with it lose a neighbour;
    # remove_service brings their operational-state up to date.
    if not await ctx.remove_service(uuid):
        raise HTTPException(
            status_code=404, detail=f"Service {uuid} not found"
        )


# After every route is declared: include_router copies the routes it is given.
for _prefix, _documented in zip(CONTAINER_PATHS, (True, False), strict=True):
    router.include_router(_container, prefix=_prefix, include_in_schema=_documented)
