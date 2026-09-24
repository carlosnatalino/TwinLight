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
inside a ``/data/`` response (CLAUDE.md constraint #1).
"""

from __future__ import annotations

import uuid as uuid_lib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # Internal only: RMSA, the physics backends and the Prometheus exporter
    # all read this, but it is never serialised under its own name. The
    # on-the-wire spelling is the end-point augment (see module docstring).
    modulation_format: ModulationFormat = Field(
        default=ModulationFormat.DP_QPSK, exclude=True
    )

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
                        data["modulation_format"] = modulation_from_mt(identity)
                        return data
        return data

    @model_validator(mode="after")
    def _stamp_modulation_on_end_points(self) -> ConnectivityService:
        """Give every end-point the photonic augment for this service.

        Applied after validation rather than at serialisation time so that a
        model built in Python (tests, snapshot restore, the example scripts)
        carries the same structure as one parsed from a T-API request.
        """
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
        return self


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
