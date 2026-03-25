"""TAPI Equipment data model types.

Minimal equipment inventory derived from GNPy elements (Fiber, Edfa,
Transceiver, Roadm). Aligned with T-API equipment concepts.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from tapi_twin.models.common import NameAndValue


class Equipment(BaseModel):
    """tapi.equipment.Equipment — one per GNPy element (or parsed topology element)."""

    model_config = ConfigDict(populate_by_name=True)

    uuid: str
    name: list[NameAndValue] = Field(default_factory=list)
    equipment_type: str = Field(
        default="",
        alias="equipment-type",
        description="GNPy type: Transceiver, Roadm, Fiber, Edfa, etc.",
    )
    # Optional attributes from params (e.g. length for Fiber)
    length_km: float | None = Field(
        default=None,
        alias="length-km",
        description="Length [km] for Fiber elements.",
    )
