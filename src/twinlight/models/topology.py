"""TAPI Topology data model types.

Derived from TAPI v2.6.0 YANG/OAS definitions for topology context,
topology, node, link, and node-edge-point.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from twinlight.models.common import (
    AdministrativeState,
    Direction,
    ForwardingDirection,
    LayerProtocolName,
    LifecycleState,
    NameAndValue,
    OperationalState,
)


class ServiceInterfacePointRef(BaseModel):
    """Reference to a SIP by UUID."""

    model_config = ConfigDict(populate_by_name=True)

    service_interface_point_uuid: str = Field(
        ..., alias="service-interface-point-uuid"
    )


class NodeEdgePointRef(BaseModel):
    """Reference to a NEP by topology/node/nep UUIDs."""

    model_config = ConfigDict(populate_by_name=True)

    topology_uuid: str = Field(..., alias="topology-uuid")
    node_uuid: str = Field(..., alias="node-uuid")
    node_edge_point_uuid: str = Field(..., alias="node-edge-point-uuid")


class LatencyCharacteristic(BaseModel):
    """tapi-topology latency-characteristic: propagation delay from fiber length.

    v2.6.0 types ``fixed-latency-characteristic`` as a string with no unit.
    The twin writes microseconds, the unit tapi-path-computation uses for
    ``max-allowed-delay``, as a decimal string with nanosecond resolution.
    """

    model_config = ConfigDict(populate_by_name=True)

    traffic_property_name: str = Field(
        default="propagation-delay",
        alias="traffic-property-name",
    )
    fixed_latency_characteristic: str = Field(
        ...,
        alias="fixed-latency-characteristic",
        description="Propagation delay in microseconds (fiber length / (c/n)).",
    )

    @classmethod
    def from_seconds(cls, delay_s: float) -> LatencyCharacteristic:
        return cls(fixed_latency_characteristic=f"{delay_s * 1e6:.3f}")


class NodeEdgePoint(BaseModel):
    """tapi.topology.NodeEdgePoint"""

    model_config = ConfigDict(populate_by_name=True)

    uuid: str
    name: list[NameAndValue] = Field(default_factory=list)
    layer_protocol_name: LayerProtocolName = Field(
        default=LayerProtocolName.PHOTONIC_MEDIA,
        alias="layer-protocol-name",
    )
    direction: Direction = Field(default=Direction.BIDIRECTIONAL)
    mapped_service_interface_point: list[ServiceInterfacePointRef] = Field(
        default_factory=list, alias="mapped-service-interface-point"
    )
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


class Node(BaseModel):
    """tapi.topology.Node"""

    model_config = ConfigDict(populate_by_name=True)

    uuid: str
    name: list[NameAndValue] = Field(default_factory=list)
    layer_protocol_name: list[LayerProtocolName] = Field(
        default_factory=lambda: [LayerProtocolName.PHOTONIC_MEDIA],
        alias="layer-protocol-name",
    )
    owned_node_edge_point: list[NodeEdgePoint] = Field(
        default_factory=list, alias="owned-node-edge-point"
    )
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


class Link(BaseModel):
    """tapi.topology.Link"""

    model_config = ConfigDict(populate_by_name=True)

    uuid: str
    name: list[NameAndValue] = Field(default_factory=list)
    layer_protocol_name: list[LayerProtocolName] = Field(
        default_factory=lambda: [LayerProtocolName.PHOTONIC_MEDIA],
        alias="layer-protocol-name",
    )
    node_edge_point: list[NodeEdgePointRef] = Field(
        default_factory=list, alias="node-edge-point"
    )
    direction: ForwardingDirection = Field(
        default=ForwardingDirection.UNIDIRECTIONAL,
    )
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
    latency_characteristic: list[LatencyCharacteristic] = Field(
        default_factory=list,
        alias="latency-characteristic",
        description="Propagation delay from fiber length (speed of light in fiber).",
    )


class Topology(BaseModel):
    """tapi.topology.Topology"""

    model_config = ConfigDict(populate_by_name=True)

    uuid: str
    name: list[NameAndValue] = Field(default_factory=list)
    layer_protocol_name: list[LayerProtocolName] = Field(
        default_factory=lambda: [LayerProtocolName.PHOTONIC_MEDIA],
        alias="layer-protocol-name",
    )
    node: list[Node] = Field(default_factory=list)
    link: list[Link] = Field(default_factory=list)


class TopologyContext(BaseModel):
    """tapi.topology.TopologyContext — augmentation on Context."""

    model_config = ConfigDict(populate_by_name=True)

    topology: list[Topology] = Field(default_factory=list)
