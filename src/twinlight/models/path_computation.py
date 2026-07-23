"""TAPI Path Computation data model types.

Minimal path computation request/response per T-API path computation
concepts: request path between end-points, return candidate path(s)
as topology link/node refs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ServiceInterfacePointRef(BaseModel):
    """Reference to a ServiceInterfacePoint by UUID."""

    model_config = ConfigDict(populate_by_name=True)

    service_interface_point_uuid: str = Field(
        ..., alias="service-interface-point-uuid"
    )


class TopologyRef(BaseModel):
    """Reference to a Topology by UUID."""

    model_config = ConfigDict(populate_by_name=True)

    topology_uuid: str = Field(..., alias="topology-uuid")


class LinkRef(BaseModel):
    """Reference to a Link within a topology."""

    model_config = ConfigDict(populate_by_name=True)

    topology_uuid: str = Field(..., alias="topology-uuid")
    link_uuid: str = Field(..., alias="link-uuid")


class NodeRef(BaseModel):
    """Reference to a Node within a topology."""

    model_config = ConfigDict(populate_by_name=True)

    topology_uuid: str = Field(..., alias="topology-uuid")
    node_uuid: str = Field(..., alias="node-uuid")


class PathComputationEndPoint(BaseModel):
    """End-point for path computation (SIP ref)."""

    model_config = ConfigDict(populate_by_name=True)

    service_interface_point: ServiceInterfacePointRef = Field(
        ..., alias="service-interface-point"
    )


class ComputePathRequest(BaseModel):
    """Request body for compute-path: source and destination SIPs."""

    model_config = ConfigDict(populate_by_name=True)

    end_point: list[PathComputationEndPoint] = Field(
        ...,
        alias="end-point",
        min_length=2,
        max_length=2,
    )
    max_candidates: int = Field(
        default=1,
        alias="max-candidates",
        ge=1,
        le=10,
        description="Max number of path candidates to return (k-shortest).",
    )


class PathCandidate(BaseModel):
    """A single path as ordered link and node refs."""

    model_config = ConfigDict(populate_by_name=True)

    link: list[LinkRef] = Field(default_factory=list)
    node: list[NodeRef] = Field(default_factory=list)


class ComputePathResponse(BaseModel):
    """Response: list of path candidates (path as link + node refs)."""

    model_config = ConfigDict(populate_by_name=True)

    path: list[PathCandidate] = Field(default_factory=list)
