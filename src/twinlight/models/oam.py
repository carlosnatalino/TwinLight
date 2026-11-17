"""tapi-oam v2.6.0: the provider-managed OAM jobs the measurement stream reports against.

TR-548 §6.2.3 places each performance record under the oam-job that
controls the measurement, and §6.3.3 makes that job the record's gNMI path.
The twin creates one job per admitted connectivity service and owns its
lifecycle; clients can read jobs but neither create nor delete them.

Only leaves defined under ``tapi-oam:oam-context/oam-job`` in the v2.6.0
YANG appear here. ``oam-job-type`` is deliberately absent: the only photonic
job type, ``OAM_JOB_TYPE_OPTICAL_POWER``, would mislabel a job whose records
carry GSNR, OSNR, BER and the other QoT measures.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from twinlight.models.common import (
    AdministrativeState,
    LifecycleState,
    OperationalState,
)

# Name-based UUIDs: the same service always maps to the same job, so a client
# filtering the stream by job UUID keeps working across a replace (PUT) or a
# restore, and two twins loaded with the same services agree.
OAM_JOB_NAMESPACE = uuid_lib.uuid5(uuid_lib.NAMESPACE_URL, "urn:twinlight:tapi-oam:oam-job")

OAM_JOB_STATE_ACTIVE = "tapi-oam:OAM_JOB_STATE_ACTIVE"


def oam_job_uuid(service_uuid: str) -> str:
    """The UUID of the OAM job that measures ``service_uuid``."""
    return str(uuid_lib.uuid5(OAM_JOB_NAMESPACE, service_uuid))


def tapi_date_and_time(when: datetime) -> str:
    """``tapi-common:date-and-time``: ``yyyyMMddhhmmss.s`` plus ``Z`` for UTC."""
    when = when.astimezone(UTC)
    return when.strftime("%Y%m%d%H%M%S") + f".{when.microsecond // 100_000}Z"


class ConnectivityServiceEndPointRef(BaseModel):
    """tapi-connectivity:connectivity-service-end-point-ref."""

    model_config = ConfigDict(populate_by_name=True)

    connectivity_service_uuid: str = Field(..., alias="connectivity-service-uuid")
    connectivity_service_end_point_local_id: str = Field(
        ..., alias="connectivity-service-end-point-local-id"
    )


class OamJob(BaseModel):
    """tapi-oam:oam-job, read-only and provider-managed."""

    model_config = ConfigDict(populate_by_name=True)

    uuid: str
    oam_job_state: str = Field(default=OAM_JOB_STATE_ACTIVE, alias="oam-job-state")
    creation_time: str = Field(..., alias="creation-time")
    connectivity_service_end_point: ConnectivityServiceEndPointRef = Field(
        ..., alias="connectivity-service-end-point"
    )
    administrative_state: AdministrativeState = Field(
        default=AdministrativeState.UNLOCKED, alias="administrative-state"
    )
    operational_state: OperationalState = Field(
        default=OperationalState.ENABLED, alias="operational-state"
    )
    lifecycle_state: LifecycleState = Field(
        default=LifecycleState.INSTALLED, alias="lifecycle-state"
    )

    @property
    def service_uuid(self) -> str:
        return self.connectivity_service_end_point.connectivity_service_uuid
