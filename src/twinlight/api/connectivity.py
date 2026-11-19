"""T-API v2.6.0 connectivity (``tapi-connectivity:connectivity-context``).

GET    {root}/data/tapi-common:context/tapi-connectivity:connectivity-context
POST   {root}/data/tapi-common:context/tapi-connectivity:connectivity-context
GET    …/connectivity-service
GET    …/connectivity-service={uuid}
PUT    …/connectivity-service={uuid}
PATCH  …/connectivity-service={uuid}
DELETE …/connectivity-service={uuid}

``tapi-connectivity`` augments ``/tapi-common:context`` with
``connectivity-context``, so RFC 8040 §3.5.3 puts its data resource under
the context, beside the topology, OAM and stream contexts.

A service is created by POST on the container, its parent, as RFC 8040
§4.4.1 specifies; the list path takes no POST.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from twinlight.algorithms.path_constraints import ConstraintError, UnresolvedReferenceError
from twinlight.api.restconf import (
    RestconfError,
    created_location,
    refuse_unknown_members,
    validation_error_tag,
)
from twinlight.models import tapi_schema
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

#: The container's RESTCONF path: an augment of tapi-common:context, so
#: module-qualified where the module changes (RFC 8040 §3.5.3).
PREFIX = "/data/tapi-common:context/tapi-connectivity:connectivity-context"

router = APIRouter(prefix=PREFIX, tags=["tapi-connectivity"])


def _single_service(body: dict) -> dict:
    """Unwrap one connectivity-service from a RESTCONF request body.

    ``connectivity-service`` is a YANG *list*, so RFC 7951 §5.4 encodes it
    as a name/array pair and RFC 8040 Appendix B.2.1 creates a single entry
    with an array of one. A bare object is the other spelling seen in the
    wild. Both are accepted; more than one entry is not, because the caller
    below admits exactly one service.
    """
    if "tapi-connectivity:connectivity-service" in body:
        extra = sorted(set(body) - {"tapi-connectivity:connectivity-service"})
        if extra:
            # §4.4.1 again, and RFC 6241's "An unexpected element is present".
            raise RestconfError(
                status.HTTP_400_BAD_REQUEST, "unknown-element",
                "the body carries one connectivity-service and nothing beside "
                f"it; unexpected: {', '.join(extra)}",
                error_path="/", error_info={"bad-element": extra[0]},
            )
    raw = body.get("tapi-connectivity:connectivity-service", body)
    if isinstance(raw, list):
        if len(raw) != 1:
            # RFC 8040 §4.4.1: "The message-body MUST contain exactly one
            # instance of the expected data resource."
            raise RestconfError(
                status.HTTP_400_BAD_REQUEST, "bad-element",
                f"Expected exactly one connectivity-service, got {len(raw)}",
            )
        raw = raw[0]
    if not isinstance(raw, dict):
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "bad-element",
            "connectivity-service must be an object or a list of one",
        )
    return raw


#: Advice for members clients are known to send that v2.6.0 does not
#: define: this project's pre-2.6 modulation leaf, and the T-API 2.1 leaves
#: ONOS sends (the ONOS adapter drops them; see integrations/onos).
_HINTS = {
    "modulation-format": (
        "not a T-API v2.6.0 member: send the modulation as end-point/"
        f"layer-protocol-constraint/{OTSIA_CSEP_SPEC}/otsi-config/modulation/"
        "standard-modulation-technique, e.g. 'MT_DP-QPSK'"
    ),
    "service-layer": "a T-API 2.1 member; v2.6.0 has layer-protocol-name",
    "service-type": "a T-API 2.1 member that v2.6.0 does not define",
}

#: The connectivity-service list in the v2.6.0 data-node tree, and the
#: instance path of its entries (RFC 7950 §9.13), for error-path.
SERVICE_PATH = "/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"
SERVICE_SCHEMA = tapi_schema.node(SERVICE_PATH)

# What the twin implements of a connectivity-service request, level by level
# (tapi-connectivity and tapi-photonic-media v2.6.0): each member it acts on,
# with what it acts on below it (None: the whole subtree, checked by its own
# parser — the name lists, the constraints in algorithms/path_constraints.py,
# the spectrum in models/connectivity.py). Every other configuration member
# the YANG defines is refused (501) rather than ignored: admitting the
# service without it would hand the client a lightpath that does not honour
# what it asked for. That covers connectivity-constraint,
# resilience-constraint, the layer-protocol qualifiers, the end-point role,
# capacity and protection-role, the OTSi power and laser controls, the MCG
# bandwidth and flexi-grid pacs and the tapi-oam service augments. A member
# the YANG does not define is a 400 (unknown-element) before any of this.
# Config-false members (operational-state, connection, csep-role,
# connection-end-point) are state the server reports: an edit ignores them.
# _FIXED below names the leaves the twin supports at one value only (two for
# the end-point direction); any other value is refused. Each is reported at
# its value, so a GET body sent back as a PUT passes.
_NAMES: tapi_schema.Supported = {"value-name": None, "value": None}
_SUPPORTED: tapi_schema.Supported = {
    "uuid": None,
    "name": _NAMES,
    "layer-protocol-name": None,
    "direction": None,
    "administrative-state": None,
    "routing-constraint": None,
    "topology-constraint": None,
    "end-point": {
        "local-id": None,
        "name": _NAMES,
        "service-interface-point": None,
        "layer-protocol-name": None,
        "direction": None,
        "layer-protocol-constraint": {
            "local-id": None,
            "layer-protocol-name": None,
            OTSIA_CSEP_SPEC: {
                "number-of-otsi": None,
                "otsi-config": {
                    "local-id": None,
                    "central-frequency": None,
                    "channel-output-power": None,
                    "modulation": {"standard-modulation-technique": None},
                },
            },
            MCG_CSEP_SPEC: {
                "number-of-mc": None,
                "mc-spectrum-config-pac": {
                    "local-id": None,
                    "name": _NAMES,
                    "spectrum": None,
                    "edge-frequency-constraint": None,
                },
            },
        },
    },
}
# A lightpath runs from its SOURCE end-point to its SINK over one-way fibres,
# so the service is UNIDIRECTIONAL (TapiContext.add_service). Each entry is
# (the values the twin supports, the values the YANG type admits): a value
# outside the type is invalid (400, invalid-value), a valid one the twin does
# not support is not implemented (501).
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
    found = tapi_schema.unsupported_members(raw, SERVICE_SCHEMA, _SUPPORTED)
    invalid: list[str] = []

    def check(level: str, node: dict, at: str) -> None:
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
                check(OTSIA_CSEP_SPEC, spec, f"{lpc_at}{OTSIA_CSEP_SPEC}/")
    return found, invalid


def _parse_service(body: dict) -> ConnectivityService:
    """Unwrap and validate a connectivity-service request body.

    The body is validated by hand rather than through a FastAPI parameter
    annotation (the envelope key and the list-or-object spelling both have
    to be resolved first), so pydantic's error has to be mapped to a 400
    here — otherwise a malformed payload surfaces as a 500.

    Raises:
        HTTPException: 400 when the body carries a member T-API v2.6.0
            does not define there (``unknown-element``, see
            ``refuse_unknown_members``); 501 (``operation-not-supported``)
            when it carries a standard member the twin does not implement,
            naming each (see ``_SUPPORTED`` and ``_FIXED``); 400 with the
            RFC 8040 §7 tag of the first problem when it does not validate
            (``validation_error_tag``).
    """
    raw = _single_service(body)
    refuse_unknown_members(raw, SERVICE_SCHEMA, SERVICE_PATH, _HINTS)
    refused, invalid = _unimplemented(raw)
    if invalid:
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "invalid-value",
            "invalid value: " + "; ".join(invalid),
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
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST,
            validation_error_tag(exc.errors()),
            "; ".join(
                f"{'/'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}"
                for e in exc.errors()
            ),
        ) from exc
    if len(svc.end_point) != 2:
        # A lightpath has a head and a tail; a third end-point would be
        # silently ignored by admission.
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "invalid-value",
            "a connectivity-service is a point-to-point lightpath: it needs "
            f"exactly two end-points, got {len(svc.end_point)}",
        )
    return svc


def _check_sips(ctx, svc: ConnectivityService) -> None:
    """Refuse a service whose end-points reference a SIP that does not exist.

    ``service-interface-point-uuid`` is a leafref to the context's SIP list
    (tapi-common v2.6.0, require-instance true by default), and RFC 7950
    §15.5 fixes the error for a leafref that "refers to an instance that
    does not exist": error-tag ``data-missing``, error-app-tag
    ``instance-required``, error-path the leafref leaf. RFC 8040 §7 pairs
    ``data-missing`` with 409.
    """
    for ep in svc.end_point:
        sip_uuid = ep.service_interface_point.service_interface_point_uuid
        if ctx.get_sip(sip_uuid) is None:
            raise RestconfError(
                status.HTTP_409_CONFLICT, "data-missing", f"SIP {sip_uuid!r} not found",
                error_app_tag="instance-required",
                error_path=(
                    "/tapi-common:context/tapi-connectivity:connectivity-context"
                    f"/connectivity-service[uuid='{svc.uuid}']"
                    f"/end-point[local-id='{ep.local_id}']"
                    "/service-interface-point/service-interface-point-uuid"
                ),
            )


def constraint_error(exc: Exception) -> RestconfError:
    """The RFC 8040 error for a constraint or spectrum request the twin cannot take.

    A reference to a node or link that does not exist is a leafref with no
    instance (RFC 7950 §15.5: 409, ``data-missing``, ``instance-required``);
    anything else — a constraint leaf or value the twin does not support, a
    malformed or contradictory constraint, a spectrum off the grid — is a
    value the twin does not accept, 400 ``invalid-value``.
    """
    if isinstance(exc, UnresolvedReferenceError):
        return RestconfError(
            status.HTTP_409_CONFLICT, "data-missing", str(exc),
            error_app_tag="instance-required",
        )
    return RestconfError(status.HTTP_400_BAD_REQUEST, "invalid-value", str(exc))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_service(body: dict, request: Request, response: Response) -> dict:
    """Create a new connectivity service between two SIPs.

    RFC 8040 §4.4.1: the target of a creating POST is the new resource's
    parent, here the connectivity-context, with the new
    ``connectivity-service`` entry in the body. The 201 carries a
    ``Location`` naming the entry; it also carries the entry itself (with
    the spectrum and format admission chose), where the RFC sends no body.
    docs/TAPI_COMPLIANCE.md records that deviation.
    """
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

    _check_sips(ctx, svc)

    try:
        await ctx.add_service(svc)
    except (ConstraintError, SpectrumRequestError) as e:
        raise constraint_error(e) from e
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

    response.headers["Location"] = created_location(
        request, PREFIX, "connectivity-service", svc.uuid
    )
    return _entry_body(ctx, svc)


_SERVICE_KEY = "tapi-connectivity:connectivity-service"


def _entry_body(ctx, svc) -> dict:
    """One connectivity-service as its GET returns it: an array of one.

    RFC 7951 §5.4: "A list instance is encoded as a name/array pair", with
    no exception for a single entry, and RFC 8040 §4.3 returns the target
    resource as the body's one member. The 201 of a creating POST or PUT and
    the 200 of a PATCH carry the same body, so a client reads one shape.
    """
    return {_SERVICE_KEY: [_connectivity_service_payload(ctx, svc)]}


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

    One function for every view of it, this module's container and list
    GETs and the full context GET (``api/common.py``), so they cannot
    disagree. Assembled per request: each service carries live spectrum and
    an operational-state that follows the current QoT.
    """
    # operational-state follows the current QoT: re-evaluate what a change
    # (a parameter write, a failure, a neighbour) invalidated.
    await ctx.refresh_operational_states()
    return {
        "connectivity-service": [
            _connectivity_service_payload(ctx, s) for s in ctx.get_services()
        ]
    }


