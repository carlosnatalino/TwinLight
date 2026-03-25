"""TAPI Connectivity data model types.

Minimal ConnectivityService model per TAPI v2.6.0, sufficient for
creating and querying point-to-point services between two SIPs.
"""

from __future__ import annotations

import uuid as uuid_lib

from pydantic import BaseModel, ConfigDict, Field

from tapi_twin.models.common import (
    AdministrativeState,
    Direction,
    LifecycleState,
    NameAndValue,
    OperationalState,
)
from tapi_twin.physics.modulation import ModulationFormat


class SipRef(BaseModel):
    """Reference to a ServiceInterfacePoint by UUID."""

    model_config = ConfigDict(populate_by_name=True)

    service_interface_point_uuid: str = Field(
        ..., alias="service-interface-point-uuid"
    )


class ConnectivityServiceEndPoint(BaseModel):
    """One end-point within a ConnectivityService (A-side or Z-side)."""

    model_config = ConfigDict(populate_by_name=True)

    local_id: str = Field(..., alias="local-id")
    service_interface_point: SipRef = Field(..., alias="service-interface-point")
    direction: Direction = Field(default=Direction.BIDIRECTIONAL)


class ConnectivityService(BaseModel):
    """tapi.connectivity.ConnectivityService (minimal)."""

    model_config = ConfigDict(populate_by_name=True)

    uuid: str = Field(default_factory=lambda: str(uuid_lib.uuid4()))
    name: list[NameAndValue] = Field(default_factory=list)
    end_point: list[ConnectivityServiceEndPoint] = Field(
        default_factory=list, alias="end-point"
    )
    administrative_state: AdministrativeState = Field(
        default=AdministrativeState.UNLOCKED, alias="administrative-state"
    )
    operational_state: OperationalState = Field(
        default=OperationalState.ENABLED, alias="operational-state"
    )
    lifecycle_state: LifecycleState = Field(
        default=LifecycleState.PLANNED, alias="lifecycle-state"
    )
    modulation_format: ModulationFormat = Field(
        default=ModulationFormat.DP_QPSK, alias="modulation-format"
    )


class UpdateConnectivityServiceRequest(BaseModel):
    """Partial update for a ConnectivityService (name, admin/lifecycle state)."""

    model_config = ConfigDict(populate_by_name=True)

    name: list[NameAndValue] | None = None
    administrative_state: AdministrativeState | None = Field(
        default=None, alias="administrative-state"
    )
    lifecycle_state: LifecycleState | None = Field(
        default=None, alias="lifecycle-state"
    )
