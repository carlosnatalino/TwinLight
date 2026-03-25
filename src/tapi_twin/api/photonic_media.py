"""T-API Photonic Media (L0) spectrum context.

GET /data/tapi-photonic-media:spectrum-context — flexible grid parameters
(ITU-T G.694.1) for the digital twin. Read-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/data", tags=["tapi-photonic-media"])


@router.get("/tapi-photonic-media:spectrum-context")
async def get_spectrum_context(request: Request) -> dict:
    """Return spectrum grid parameters (num-slots, slot-width, center frequency).

    Aligned with T-API photonic media profile and ITU-T G.694.1 flexible grid.
    """
    config = request.app.state.config
    spec = config.spectrum
    return {
        "tapi-photonic-media:spectrum-context": {
            "num-slots": spec.num_slots,
            "slot-width-ghz": spec.slot_width_ghz,
            "nominal-central-frequency-thz": spec.center_frequency_thz,
        }
    }