@router.get("")
async def get_connectivity_context(request: Request) -> dict:
    """The whole connectivity context: every service, with live spectrum."""
    ctx = request.app.state.context
    return {
        "tapi-connectivity:connectivity-context": await connectivity_context_payload(ctx)
    }


@router.get("/connectivity-service")
async def get_services(request: Request) -> dict:
    """Return all connectivity services, with assigned spectrum when held.

    RFC 8040 §4.3 and RFC 7951 §4: the target list is the body's one
    top-level member, named with its module. The entries are the container
    GET's, which still answers with the whole connectivity context.
    """
    ctx = request.app.state.context
    payload = await connectivity_context_payload(ctx)
    return {"tapi-connectivity:connectivity-service": payload["connectivity-service"]}


@router.get("/connectivity-service={uuid}")
async def get_service(uuid: str, request: Request) -> dict:
    """Return one connectivity service, with assigned spectrum when held."""
    ctx = request.app.state.context
    svc = ctx.get_service(uuid)
    if svc is None:
        raise HTTPException(
            status_code=404, detail=f"Service {uuid} not found"
        )
    await ctx.get_or_compute_baseline(svc)  # current operational-state
    return _entry_body(ctx, svc)


@router.put(
    "/connectivity-service={uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_201_CREATED: {"description": "Created"}},
)
async def replace_service(uuid: str, body: dict, request: Request) -> Response:
    """Replace a connectivity service by UUID (full replacement), or create it.

    The replacement goes through admission with the old service's resources
    released. If it is refused, the old service is put back exactly as it
    was (``TapiContext.replace_service``): a failed replace never moves or
    tears down the service that was there.

    RFC 8040 §4.5: "if the PUT request creates a new resource, a '201
    Created' status-line is returned. If an existing resource is modified, a
    '204 No Content' status-line is returned." A replacement is therefore a
    204 with no body; a client reads what admission chose with a GET. A
    creation is a 201 carrying a ``Location`` and, like the creating POST,
    the entry (the deviation docs/TAPI_COMPLIANCE.md records).
    """
    ctx = request.app.state.context
    svc = _parse_service(body)
    if svc.uuid != uuid:
        # RFC 8040 §4.5: "the key leaf values, in message-body
        # representation, MUST be the same as the key leaf values in the
        # request URI."
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "invalid-value", "Body UUID must match path UUID",
        )
    _check_sips(ctx, svc)
    # A refused replacement leaves the old service exactly as it was (same
    # route and block); with no old service, a PUT creates one.
    created = ctx.get_service(uuid) is None
    try:
        if created:
            await ctx.add_service(svc)
        else:
            await ctx.replace_service(svc)
    except (ConstraintError, SpectrumRequestError) as e:
        raise constraint_error(e) from e
    except RmsaError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    if not created:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(
        _entry_body(ctx, svc),
        status_code=status.HTTP_201_CREATED,
        headers={"Location": created_location(request, PREFIX, "connectivity-service", uuid)},
    )


