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

from tapi_twin.physics.element_params import (
    ParamValidationError,
    read_all,
    schema,
    specs_for,
)
from tapi_twin.state.twin_overrides import (
    TWIN_ALLOWED,
    TwinOverrideError,
    flatten,
    to_nested,
)

router = APIRouter(prefix="/config", tags=["config"])


def _device_state(ctx: Any) -> list[dict[str, Any]]:
    """One entry per writable GNPy element: uid, type, attrs, failed flag."""
    if not ctx._gnpy_uid_map:
        return []
    failed = ctx.get_failed_links()
    devices: list[dict[str, Any]] = []
    for uid, el in ctx._gnpy_uid_map.items():
        if not specs_for(el):
            continue
        entry: dict[str, Any] = {
            "uid": uid,
            "type": type(el).__name__,
            "attributes": read_all(el),
        }
        if type(el).__name__ == "Fiber":
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
    if not ctx._gnpy_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GNPy network not loaded — /config requires GNPy",
        )
    return {
        "devices": _device_state(ctx),
        "failed_links": sorted(ctx.get_failed_links()),
        "element_overrides": ctx.get_element_overrides(),
        "twin": to_nested(ctx.get_twin_overrides()),
        "allowed_attributes": schema(),
        "allowed_twin_keys": sorted(TWIN_ALLOWED),
    }


@router.get("/devices/{uid:path}")
async def get_device(uid: str, request: Request) -> dict[str, Any]:
    """Single-device current state (attributes, type, failed flag)."""
    ctx = request.app.state.context
    if not ctx._gnpy_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GNPy network not loaded — /config requires GNPy",
        )
    el = ctx._gnpy_uid_map.get(uid)
    if el is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown GNPy element UID: {uid!r}",
        )
    if not specs_for(el):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Element {uid!r} (type {type(el).__name__}) has no "
                f"runtime-mutable attributes"
            ),
        )
    entry: dict[str, Any] = {
        "uid": uid,
        "type": type(el).__name__,
        "attributes": read_all(el),
        "overrides": ctx.get_element_overrides().get(uid, {}),
        "affected_services": sorted(ctx.services_for_element(uid)),
    }
    if type(el).__name__ == "Fiber":
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
    if not ctx._gnpy_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GNPy network not loaded — /config requires GNPy",
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

    invalidated: dict[str, set[str]] = {}
    if devices:
        try:
            invalidated = ctx.apply_element_overrides(devices)
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

    # M5 will hook the redesign flag through; for now reject with a clear
    # 501 if a client sets it, so an early integration doesn't believe it
    # took effect.
    if body.get("redesign"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="redesign=true is not yet implemented (planned for M5)",
        )

    return {
        "applied": {
            "devices": devices,
            "twin": to_nested(twin_applied),
        },
        "invalidated_services": {
            uid: sorted(svcs) for uid, svcs in invalidated.items()
        },
        "failed_links": sorted(ctx.get_failed_links()),
    }
