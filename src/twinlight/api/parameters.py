"""Path-addressed parameter plane (`/config/parameters`).

* ``GET  /config/parameters?path=…`` — what a parameter path addresses;
* ``POST /config/parameters`` — ``{"set": {path: value}}``;
* ``POST /config/parameters/changes`` — a list of ``to``/``by``/``by_percent``
  changes.

Both writes are immediate and all-or-nothing, and both take ``?dry_run=true``
to return the plan without writing. The twin holds no clock: gradual changes
(soft failures) are driven by a client issuing these calls over time — see
``twinlight-soft-failure``. The logic lives in ``state/parameter_ops.py``
and ``state/parameter_path.py``; this router is the HTTP skin.

Like the rest of ``/config``, this is a proprietary control plane and is
deliberately kept out of the T-API routers (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from twinlight.physics.element_params import ParamValidationError
from twinlight.state.parameter_ops import (
    ParameterChange,
    ParameterOpError,
    apply,
    plan_changes,
    plan_set,
)
from twinlight.state.parameter_path import ParameterPathError, parse, resolve

router = APIRouter(prefix="/config", tags=["parameters"])

_DRY_RUN = Query(default=False, description="Validate and return the plan without writing")


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


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/parameters")
async def list_parameters(
    request: Request,
    path: str = Query(
        default="/*/*/*",
        description="Parameter path to expand, e.g. /Fiber/*/loss_coef",
    ),
) -> dict[str, Any]:
    """Expand a parameter path against the live twin.

    The default ``/*/*/*`` lists every runtime-mutable parameter with its
    current value and bounds. Each entry's ``path`` names exactly that one
    parameter.
    """
    ctx = _context(request)
    try:
        parsed = parse(path)
        refs = resolve(parsed, ctx)
    except ParameterPathError as exc:
        raise _bad_request(exc) from exc
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


@router.post("/parameters")
async def set_parameters(
    request: Request,
    body: dict[str, Any] = Body(
        examples=[{"set": {"/Fiber/*/loss_coef": 0.22, "/Edfa/Edfa1/gain_target": 16}}],
    ),
    dry_run: bool = _DRY_RUN,
) -> dict[str, Any]:
    """Give every parameter each path matches the stated absolute value.

    Body: ``{"set": {"<path>": <number>, ...}}``. A parameter matched by two
    paths, a path matching nothing, a non-number, or a value outside the
    attribute's range refuses the whole batch.
    """
    ctx = _context(request)
    values = body.get("set") if isinstance(body, dict) else None
    if not isinstance(values, dict) or set(body) != {"set"}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            'Body must be {"set": {"<path>": <number>, ...}}',
        )
    try:
        assignments = plan_set(ctx, values)
        invalidated = set() if dry_run else apply(ctx, assignments)
    except (ParameterOpError, ParamValidationError) as exc:
        raise _bad_request(exc) from exc
    return {
        "applied": not dry_run,
        "parameters": [a.describe() for a in assignments],
        "invalidated_services": sorted(invalidated),
    }


@router.post("/parameters/changes")
async def change_parameters(
    request: Request,
    changes: list[ParameterChange],
    dry_run: bool = _DRY_RUN,
) -> dict[str, Any]:
    """Apply a list of changes together.

    Each entry moves every parameter its path matches to ``to``, or by
    ``by`` / ``by_percent`` from that parameter's own current value. All
    entries are computed from the values before the batch, and two entries
    may not target the same parameter. With ``dry_run`` the response is the
    plan: every target's current (``previous``) and resulting (``value``)
    value.
    """
    ctx = _context(request)
    try:
        plans = plan_changes(ctx, changes)
        assignments = [a for p in plans for a in p.assignments]
        invalidated = set() if dry_run else apply(ctx, assignments)
    except (ParameterOpError, ParamValidationError) as exc:
        raise _bad_request(exc) from exc
    return {
        "applied": not dry_run,
        "changes": [p.describe() for p in plans],
        "invalidated_services": sorted(invalidated),
    }
