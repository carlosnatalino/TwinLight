"""Soft-failure plane (`/config/parameters`, `/config/soft-failures`).

Gradual, scheduled drift of physical-layer parameters: the engine lives in
``state/soft_failure.py``, the addressing scheme in
``state/parameter_path.py``. This router is a thin HTTP skin over both.

Like the rest of ``/config``, this is a proprietary control plane and is
deliberately kept out of the T-API routers (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from twinlight.state.parameter_path import ParameterPathError, parse, resolve
from twinlight.state.soft_failure import (
    SoftFailureConflict,
    SoftFailureError,
    SoftFailureManager,
    SoftFailureSpec,
)

router = APIRouter(prefix="/config", tags=["soft-failures"])


def _context(request: Request) -> Any:
    ctx = request.app.state.context
    if not ctx._backend.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Physical-layer backend {ctx._backend.name!r} not loaded — "
                f"/config requires a working backend"
            ),
        )
    return ctx


def _manager(request: Request) -> SoftFailureManager:
    manager: SoftFailureManager = _context(request).soft_failures
    return manager


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a manager call, mapping its exceptions onto HTTP statuses."""
    try:
        return fn(*args, **kwargs)
    except SoftFailureError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SoftFailureConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, str(exc).strip("'\""),
        ) from exc


@router.get("/parameters")
async def list_parameters(
    request: Request,
    path: str = Query(
        default="/*/*/*",
        description="Parameter path to expand, e.g. /Fiber/*/loss_coef",
    ),
) -> dict[str, Any]:
    """Expand a parameter path against the live twin.

    The way to check what a soft failure *would* touch before starting it,
    and to discover what is addressable: the default ``/*/*/*`` lists every
    runtime-mutable parameter with its current value and bounds.
    """
    ctx = _context(request)
    try:
        parsed = parse(path)
        refs = resolve(parsed, ctx)
    except ParameterPathError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    backend = ctx._backend
    return {
        "path": parsed.describe(),
        "parameters": [
            {
                "path": r.path,
                "uid": r.uid,
                "type": r.kind,
                "attribute": r.attribute,
                "value": backend.read_attribute(r.uid, r.attribute),
                "unit": r.spec.get("unit", ""),
                "min": r.spec.get("min"),
                "max": r.spec.get("max"),
            }
            for r in refs
        ],
    }


@router.get("/soft-failures")
async def list_soft_failures(request: Request) -> dict[str, Any]:
    manager = _manager(request)
    return {"soft-failures": [manager.describe(j) for j in manager.list()]}


@router.post("/soft-failures", status_code=status.HTTP_201_CREATED)
async def create_soft_failure(
    request: Request,
    response: Response,
    spec: SoftFailureSpec,
    dry_run: bool = Query(
        default=False,
        description="Validate and return the plan without starting it",
    ),
) -> dict[str, Any]:
    """Start a soft failure. The first parameter write happens immediately
    (after ``start_delay_s``, if set); the rest follow every ``interval_s``.
    """
    manager = _manager(request)
    if dry_run:
        response.status_code = status.HTTP_200_OK
        return manager.describe(_call(manager.plan, spec))
    job = _call(manager.create, spec)
    return manager.describe(job)


@router.get("/soft-failures/{job_id}")
async def get_soft_failure(job_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    return manager.describe(_call(manager.get, job_id))


@router.post("/soft-failures/{job_id}/pause")
async def pause_soft_failure(job_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    return manager.describe(_call(manager.pause, job_id))


@router.post("/soft-failures/{job_id}/resume")
async def resume_soft_failure(job_id: str, request: Request) -> dict[str, Any]:
    manager = _manager(request)
    return manager.describe(_call(manager.resume, job_id))


@router.post("/soft-failures/{job_id}/stop")
async def stop_soft_failure(job_id: str, request: Request) -> dict[str, Any]:
    """Freeze the job; parameters keep the values they had reached."""
    manager = _manager(request)
    return manager.describe(_call(manager.stop, job_id))


@router.delete("/soft-failures/{job_id}")
async def delete_soft_failure(
    job_id: str,
    request: Request,
    revert: bool = Query(
        default=False,
        description="Write every target's start value back before deleting",
    ),
) -> dict[str, Any]:
    manager = _manager(request)
    return manager.describe(_call(manager.delete, job_id, revert=revert))
