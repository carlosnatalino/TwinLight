"""Runtime configuration plane (`/config/...`).

Lets a client mutate the live digital twin without restarting it:

* per-device physical-layer parameters (allow-listed in
  ``physics.element_params`` — fiber loss, EDFA NF/gain/tilt/VOA, ROADM
  per-channel target), plus a ``failed: bool`` toggle for fiber spans;
* a small runtime-safe subset of the twin's own YAML config
  (transient-model enable flags, RMSA admission margin), allow-listed in
  ``state.twin_overrides``.

Validation errors come back as RESTCONF-style 400/422 via the FastAPI
exception handler in ``app.py``. Successful mutations return the diff
plus the set of service UUIDs whose baselines were invalidated so the
caller knows which OPM streams will move next.

This is intentionally a non-TAPI internal control plane — per the
CLAUDE.md rule, TAPI routers stay TAPI-only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from tapi_twin.physics.backend import element_kind_name as _kind_name
from tapi_twin.physics.element_params import (
    ParamValidationError,
)
from tapi_twin.state.twin_overrides import (
    TWIN_ALLOWED,
    TwinOverrideError,
    flatten,
    to_nested,
)

router = APIRouter(prefix="/config", tags=["config"])


def _device_state(ctx: Any) -> list[dict[str, Any]]:
    """One entry per writable element: uid, type, attrs, failed flag.

    Iterates the live backend's allow-list (GnpyBackend exposes the
    GNPy element_params schema; EgnBackend exposes its smaller
    Fiber/Edfa subset). Elements whose kind has no entry in the schema
    (e.g. ROADM under EGN) are skipped.
    """
    backend = ctx._backend
    if not backend.uid_map:
        return []
    schema = backend.supported_attributes()
    failed = ctx.get_failed_links()
    devices: list[dict[str, Any]] = []
    for uid, el in backend.uid_map.items():
        kind = _kind_name(el)
        attrs_for_kind = schema.get(kind) or {}
        if not attrs_for_kind:
            continue
        entry: dict[str, Any] = {
            "uid": uid,
            "type": kind,
            "attributes": backend.read_all_attributes(uid),
        }
        if kind == "Fiber":
            entry["failed"] = uid in failed
        devices.append(entry)
    return devices


@router.get("/get")
async def get_config(request: Request) -> dict[str, Any]:
    """Full snapshot of the runtime-mutable state.

    Returns the per-device current parameter values, the failed-fiber
    set, every applied override (so a client can tell what the user
    changed vs. what came from the YAML), the runtime-safe twin-config
    subset, and a self-describing ``allowed_attributes`` schema so
    clients can build form UIs without hard-coding the allow-list.
    """
    ctx = request.app.state.context
    if not ctx._backend.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Physical-layer backend {ctx._backend.name!r} not "
                f"loaded — /config requires a working backend"
            ),
        )
    return {
        "backend": ctx._backend.name,
        "devices": _device_state(ctx),
        "failed_links": sorted(ctx.get_failed_links()),
        "element_overrides": ctx.get_element_overrides(),
        "twin": to_nested(ctx.get_twin_overrides()),
        "allowed_attributes": ctx._backend.supported_attributes(),
        "allowed_twin_keys": sorted(TWIN_ALLOWED),
    }


@router.get("/devices/{uid:path}")
async def get_device(uid: str, request: Request) -> dict[str, Any]:
    """Single-device current state (attributes, type, failed flag)."""
    ctx = request.app.state.context
    backend = ctx._backend
    if not backend.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Physical-layer backend {backend.name!r} not loaded — "
                f"/config requires a working backend"
            ),
        )
    el = backend.uid_map.get(uid)
    if el is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown element UID: {uid!r}",
        )
    kind = _kind_name(el)
    schema = backend.supported_attributes()
    if not schema.get(kind):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Element {uid!r} (type {kind}) has no runtime-mutable "
                f"attributes under backend {backend.name!r}"
            ),
        )
    entry: dict[str, Any] = {
        "uid": uid,
        "type": kind,
        "attributes": backend.read_all_attributes(uid),
        "overrides": ctx.get_element_overrides().get(uid, {}),
        "affected_services": sorted(ctx.services_for_element(uid)),
    }
    if kind == "Fiber":
        entry["failed"] = uid in ctx.get_failed_links()
    return entry


@router.post("/set")
async def set_config(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Batch apply runtime overrides.

    Request body::

        {
          "devices": {"<uid>": {"<attr>": value, ..., "failed": bool}, ...},
          "twin":    {nested dict mirroring TWIN_ALLOWED, e.g.
                      {"rmsa": {"qot_margin_db": 2.0}}},
          "redesign": false        // M5: re-run gnpy designed_network()
        }

    Returns the applied diff and the set of service UUIDs whose
    baselines were invalidated as a result.
    """
    ctx = request.app.state.context
    if not ctx._backend.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Physical-layer backend {ctx._backend.name!r} not "
                f"loaded — /config requires a working backend"
            ),
        )

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be a JSON object",
        )
    devices = body.get("devices") or {}
    twin_nested = body.get("twin") or {}
    if not isinstance(devices, dict) or not isinstance(twin_nested, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'devices' and 'twin' must be JSON objects",
        )

    redesign = bool(body.get("redesign"))

    invalidated: dict[str, set[str]] = {}
    if devices or redesign:
        try:
            invalidated = ctx.apply_element_overrides(
                devices, redesign=redesign,
            )
        except (ParamValidationError, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e
        except KeyError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e).strip("'\""),
            ) from e
        except NotImplementedError as e:
            # ``redesign=true`` on a backend that has no equalisation
            # step (EGN). Surface as 501 so the client knows the call
            # was understood but the operation isn't supported here.
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=str(e),
            ) from e

    twin_applied: dict[str, Any] = {}
    if twin_nested:
        flat = flatten(twin_nested)
        try:
            twin_applied = ctx.apply_twin_overrides(flat)
        except TwinOverrideError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e

    return {
        "applied": {
            "devices": devices,
            "twin": to_nested(twin_applied),
            "redesign": redesign,
        },
        "invalidated_services": {
            uid: sorted(svcs) for uid, svcs in invalidated.items()
        },
        "failed_links": sorted(ctx.get_failed_links()),
    }
