"""TAPI Common data model types.

Derived from TAPI v2.6.0 YANG/OAS definitions. All models use hyphenated
aliases so that ``model_dump(by_alias=True)`` produces spec-compliant JSON.
"""

from __future__ import annotations

import uuid as uuid_lib
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class LayerProtocolName(str, Enum):
    ODU = "ODU"
    ETH = "ETH"
    DSR = "DSR"
    PHOTONIC_MEDIA = "PHOTONIC_MEDIA"
    DIGITAL_OTN = "DIGITAL_OTN"


class AdministrativeState(str, Enum):
    LOCKED = "LOCKED"
    UNLOCKED = "UNLOCKED"


class OperationalState(str, Enum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"


class LifecycleState(str, Enum):
    PLANNED = "PLANNED"
    POTENTIAL_AVAILABLE = "POTENTIAL_AVAILABLE"
    POTENTIAL_BUSY = "POTENTIAL_BUSY"
    INSTALLED = "INSTALLED"
    PENDING_REMOVAL = "PENDING_REMOVAL"


class ForwardingDirection(str, Enum):
    BIDIRECTIONAL = "BIDIRECTIONAL"
    UNIDIRECTIONAL = "UNIDIRECTIONAL"
    UNDEFINED_OR_UNKNOWN = "UNDEFINED_OR_UNKNOWN"


class Direction(str, Enum):
    BIDIRECTIONAL = "BIDIRECTIONAL"
    SINK = "SINK"
    SOURCE = "SOURCE"
    UNDEFINED_OR_UNKNOWN = "UNDEFINED_OR_UNKNOWN"


class NameAndValue(BaseModel):
    """tapi.common.NameAndValue"""

    model_config = ConfigDict(populate_by_name=True)

    value_name: str = Field(..., alias="value-name")
    value: str


class ServiceInterfacePoint(BaseModel):
    """tapi.common.ServiceInterfacePoint"""

    model_config = ConfigDict(populate_by_name=True)

    uuid: str = Field(default_factory=lambda: str(uuid_lib.uuid4()))
    name: list[NameAndValue] = Field(default_factory=list)
    layer_protocol_name: LayerProtocolName = Field(
        default=LayerProtocolName.PHOTONIC_MEDIA,
        alias="layer-protocol-name",
    )
    direction: Direction = Field(default=Direction.BIDIRECTIONAL)
    administrative_state: AdministrativeState = Field(
        default=AdministrativeState.UNLOCKED,
        alias="administrative-state",
    )
    operational_state: OperationalState = Field(
        default=OperationalState.ENABLED,
        alias="operational-state",
    )
    lifecycle_state: LifecycleState = Field(
        default=LifecycleState.INSTALLED,
        alias="lifecycle-state",
    )
