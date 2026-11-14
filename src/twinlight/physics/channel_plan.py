"""The line's spectral defaults and amplifier bands, taken from GNPy.

Two facts about the optical line come from the equipment library rather than
from twin configuration:

* the reference channel (``SI.default``: comb bounds, spacing, symbol rate,
  roll-off, the per-channel launch power the amplifiers are designed for, and
  the transmitter output power);
* the band each amplifier passes (``Edfa.params.bands``). An EDFA drops every
  carrier whose slot is not inside its band
  (``gnpy.core.info.is_in_band``), so spectrum outside the band common to a
  path's amplifiers is not assignable on that path.

When no equipment file is configured — possible under the EGN backend — the
values come from GNPy's own reference library, ``example-data/eqpt_config.json``
in the installed gnpy package, so the twin never invents a channel plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpectralDefaults:
    """GNPy's ``SI.default`` entry, in SI units.

    ``f_min_hz`` and ``f_max_hz`` are the first and last channel *centres* of
    the reference comb (gnpy 3 semantics, see ``create_input_spectral_information``).
    """

    f_min_hz: float
    f_max_hz: float
    spacing_hz: float
    baud_rate_hz: float
    roll_off: float
    power_dbm: float       # per-channel launch power the line is designed for
    tx_power_dbm: float    # transmitter output power
    tx_osnr_db: float
    source: str            # the equipment file these came from


def spectral_defaults_from_equipment(equipment: dict, source: str) -> SpectralDefaults:
    """Read ``SI.default`` from a GNPy equipment library (``load_equipment``)."""
    si = equipment["SI"]["default"]
    return SpectralDefaults(
        f_min_hz=float(si.f_min),
        f_max_hz=float(si.f_max),
        spacing_hz=float(si.spacing),
        baud_rate_hz=float(si.baud_rate),
        roll_off=float(si.roll_off),
        power_dbm=float(si.power_dbm),
        tx_power_dbm=float(getattr(si, "tx_power_dbm", None) or si.power_dbm),
        tx_osnr_db=float(si.tx_osnr),
        source=source,
    )


def bundled_equipment_path() -> Path:
    """GNPy's reference equipment library inside the installed package."""
    from twinlight.example_data import find_example_file

    path = find_example_file("eqpt_config.json")
    if path is None:
        raise FileNotFoundError(
            "gnpy's example-data/eqpt_config.json is not installed; configure "
            "gnpy.equipment"
        )
    return path


def load_spectral_defaults(equipment_path: Path | None) -> SpectralDefaults:
    """``SI.default`` from the configured equipment file, else GNPy's bundled one.

    A configured file that GNPy cannot read falls back to the bundled library
    with a warning, mirroring how the EGN backend treats a failed amplifier
    design: the twin keeps running on GNPy's reference values.
    """
    from gnpy.tools.json_io import load_equipment

    if equipment_path is not None:
        try:
            return spectral_defaults_from_equipment(
                load_equipment(str(equipment_path)), str(equipment_path),
            )
        except Exception:  # any unreadable library falls back
            logger.warning(
                "Cannot read SI from %s; using gnpy's bundled equipment library",
                equipment_path, exc_info=True,
            )
    bundled = bundled_equipment_path()
    return spectral_defaults_from_equipment(load_equipment(str(bundled)), str(bundled))


def common_band(
    amp_bands: list[list[dict[str, Any]]], si: SpectralDefaults,
) -> list[tuple[float, float]]:
    """Frequency ranges [Hz] every amplifier in ``amp_bands`` passes.

    ``gnpy.core.utils.find_common_range`` — the function GNPy's own
    ``find_elements_common_range`` uses to filter a path's spectrum. A path
    with no amplifier gets the SI comb range, as in GNPy.
    """
    from gnpy.core.utils import find_common_range

    ranges = find_common_range(amp_bands, si.f_min_hz, si.f_max_hz, si.spacing_hz)
    return [(float(r["f_min"]), float(r["f_max"])) for r in ranges]
