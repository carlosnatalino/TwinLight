"""TAPI OAM API endpoints (tapi-oam v2.6.0) — read-only OAM jobs.

GET .../tapi-oam:oam-context
GET .../tapi-oam:oam-context/oam-job
GET .../tapi-oam:oam-context/oam-job={uuid}

T-API only: every key served here is defined under
``tapi-oam:oam-context/oam-job`` in the v2.6.0 YANG. The twin creates one job
per admitted connectivity service, referencing the end-point its OPM is
measured at; the T-API measurement stream (TR-548 §6) uses the job as each
record's gNMI path. Jobs are provider-managed, so there is no POST, PUT or
DELETE — RESTCONF answers those with 405.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

PREFIX = "/data/tapi-common:context/tapi-oam:oam-context"

router = APIRouter(prefix=PREFIX, tags=["tapi-oam"])


def oam_context_payload(ctx) -> dict:
    """The oam-context container; an empty list is omitted (RFC 7951 §5.4)."""
    jobs = [job.model_dump(by_alias=True) for job in ctx.get_oam_jobs()]
    return {"oam-job": jobs} if jobs else {}


@router.get("")
async def get_oam_context(request: Request) -> dict:
    """Return the OAM context (the twin populates only oam-job)."""
    return {"tapi-oam:oam-context": oam_context_payload(request.app.state.context)}


@router.get("/oam-job")
async def get_oam_jobs(request: Request) -> dict:
    """Return every OAM job."""
    ctx = request.app.state.context
    return {
        "tapi-oam:oam-job": [job.model_dump(by_alias=True) for job in ctx.get_oam_jobs()]
    }


@router.get("/oam-job={uuid}")
async def get_oam_job(uuid: str, request: Request) -> dict:
    """Return one OAM job."""
    job = request.app.state.context.get_oam_job(uuid)
    if job is None:
        raise HTTPException(status_code=404, detail=f"OAM job {uuid} not found")
    return {"tapi-oam:oam-job": [job.model_dump(by_alias=True)]}