#: What a plain patch may change on a connectivity-service. Every other
#: config leaf decides the admission (end-points, constraints, format,
#: spectrum), so it is changed by replacing the service with PUT.
_PATCHABLE = frozenset({"name", "administrative-state", "lifecycle-state"})
#: Config-false leaves: the server's, ignored on input as on PUT.
_STATE = frozenset({"operational-state", "connection", "connection-end-point", "csep-role"})


@router.patch("/connectivity-service={uuid}")
async def update_service(uuid: str, body: dict, request: Request) -> dict:
    """Merge name, administrative-state and lifecycle-state into a service.

    RFC 8040 §4.6.1 plain patch: the body is the target resource's
    representation, ``{"tapi-connectivity:connectivity-service": [{…}]}``
    (the array of one, a bare object, or the bare entry, as POST and PUT
    accept), merged into the entry. Its key, if present, "MUST be the same
    as the key leaf values in the request URI". A leaf the twin cannot
    merge is refused by name rather than ignored. §4.6: 200 with the entry,
    as its GET returns it ("a '200 OK' status-line is returned if there is
    a message-body").
    """
    ctx = request.app.state.context
    svc = ctx.get_service(uuid)
    if svc is None:
        raise HTTPException(
            status_code=404, detail=f"Service {uuid} not found"
        )
    raw = _single_service(body)
    if "uuid" in raw and raw["uuid"] != uuid:
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "invalid-value", "Body UUID must match path UUID",
        )
    refuse_unknown_members(raw, SERVICE_SCHEMA, SERVICE_PATH, _HINTS)
    fixed = sorted(set(raw) - _PATCHABLE - _STATE - {"uuid"})
    if fixed:
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "invalid-value",
            f"PATCH cannot change {', '.join(fixed)}; it merges only "
            f"{', '.join(sorted(_PATCHABLE))}. Replace the service with PUT",
        )
    try:
        patch = UpdateConnectivityServiceRequest.model_validate(
            {k: v for k, v in raw.items() if k in _PATCHABLE}
        )
    except ValidationError as exc:
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST,
            validation_error_tag(exc.errors()),
            "; ".join(
                f"{'/'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}"
                for e in exc.errors()
            ),
        ) from exc
    if patch.name is not None:
        svc.name = patch.name
    if patch.administrative_state is not None:
        svc.administrative_state = patch.administrative_state
    if patch.lifecycle_state is not None:
        svc.lifecycle_state = patch.lifecycle_state
    await ctx.get_or_compute_baseline(svc)  # current operational-state
    return _entry_body(ctx, svc)


@router.delete(
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
