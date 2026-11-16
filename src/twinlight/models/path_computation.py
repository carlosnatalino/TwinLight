"""T-API v2.6.0 path-computation objects (``tapi-path-computation.yang``).

v2.6.0 has no RPCs. Path computation is a resource: a client creates a
``path-comp-service`` in the ``path-computation-context`` — two end-points,
plus optional ``routing-constraint``, ``topology-constraint``,
``objective-function`` and ``optimization-constraint`` — and the server
answers with ``path`` objects in the same context, which the service lists by
reference.

Only what the twin can compute is accepted. Leaves it cannot honour are
refused by name rather than ignored (see ``algorithms/path_constraints.py``
for the constraint subset).
"""

from __future__ import annotations

import uuid as uuid_lib
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from twinlight.algorithms.path_constraints import (
    ConstraintError,
    PathConstraints,
    parse_constraints,
)
from twinlight.models.common import NameAndValue


class ServiceInterfacePointRef(BaseModel):
    """``tapi-common:service-interface-point-ref``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    service_interface_point_uuid: str = Field(
        ..., alias="service-interface-point-uuid"
    )


# Leaves of path-service-end-point the twin can act on. The rest (capacity,
# role, direction, layer-protocol-qualifier, node-edge-point) would each change
# what is computed, so a request carrying one is refused.
_END_POINT_KEYS = frozenset({
    "local-id", "name", "service-interface-point", "layer-protocol-name",
})
# Leaves of path-comp-service accepted on create. ``path`` is config false.
_SERVICE_KEYS = frozenset({
    "uuid", "name", "layer-protocol-name", "direction", "end-point",
    "routing-constraint", "topology-constraint", "objective-function",
    "optimization-constraint",
})
# objective-function and optimization-constraint hold only config-false leaves
# in v2.6.0; a client can name the entry and nothing else.
_LOCAL_CLASS_KEYS = frozenset({"local-id", "name"})


def _refuse_unknown(data: Any, allowed: frozenset[str], where: str) -> None:
    if isinstance(data, dict):
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"{where}/{unknown[0]} is not supported")


class PathServiceEndPoint(BaseModel):
    """``tapi-path-computation:path-service-end-point`` (supported subset)."""

    model_config = ConfigDict(populate_by_name=True)

    local_id: str = Field(..., alias="local-id")
    name: list[NameAndValue] = Field(default_factory=list)
    service_interface_point: ServiceInterfacePointRef = Field(
        ..., alias="service-interface-point"
    )
    layer_protocol_name: str = Field(
        default="PHOTONIC_MEDIA", alias="layer-protocol-name"
    )

    @model_validator(mode="before")
    @classmethod
    def _supported_leaves(cls, data: Any) -> Any:
        _refuse_unknown(data, _END_POINT_KEYS, "end-point")
        return data

    @field_validator("layer_protocol_name")
    @classmethod
    def _photonic_only(cls, value: str) -> str:
        if value != "PHOTONIC_MEDIA":
            raise ValueError(
                f"end-point/layer-protocol-name={value!r} is not supported; "
                "this twin computes PHOTONIC_MEDIA paths only"
            )
        return value


class PathCompService(BaseModel):
    """``tapi-path-computation:path-comp-service``.

    ``routing-constraint`` and ``topology-constraint`` are kept as the JSON
    the client sent, so a GET echoes them; their supported subset is checked
    on parse and resolved against the topology when the paths are computed.
    """

    model_config = ConfigDict(populate_by_name=True)

    uuid: str = Field(default_factory=lambda: str(uuid_lib.uuid4()))
    name: list[NameAndValue] = Field(default_factory=list)
    layer_protocol_name: str = Field(
        default="PHOTONIC_MEDIA", alias="layer-protocol-name"
    )
    direction: str = Field(default="UNIDIRECTIONAL")
    end_point: list[PathServiceEndPoint] = Field(
        ..., alias="end-point", min_length=2, max_length=2
    )
    routing_constraint: dict[str, Any] | None = Field(
        default=None, alias="routing-constraint"
    )
    topology_constraint: list[dict[str, Any]] | None = Field(
        default=None, alias="topology-constraint"
    )
    objective_function: dict[str, Any] | None = Field(
        default=None, alias="objective-function"
    )
    optimization_constraint: dict[str, Any] | None = Field(
        default=None, alias="optimization-constraint"
    )
    # config false: filled in by the server, best candidate first.
    path: list[dict[str, str]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _supported_leaves(cls, data: Any) -> Any:
        _refuse_unknown(data, _SERVICE_KEYS, "path-comp-service")
        if isinstance(data, dict):
            for key in ("objective-function", "optimization-constraint"):
                _refuse_unknown(data.get(key), _LOCAL_CLASS_KEYS, key)
        return data

    @field_validator("topology_constraint", mode="before")
    @classmethod
    def _topology_constraint_as_list(cls, value: Any) -> Any:
        """A one-entry list is also accepted spelled as a bare object."""
        return [value] if isinstance(value, dict) else value

    @field_validator("layer_protocol_name")
    @classmethod
    def _photonic_only(cls, value: str) -> str:
        if value != "PHOTONIC_MEDIA":
            raise ValueError(
                f"layer-protocol-name={value!r} is not supported; this twin "
                "computes PHOTONIC_MEDIA paths only"
            )
        return value

    @field_validator("direction")
    @classmethod
    def _unidirectional_only(cls, value: str) -> str:
        # T-API links in this twin are unidirectional and a route is computed
        # from the first end-point to the second; the reverse direction is a
        # separate request.
        if value != "UNIDIRECTIONAL":
            raise ValueError(
                f"direction={value!r} is not supported; paths are computed "
                "UNIDIRECTIONAL, from the first end-point to the second"
            )
        return value

    @model_validator(mode="after")
    def _check_constraints(self) -> PathCompService:
        try:
            self.path_constraints()
        except ConstraintError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def path_constraints(self) -> PathConstraints:
        return parse_constraints(self.routing_constraint, self.topology_constraint)

    @property
    def sip_uuids(self) -> tuple[str, str]:
        a, z = self.end_point
        return (
            a.service_interface_point.service_interface_point_uuid,
            z.service_interface_point.service_interface_point_uuid,
        )

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Absent containers are left out; JSON ``null`` is not a valid
        encoding of an absent YANG container or list (RFC 7951)."""
        data: dict[str, Any] = handler(self)
        return {k: v for k, v in data.items() if v is not None}
