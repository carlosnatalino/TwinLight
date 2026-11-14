"""T-API v2.6.0 path computation (``tapi-path-computation``).

GET    /data/tapi-path-computation:path-computation-context
GET    …/path-comp-service
POST   …/path-comp-service
GET    …/path-comp-service={uuid}
DELETE …/path-comp-service={uuid}
GET    …/path
GET    …/path={uuid}

v2.6.0 defines no RPCs: a client creates a ``path-comp-service`` and reads the
computed ``path`` objects it references. The ranked-candidates view with a
candidate count, which T-API has no leaf for, is ``POST /internal/compute-path``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from twinlight.algorithms.path_constraints import ConstraintError
from twinlight.models.path_computation import PathCompService
from twinlight.state.context import NoPathError

router = APIRouter(prefix="/data", tags=["tapi-path-computation"])

_BASE = "/tapi-path-computation:path-computation-context"
_SERVICE_KEY = "tapi-path-computation:path-comp-service"
_PATH_KEY = "tapi-path-computation:path"


def _dump(service: PathCompService) -> dict[str, Any]:
    return service.model_dump(by_alias=True)


def _parse(body: dict) -> PathCompService:
    """Unwrap one path-comp-service (list-or-object, RFC 7951 §5.4) and validate it."""
    raw = body.get(_SERVICE_KEY, body)
    if isinstance(raw, list):
        if len(raw) != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Expected exactly one path-comp-service, got {len(raw)}",
            )
        raw = raw[0]
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path-comp-service must be an object or a list of one",
        )
    try:
        return PathCompService.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(
                f"{'/'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}"
                for e in exc.errors()
            ),
        ) from exc


@router.get(_BASE)
async def get_path_computation_context(request: Request) -> dict:
    """The whole path-computation context: services and computed paths."""
    ctx = request.app.state.context
    return {
        "tapi-path-computation:path-computation-context": {
            "path-comp-service": [_dump(s) for s in ctx.get_path_comp_services()],
            "path": ctx.get_paths(),
        }
    }


@router.get(f"{_BASE}/path-comp-service")
async def get_path_comp_services(request: Request) -> dict:
    ctx = request.app.state.context
    return {_SERVICE_KEY: [_dump(s) for s in ctx.get_path_comp_services()]}


@router.post(f"{_BASE}/path-comp-service", status_code=status.HTTP_201_CREATED)
async def create_path_comp_service(body: dict, request: Request) -> dict:
    """Create a path-comp-service; its ``path`` list holds the computed routes.

    Up to ``rmsa.k_shortest_paths`` routes, best first, each a ``path`` in the
    context. Commits no network resources.
    """
    ctx = request.app.state.context
    service = _parse(body)
    try:
        ctx.create_path_comp_service(service)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc.args[0]),
        ) from exc
    except ConstraintError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc),
        ) from exc
    except NoPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        ) from exc
    except ValueError as exc:  # duplicate UUID
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc),
        ) from exc
    return {_SERVICE_KEY: _dump(service)}


@router.get(f"{_BASE}/path-comp-service={{uuid}}")
async def get_path_comp_service(uuid: str, request: Request) -> dict:
    service = request.app.state.context.get_path_comp_service(uuid)
    if service is None:
        raise HTTPException(status_code=404, detail=f"path-comp-service {uuid} not found")
    return {_SERVICE_KEY: _dump(service)}


@router.delete(
    f"{_BASE}/path-comp-service={{uuid}}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_path_comp_service(uuid: str, request: Request) -> None:
    """Delete a path-comp-service and the paths it references."""
    if not request.app.state.context.delete_path_comp_service(uuid):
        raise HTTPException(status_code=404, detail=f"path-comp-service {uuid} not found")


@router.get(f"{_BASE}/path")
async def get_paths(request: Request) -> dict:
    return {_PATH_KEY: request.app.state.context.get_paths()}


@router.get(f"{_BASE}/path={{uuid}}")
async def get_path(uuid: str, request: Request) -> dict:
    path = request.app.state.context.get_path(uuid)
    if path is None:
        raise HTTPException(status_code=404, detail=f"path {uuid} not found")
    return {_PATH_KEY: path}
