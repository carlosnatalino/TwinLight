"""Admin endpoints: snapshot and restore checkpoints.

POST /admin/snapshot        — serialize state to JSON
POST /admin/restore         — restore state from a snapshot file
GET  /admin/snapshot/latest — path and timestamp of most recent snapshot
GET  /admin/snapshots       — list available snapshot files (path + timestamp)
GET  /admin/snapshot/content — return JSON content of a snapshot (query: path=)
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, status

router = APIRouter(prefix="/admin", tags=["admin"])

_DEFAULT_SNAPSHOT_DIR = Path("snapshots")


def _snapshot_dir(request: Request) -> Path:
    """Directory for snapshots; ensure it exists."""
    out = _DEFAULT_SNAPSHOT_DIR
    out.mkdir(parents=True, exist_ok=True)
    return out


@router.post("/snapshot")
async def create_snapshot(
    request: Request,
    body: dict | None = Body(default=None),
) -> dict:
    """Write current connectivity and spectrum state to a JSON file.

    Optional body: {"path": "snapshots/my.json"} to choose path. If omitted,
    writes to snapshots/twin-<ISO8601>.json.
    """
    ctx = request.app.state.context
    if body and body.get("path"):
        path = Path(body["path"])
    else:
        from datetime import datetime
        name = f"twin-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        path = _snapshot_dir(request) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx.snapshot(path)
    return {
        "path": str(path),
        "timestamp": path.stat().st_mtime,
    }


@router.post("/restore")
async def restore_snapshot(request: Request, body: dict) -> dict:
    """Restore state from a snapshot file. Body: {"path": "snapshots/..."}."""
    path_str = body.get("path")
    if not path_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'path' in body",
        )
    path = Path(path_str)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Snapshot file not found: {path}",
        )
    ctx = request.app.state.context
    try:
        ctx.restore_from(path)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return {"restored": str(path), "services": len(ctx.get_services())}


@router.get("/snapshot/latest")
async def get_latest_snapshot(request: Request) -> dict:
    """Return path and mtime of the most recent snapshot in the default dir."""
    directory = _snapshot_dir(request)
    candidates = list(directory.glob("*.json"))
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No snapshot files found",
        )
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return {
        "path": str(latest),
        "timestamp": latest.stat().st_mtime,
    }


@router.get("/snapshots")
async def list_snapshots(request: Request) -> dict:
    """Return list of available snapshot files (path + timestamp), newest first."""
    directory = _snapshot_dir(request)
    candidates = list(directory.glob("*.json"))
    entries: list[dict[str, Any]] = [
        {"path": str(p), "timestamp": p.stat().st_mtime}
        for p in candidates
    ]
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return {"snapshots": entries}


@router.get("/snapshot/content")
async def get_snapshot_content(request: Request, path: str = "") -> dict:
    """Return the JSON content of a snapshot file. Query: path=<filename or path>."""
    if not path.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing query parameter: path",
        )
    directory = _snapshot_dir(request)
    file_path = (
        Path(path).resolve()
        if Path(path).is_absolute()
        else (directory / path).resolve()
    )
    dir_resolved = directory.resolve()
    if not str(file_path).startswith(str(dir_resolved)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must be under the snapshot directory",
        )
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Snapshot file not found: {path}",
        )
    try:
        data = json.loads(file_path.read_text())
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON in snapshot: {e}",
        ) from e
    return data
