"""T-API photonic media (L0) spectrum model.

Frequency-slot representation aligned with T-API photonic media profile
and ITU-T G.694.1 flexible grid. Used to augment connectivity-service
responses with assigned spectrum (read-only).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FrequencySlot(BaseModel):
    """T-API / G.694.1 frequency slot (assigned spectrum for a channel).

    nominal-central-frequency: center of the slot in THz.
    slot-width: total width of the allocation in GHz (e.g. 12.5 for 2 × 6.25 GHz).
    """

    model_config = ConfigDict(populate_by_name=True)

    nominal_central_frequency: float = Field(
        ...,
        alias="nominal-central-frequency",
        description="Center frequency in THz (ITU-T G.694.1 flexible grid).",
    )
    slot_width: float = Field(
        ...,
        alias="slot-width",
        description="Slot width in GHz (total allocated width).",
    )
