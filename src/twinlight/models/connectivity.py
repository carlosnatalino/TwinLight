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
    """tapi-photonic-media.otsi-config-pac (the subset the twin acts on)."""

    model_config = ConfigDict(populate_by_name=True)

    local_id: str = Field(default="1", alias="local-id")
    # uint64, Hz ("The central frequency of the laser … Measured in Hz.").
    central_frequency: int | None = Field(default=None, alias="central-frequency")
    modulation: ModulationTechnique

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        return {k: v for k, v in data.items() if v is not None}


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
    # Spectrum the client asked for, lifted from the end-points (see
    # _channel_from_end_points). None = first-fit at admission.
    requested_spectrum_hz: tuple[int, int] | None = Field(default=None, exclude=True)
    requested_central_frequency_hz: int | None = Field(default=None, exclude=True)
    requested_edge_frequency_constraint: dict[str, Any] | None = Field(
        default=None, exclude=True
    )
    # Central frequency of the admitted channel [Hz]; reported in otsi-config.
    central_frequency_hz: int | None = Field(default=None, exclude=True)

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

    @model_validator(mode="before")
    @classmethod
    def _channel_from_end_points(cls, data: Any) -> Any:
        """Lift a requested spectrum and central frequency from the end-points.

        T-API v2.6.0 has two places for them, both optional:

        * ``…/mcg-connectivity-service-end-point-spec/mc-spectrum-config-pac/
          spectrum`` — the media channel's ``lower-frequency`` and
          ``upper-frequency``, uint64 Hz;
        * ``…/otsia-connectivity-service-end-point-spec/otsi-config/
          central-frequency`` — the carrier's frequency, uint64 Hz.

        Each end-point may carry them; when several do, they must agree, since
        a lightpath has one channel. A service holds one media channel, so
        more than one ``mc-spectrum-config-pac``, and the MCG leaves the twin
        does not act on, are refused by name rather than ignored. Grid
        alignment, width and availability depend on the grid and the route,
        and are checked at admission.
        """
        if not isinstance(data, dict):
            return data
        spectra: set[tuple[int, int]] = set()
        centres: set[int] = set()
        edges: list[dict[str, Any]] = []
        for end_point in data.get("end-point") or []:
            for constraint in (end_point or {}).get("layer-protocol-constraint") or []:
                if not isinstance(constraint, dict):
                    continue
                for cfg in (constraint.get(OTSIA_CSEP_SPEC) or {}).get("otsi-config") or []:
                    if isinstance(cfg, dict) and cfg.get("central-frequency") is not None:
                        centres.add(_uint64(
                            cfg["central-frequency"], "otsi-config/central-frequency",
                        ))
                mcg = constraint.get(MCG_CSEP_SPEC)
                if mcg is None:
                    continue
                spectrum, edge = _requested_media_channel(mcg)
                if spectrum is not None:
                    spectra.add(spectrum)
                if edge is not None:
                    edges.append(edge)
        if len(spectra) > 1 or len(centres) > 1:
            raise ValueError(
                "the end-points request different spectrum; a lightpath has one "
                "channel, so every end-point must carry the same values"
            )
        if spectra:
            data["requested_spectrum_hz"] = spectra.pop()
        if centres:
            data["requested_central_frequency_hz"] = centres.pop()
        if edges:
            data["requested_edge_frequency_constraint"] = edges[0]
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

    def assign_channel(self, fmt: ModulationFormat, central_frequency_hz: int) -> None:
        """Record the channel admission put in effect, on the model and on the
        wire: a GET reports the format and central frequency the lightpath
        runs, not just what the client asked for (which may have been
        nothing)."""
        self.modulation_format = fmt
        self.central_frequency_hz = central_frequency_hz
        self._stamp()

    def _stamp(self) -> None:
        spec = OtsiaCsepSpec(
            otsi_config=[
                OtsiConfig(
                    central_frequency=(
                        self.central_frequency_hz
                        if self.central_frequency_hz is not None
                        else self.requested_central_frequency_hz
                    ),
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


# MCG leaves the twin acts on. mc-bandwidth-config-pac and
# mc-flexi-grid-config-pac would constrain the channel in ways the twin does
# not implement, so a request carrying them is refused rather than ignored.
_MCG_KEYS = frozenset({"number-of-mc", "mc-spectrum-config-pac"})
_MC_SPECTRUM_KEYS = frozenset({"local-id", "name", "spectrum", "edge-frequency-constraint"})


# These helpers run inside pydantic validators, which turn only ValueError into
# a validation error (a 422); a TypeError would escape as a 500. Hence the
# TRY004 suppressions below.


def _uint64(value: Any, where: str) -> int:
    """A YANG uint64: a JSON number or, per RFC 7951 §6.1, a decimal string."""
    if isinstance(value, bool):
        raise ValueError(f"{where} must be a uint64, got {value!r}")  # noqa: TRY004
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(f"{where} must be a uint64, got {value!r}")


def _requested_media_channel(
    mcg: Any,
) -> tuple[tuple[int, int] | None, dict[str, Any] | None]:
    """(lower, upper) Hz and edge-frequency-constraint of a requested MC."""
    where = MCG_CSEP_SPEC
    if not isinstance(mcg, dict):
        raise ValueError(f"{where} must be an object")  # noqa: TRY004
    unknown = sorted(set(mcg) - _MCG_KEYS)
    if unknown:
        raise ValueError(f"{where}/{unknown[0]} is not supported")
    pacs = mcg.get("mc-spectrum-config-pac") or []
    number = mcg.get("number-of-mc", 1)
    if number != 1 or len(pacs) > 1:
        raise ValueError(
            f"{where}: a connectivity-service holds one media channel; "
            "number-of-mc and mc-spectrum-config-pac must describe one"
        )
    if not pacs:
        return None, None
    pac = pacs[0]
    if not isinstance(pac, dict):
        raise ValueError(  # noqa: TRY004
            f"{where}/mc-spectrum-config-pac entries must be objects"
        )
    unknown = sorted(set(pac) - _MC_SPECTRUM_KEYS)
    if unknown:
        raise ValueError(f"{where}/mc-spectrum-config-pac/{unknown[0]} is not supported")
    spectrum = pac.get("spectrum")
    band: tuple[int, int] | None = None
    if spectrum is not None:
        lower = _uint64(spectrum.get("lower-frequency"), "spectrum/lower-frequency")
        upper = _uint64(spectrum.get("upper-frequency"), "spectrum/upper-frequency")
        if lower >= upper:
            raise ValueError(
                f"spectrum/lower-frequency {lower} must be below upper-frequency {upper}"
            )
        band = (lower, upper)
    edge = pac.get("edge-frequency-constraint")
    return band, edge if isinstance(edge, dict) else None

