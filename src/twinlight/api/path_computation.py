"""T-API v2.6.0 path computation (``tapi-path-computation``).

GET    {root}/data/tapi-common:context/tapi-path-computation:path-computation-context
POST   {root}/data/tapi-common:context/tapi-path-computation:path-computation-context
PUT    {root}/data/tapi-common:context/tapi-path-computation:path-computation-context
GET    …/path-comp-service
GET    …/path-comp-service={uuid}
PUT    …/path-comp-service={uuid}
DELETE …/path-comp-service={uuid}
GET    …/path
GET    …/path={uuid}

v2.6.0 defines no RPCs: a client creates a ``path-comp-service`` by POST on
the container, its parent (RFC 8040 §4.4.1), and reads the computed ``path``
objects it references. The ranked-candidates view with a
candidate count, which T-API has no leaf for, is ``POST /internal/compute-path``.

``tapi-path-computation`` augments ``/tapi-common:context``, so RFC 8040
§3.5.3 puts the container under the context.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from twinlight.algorithms.path_constraints import ConstraintError
from twinlight.api.connectivity import constraint_error, list_entries, replace_lists
from twinlight.api.restconf import (
    RestconfError,
    created_location,
    refuse_unknown_members,
    target_member,
    validation_error_tag,
)
from twinlight.models import tapi_schema
from twinlight.models.path_computation import PathCompService
from twinlight.state.context import NoPathError

#: The container's RESTCONF path: an augment of tapi-common:context, so
#: module-qualified where the module changes (RFC 8040 §3.5.3).
PREFIX = "/data/tapi-common:context/tapi-path-computation:path-computation-context"

router = APIRouter(prefix=PREFIX, tags=["tapi-path-computation"])
_SERVICE_KEY = "tapi-path-computation:path-comp-service"
_PATH_KEY = "tapi-path-computation:path"


def _dump(service: PathCompService) -> dict[str, Any]:
    return service.model_dump(by_alias=True)


#: The path-comp-service list in the v2.6.0 data-node tree, and the
#: instance path of its entries (RFC 7950 §9.13), for error-path.
SERVICE_PATH = "/tapi-common:context/tapi-path-computation:path-computation-context/path-comp-service"
SERVICE_SCHEMA = tapi_schema.node(SERVICE_PATH)

# What the twin computes from, level by level (see connectivity._SUPPORTED):
# every other configuration member v2.6.0 defines — the end-point capacity,
# role, direction, qualifier and node-edge-point, path-set,
# path-set-constraint, adopt-orphan-path — is refused with 501 rather than
# ignored, and a member it does not define with 400 (unknown-element).
# objective-function and optimization-constraint hold only config-false
# leaves besides their name in v2.6.0, and an edit ignores config-false
# members (the computed ``path`` list included), so a client can name them
# and nothing else.
_NAMES: tapi_schema.Supported = {"value-name": None, "value": None}
_SUPPORTED: tapi_schema.Supported = {
    "uuid": None,
    "name": _NAMES,
    "layer-protocol-name": None,
    "direction": None,
    "end-point": {
        "local-id": None,
        "name": _NAMES,
        "service-interface-point": None,
        "layer-protocol-name": None,
    },
    "routing-constraint": None,
    "topology-constraint": None,
    "objective-function": {"local-id": None, "name": _NAMES},
    "optimization-constraint": {"local-id": None, "name": _NAMES},
}


def _parse(body: dict) -> PathCompService:
    """Unwrap one path-comp-service (list-or-object, RFC 7951 §5.4) and validate it.

    Raises:
        RestconfError: 400 ``unknown-element`` for a member v2.6.0 does not
            define (``refuse_unknown_members``); 501
            ``operation-not-supported`` for a standard member the twin does
            not compute from, naming each; 400 with the RFC 8040 §7 tag of
            the first problem when it does not validate.
    """
    if _SERVICE_KEY in body and set(body) != {_SERVICE_KEY}:
        extra = sorted(set(body) - {_SERVICE_KEY})
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "unknown-element",
            "the body carries one path-comp-service and nothing beside it; "
            f"unexpected: {', '.join(extra)}",
            error_path="/", error_info={"bad-element": extra[0]},
        )
    raw = body.get(_SERVICE_KEY, body)
    if isinstance(raw, list):
        if len(raw) != 1:
            # RFC 8040 §4.4.1: exactly one instance of the new resource.
            raise RestconfError(
                status.HTTP_400_BAD_REQUEST, "bad-element",
                f"Expected exactly one path-comp-service, got {len(raw)}",
            )
        raw = raw[0]
    if not isinstance(raw, dict):
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "bad-element",
            "path-comp-service must be an object or a list of one",
        )
    refuse_unknown_members(raw, SERVICE_SCHEMA, SERVICE_PATH)
    refused = tapi_schema.unsupported_members(raw, SERVICE_SCHEMA, _SUPPORTED)
    if refused:
        raise RestconfError(
            status.HTTP_501_NOT_IMPLEMENTED, "operation-not-supported",
            "not implemented by this twin: " + "; ".join(refused)
            + ". Remove these from the path-comp-service request",
        )
    try:
        return PathCompService.model_validate(tapi_schema.without_state(raw, SERVICE_SCHEMA))
    except ValidationError as exc:
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST,
            validation_error_tag(exc.errors()),
            "; ".join(
                f"{'/'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}"
                for e in exc.errors()
            ),
        ) from exc


CONTAINER_PATH = "/tapi-common:context/tapi-path-computation:path-computation-context"
CONTAINER_SCHEMA = tapi_schema.node(CONTAINER_PATH)


def plan_path_computation_context(
    ctx, container: Any,
) -> tuple[list[PathCompService], set[str]]:
    """What a path-computation-context PUT makes of the path-comp-service list.

    As ``connectivity.plan_connectivity_context``: the entries to compute
    (new or changed) and the UUIDs of those repeated unchanged, which keep
    their computed paths. ``path`` is config false (the server's results),
    so it is ignored; ``path-set`` is a standard list the twin does not
    implement (501).
    """
    refuse_unknown_members(container, CONTAINER_SCHEMA, CONTAINER_PATH)
    refused = tapi_schema.unsupported_members(
        container, CONTAINER_SCHEMA, {"path-comp-service": None},
    )
    if refused:
        raise RestconfError(
            status.HTTP_501_NOT_IMPLEMENTED, "operation-not-supported",
            f"not implemented by this twin: {'; '.join(refused)}",
        )
    services: list[PathCompService] = []
    keep: set[str] = set()
    for entry in list_entries(container, "path-comp-service", CONTAINER_PATH):
        current = ctx.get_path_comp_service(entry["uuid"])
        if current is not None and (
            tapi_schema.config_view(entry, SERVICE_SCHEMA)
            == tapi_schema.config_view(_dump(current), SERVICE_SCHEMA)
        ):
            keep.add(current.uuid)
            continue
        services.append(_parse({_SERVICE_KEY: [entry]}))
    return services, keep


@router.put("", status_code=status.HTTP_204_NO_CONTENT)
async def replace_path_computation_context(body: dict, request: Request) -> Response:
    """Replace the whole path-comp-service list (RFC 8040 §4.5).

    The body is the container as its GET returns it; a path-comp-service
    absent from it is deleted with its paths, a new or changed one computed,
    one repeated unchanged kept with its paths. All or nothing, as for the
    connectivity-context. 204: the container always exists.
    """
    ctx = request.app.state.context
    container = target_member(body, "tapi-path-computation:path-computation-context")
    services, keep = plan_path_computation_context(ctx, container)
    await replace_lists(ctx, path_comp_services=services, keep_path_comp_services=keep)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def path_computation_context_payload(ctx) -> dict[str, Any]:
    """The ``tapi-path-computation:path-computation-context`` container.

    One function for both views of it, this module's GET and the full
    context GET (``api/common.py``), so the two cannot disagree.
    """
    return {
        "path-comp-service": [_dump(s) for s in ctx.get_path_comp_services()],
        "path": ctx.get_paths(),
    }


@router.get("")
async def get_path_computation_context(request: Request) -> dict:
    """The whole path-computation context: services and computed paths."""
    return {
        "tapi-path-computation:path-computation-context": (
            path_computation_context_payload(request.app.state.context)
        )
    }


@router.get("/path-comp-service")
async def get_path_comp_services(request: Request) -> dict:
    ctx = request.app.state.context
    return {_SERVICE_KEY: [_dump(s) for s in ctx.get_path_comp_services()]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_path_comp_service(
    body: dict, request: Request, response: Response
) -> dict:
    """Create a path-comp-service; its ``path`` list holds the computed routes.

    Up to ``rmsa.k_shortest_paths`` routes, best first, each a ``path`` in the
    context. Commits no network resources.

    RFC 8040 §4.4.1: the POST targets the parent, the
    path-computation-context, with the new entry in the body; the 201
    carries a ``Location`` naming it, and the entry itself (see
    docs/TAPI_COMPLIANCE.md for that deviation).
    """
    ctx = request.app.state.context
    service = _parse(body)
    try:
        ctx.create_path_comp_service(service)
    except KeyError as exc:
        # An end-point's SIP does not exist: a leafref with no instance,
        # RFC 7950 §15.5 (see connectivity._check_sips).
        raise RestconfError(
            status.HTTP_409_CONFLICT, "data-missing", str(exc.args[0]),
            error_app_tag="instance-required",
        ) from exc
    except ConstraintError as exc:
        raise constraint_error(exc) from exc
    except NoPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        ) from exc
    except ValueError as exc:  # duplicate UUID
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        ) from exc
    response.headers["Location"] = created_location(
        request, PREFIX, "path-comp-service", service.uuid
    )
    # The body is the entry as its GET returns it, an array of one; see
    # docs/TAPI_COMPLIANCE.md for why a 201 carries it at all.
    return {_SERVICE_KEY: [_dump(service)]}


@router.get("/path-comp-service={uuid}")
async def get_path_comp_service(uuid: str, request: Request) -> dict:
    service = request.app.state.context.get_path_comp_service(uuid)
    if service is None:
        raise HTTPException(status_code=404, detail=f"path-comp-service {uuid} not found")
    # A list entry is an array of one (RFC 7951 §5.4).
    return {_SERVICE_KEY: [_dump(service)]}


@router.put(
    "/path-comp-service={uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_201_CREATED: {"description": "Created"}},
)
async def replace_path_comp_service(uuid: str, body: dict, request: Request) -> Response:
    """Create a path-comp-service, or replace it and compute its paths anew.

    RFC 8040 §4.5: 201 with a ``Location`` when the PUT created the entry,
    204 when it replaced one; the key in the body "MUST be the same as the
    key leaf values in the request URI". Like the creating POST, a 201
    carries the entry (the deviation docs/TAPI_COMPLIANCE.md records). A
    replacement that computes no path leaves the old entry and its paths as
    they were.
    """
    ctx = request.app.state.context
    service = _parse(body)
    if service.uuid != uuid:
        raise RestconfError(
            status.HTTP_400_BAD_REQUEST, "invalid-value", "Body UUID must match path UUID",
        )
    try:
        created = await ctx.put_path_comp_service(service)
    except KeyError as exc:
        raise RestconfError(
            status.HTTP_409_CONFLICT, "data-missing", str(exc.args[0]),
            error_app_tag="instance-required",
        ) from exc
    except ConstraintError as exc:
        raise constraint_error(exc) from exc
    except NoPathError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not created:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(
        {_SERVICE_KEY: [_dump(service)]},
        status_code=status.HTTP_201_CREATED,
        headers={"Location": created_location(request, PREFIX, "path-comp-service", uuid)},
    )


@router.delete(
    "/path-comp-service={uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_path_comp_service(uuid: str, request: Request) -> None:
    """Delete a path-comp-service and the paths it references."""
    if not request.app.state.context.delete_path_comp_service(uuid):
        raise HTTPException(status_code=404, detail=f"path-comp-service {uuid} not found")


@router.get("/path")
async def get_paths(request: Request) -> dict:
    return {_PATH_KEY: request.app.state.context.get_paths()}


@router.get("/path={uuid}")
async def get_path(uuid: str, request: Request) -> dict:
    path = request.app.state.context.get_path(uuid)
    if path is None:
        raise HTTPException(status_code=404, detail=f"path {uuid} not found")
    return {_PATH_KEY: [path]}
