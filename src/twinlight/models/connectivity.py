"""TAPI Connectivity data model types.

Minimal ConnectivityService model per TAPI v2.6.0, sufficient for
creating and querying point-to-point services between two SIPs.

Modulation format
-----------------
``tapi-connectivity.yang`` v2.6.0 has no modulation leaf anywhere — the
photonic layer expresses it as an augment on the *end-point*:

    connectivity-service → end-point → layer-protocol-constraint
      → tapi-photonic-media:otsia-connectivity-service-end-point-spec
          → otsi-config → modulation → standard-modulation-technique

So that is where this model puts it. ``ConnectivityService`` keeps a
``modulation_format`` attribute because RMSA, the physics backends and
the metrics exporter all read it, but the attribute is excluded from
serialisation and the augment above is the only on-the-wire spelling —
a bare top-level ``modulation-format`` would be a non-standard field
inside a ``/data/`` response (see CONTRIBUTING.md, "T-API surfaces stay
standard").
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
from twinlight.models.common import (
    AdministrativeState,
    Direction,
    LayerProtocolName,
    LifecycleState,
    NameAndValue,
    OperationalState,
)
from twinlight.physics.modulation import (
    MODULATION_TO_MT,
    ModulationFormat,
    modulation_from_mt,
)

# The augments' JSON member names. Module-qualified per RFC 7951 §4, because
# both are defined in tapi-photonic-media rather than tapi-connectivity, and
# both augment the same layer-protocol-constraint list entry: OTSiA carries
# the modulation, MCG the assigned spectrum.
OTSIA_CSEP_SPEC = "tapi-photonic-media:otsia-connectivity-service-end-point-spec"
MCG_CSEP_SPEC = "tapi-photonic-media:mcg-connectivity-service-end-point-spec"


class SipRef(BaseModel):
    """Reference to a ServiceInterfacePoint by UUID."""

    model_config = ConfigDict(populate_by_name=True)

    service_interface_point_uuid: str = Field(
        ..., alias="service-interface-point-uuid"
    )


class ModulationTechnique(BaseModel):
    """tapi-photonic-media.modulation-technique."""

    model_config = ConfigDict(populate_by_name=True)

    standard_modulation_technique: str = Field(
        ..., alias="standard-modulation-technique"
    )


class OtsiConfig(BaseModel):
    """tapi-photonic-media.otsi-config-pac (modulation subset)."""

    model_config = ConfigDict(populate_by_name=True)

    local_id: str = Field(default="1", alias="local-id")
    modulation: ModulationTechnique


class OtsiaCsepSpec(BaseModel):
    """tapi-photonic-media.otsia-connectivity-service-end-point-spec."""

    model_config = ConfigDict(populate_by_name=True)

    otsi_config: list[OtsiConfig] = Field(
        default_factory=list, alias="otsi-config"
    )
    number_of_otsi: int = Field(default=1, alias="number-of-otsi")


class LayerProtocolConstraint(BaseModel):
    """tapi-connectivity.layer-protocol-constraint + its photonic augment."""

    model_config = ConfigDict(populate_by_name=True)

    local_id: str = Field(default="otsi", alias="local-id")
    layer_protocol_name: LayerProtocolName = Field(
        default=LayerProtocolName.PHOTONIC_MEDIA,
        alias="layer-protocol-name",
    )
    otsia_spec: OtsiaCsepSpec | None = Field(
        default=None, alias=OTSIA_CSEP_SPEC
    )


class ConnectivityServiceEndPoint(BaseModel):
    """One end-point within a ConnectivityService (A-side or Z-side)."""

    model_config = ConfigDict(populate_by_name=True)

    local_id: str = Field(..., alias="local-id")
    service_interface_point: SipRef = Field(..., alias="service-interface-point")
    direction: Direction = Field(default=Direction.BIDIRECTIONAL)
    layer_protocol_constraint: list[LayerProtocolConstraint] = Field(
        default_factory=list, alias="layer-protocol-constraint"
    )


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
    # T-API routing and topology constraints (tapi-path-computation
    # groupings, used by tapi-connectivity). Kept as the JSON the client sent,
    # so a GET echoes it verbatim; the supported subset is checked when the
    # model is parsed and resolved against the topology at admission
    # (algorithms/path_constraints.py).
    routing_constraint: dict[str, Any] | None = Field(
        default=None, alias="routing-constraint"
    )
    topology_constraint: list[dict[str, Any]] | None = Field(
        default=None, alias="topology-constraint"
    )
    # Internal only: RMSA, the physics backends and the Prometheus exporter
    # all read this, but it is never serialised under its own name. The
    # on-the-wire spelling is the end-point augment (see module docstring).
    modulation_format: ModulationFormat = Field(
        default=ModulationFormat.DP_QPSK, exclude=True
    )
    # The format the client asked for, or None when it left the choice to
    # admission, which then tries formats from the highest spectral
    # efficiency down (TapiContext.add_service). ``modulation_format`` is the
    # format in effect: the requested one, or the one admission chose.
    requested_modulation: ModulationFormat | None = Field(default=None, exclude=True)

    @field_validator("topology_constraint", mode="before")
    @classmethod
    def _topology_constraint_as_list(cls, value: Any) -> Any:
        """A one-entry list is also accepted spelled as a bare object."""
        return [value] if isinstance(value, dict) else value

    @model_validator(mode="after")
    def _check_constraints(self) -> ConnectivityService:
        """Refuse constraints the twin cannot honour when the request is parsed,
        so an unsupported leaf is a validation error rather than ignored."""
        try:
            self.path_constraints()
        except ConstraintError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def path_constraints(self) -> PathConstraints:
        """The supported subset of this service's constraints.

        Raises:
            ConstraintError: See :func:`parse_constraints`.
        """
        return parse_constraints(self.routing_constraint, self.topology_constraint)

    @model_serializer(mode="wrap")
    def _omit_absent_constraints(
        self, handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        """Leave absent constraints out: a JSON ``null`` is not a valid
        encoding of an absent YANG container or list (RFC 7951)."""
        data: dict[str, Any] = handler(self)
        for key in ("routing-constraint", "routing_constraint",
                    "topology-constraint", "topology_constraint"):
            if key in data and data[key] is None:
                del data[key]
        return data

    @model_validator(mode="before")
    @classmethod
    def _modulation_from_end_point(cls, data: Any) -> Any:
        """Lift the end-point's modulation augment into ``modulation_format``.

        Raises:
            ValueError: The payload carries a top-level ``modulation-format``.
                That was this project's pre-2.6 spelling; failing loudly beats
                silently provisioning DP-QPSK because the key was ignored.
        """
        if not isinstance(data, dict):
            return data
        if data.get("modulation_format") is not None:  # built in Python
            data.setdefault("requested_modulation", data["modulation_format"])
        if "modulation-format" in data:
            raise ValueError(
                "'modulation-format' is not a T-API v2.6.0 field. Send the "
                "modulation as end-point/layer-protocol-constraint/"
                f"{OTSIA_CSEP_SPEC}/otsi-config/modulation/"
                "standard-modulation-technique (e.g. 'MT_DP-QPSK')."
            )
        for end_point in data.get("end-point") or data.get("end_point") or []:
            if not isinstance(end_point, dict):
                continue
            constraints = (
                end_point.get("layer-protocol-constraint")
                or end_point.get("layer_protocol_constraint")
                or []
            )
            for constraint in constraints:
                if not isinstance(constraint, dict):
                    continue
                spec = constraint.get(OTSIA_CSEP_SPEC) or constraint.get(
                    "otsia_spec"
                )
                if not isinstance(spec, dict):
                    continue
                for cfg in spec.get("otsi-config") or spec.get(
                    "otsi_config"
                ) or []:
                    identity = (cfg or {}).get("modulation", {}).get(
                        "standard-modulation-technique"
                    )
                    if identity:
                        fmt = modulation_from_mt(identity)
                        data["modulation_format"] = fmt
                        data["requested_modulation"] = fmt
                        return data
        return data

    @model_validator(mode="after")
    def _stamp_modulation_on_end_points(self) -> ConnectivityService:
        """Give every end-point the photonic augment for this service.

        Applied after validation rather than at serialisation time so that a
        model built in Python (tests, snapshot restore, the example scripts)
        carries the same structure as one parsed from a T-API request.
        """
        self._stamp()
        return self

    def assign_modulation(self, fmt: ModulationFormat) -> None:
        """Record the format admission put in effect, on the model and on the
        wire: a GET reports the format the lightpath runs, not just the one
        the client asked for (which may have been none)."""
        self.modulation_format = fmt
        self._stamp()

    def _stamp(self) -> None:
        spec = OtsiaCsepSpec(
            otsi_config=[
                OtsiConfig(
                    modulation=ModulationTechnique(
                        standard_modulation_technique=MODULATION_TO_MT[
                            self.modulation_format
                        ],
                    ),
                ),
            ],
        )
        for end_point in self.end_point:
            end_point.layer_protocol_constraint = [
                LayerProtocolConstraint(otsia_spec=spec)
            ]


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
