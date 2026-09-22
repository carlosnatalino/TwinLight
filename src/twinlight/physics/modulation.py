"""Modulation format definitions and parameters for coherent optical systems.

Supported formats (all DP = dual-polarization):
    DP-QPSK   — 100G class, 2 bits/symbol/polarization
    DP-16QAM  — 200G class, 4 bits/symbol/polarization
    DP-64QAM  — 300G class, 6 bits/symbol/polarization
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModulationFormat(str, Enum):
    DP_QPSK = "DP-QPSK"
    DP_16QAM = "DP-16QAM"
    DP_64QAM = "DP-64QAM"


@dataclass(frozen=True)
class ModulationParams:
    baud_rate_hz: float   # Symbol rate [Hz]
    spacing_hz: float     # Channel spacing [Hz]
    tx_power_dbm: float   # Launch power per channel [dBm]
    req_gsnr_db: float    # Required GSNR at HD-FEC threshold (BER ~ 3.8e-3) [dB]
    bits_per_symbol: int  # Selects BER Gaussian approximation formula


# Standard ITU-T parameters for 50 GHz gridded C-band coherent channels.
MODULATION_TABLE: dict[str, ModulationParams] = {
    ModulationFormat.DP_QPSK: ModulationParams(
        baud_rate_hz=32e9,
        spacing_hz=50e9,
        tx_power_dbm=0.0,
        req_gsnr_db=8.5,
        bits_per_symbol=2,
    ),
    ModulationFormat.DP_16QAM: ModulationParams(
        baud_rate_hz=32e9,
        spacing_hz=50e9,
        tx_power_dbm=0.0,
        req_gsnr_db=14.5,
        bits_per_symbol=4,
    ),
    ModulationFormat.DP_64QAM: ModulationParams(
        baud_rate_hz=32e9,
        spacing_hz=50e9,
        tx_power_dbm=0.0,
        req_gsnr_db=20.5,
        bits_per_symbol=6,
    ),
}


# T-API v2.6.0 spells modulation as a `tapi-photonic-media:MT` identity on
# the connectivity-service end-point (see models/connectivity.py). These are
# the identities defined in tapi-photonic-media.yang; note the ONF naming is
# MT_DP-QAM16, not MT_DP-16QAM.
MODULATION_TO_MT: dict[ModulationFormat, str] = {
    ModulationFormat.DP_QPSK: "MT_DP-QPSK",
    ModulationFormat.DP_16QAM: "MT_DP-QAM16",
    ModulationFormat.DP_64QAM: "MT_DP-QAM64",
}

MT_TO_MODULATION: dict[str, ModulationFormat] = {
    mt: fmt for fmt, mt in MODULATION_TO_MT.items()
}


def modulation_from_mt(identity: str) -> ModulationFormat:
    """Resolve a T-API ``MT`` identityref to a :class:`ModulationFormat`.

    RFC 7951 §6.8 only requires the module prefix when the identity comes
    from a different module than the leaf, which is not the case here — so
    the canonical encoding is the bare ``MT_DP-QPSK``. A prefixed spelling
    is still legal, and clients do send it, so both are accepted.

    Raises:
        ValueError: The identity is not one this twin can transmit.
    """
    bare = identity.split(":")[-1].strip()
    try:
        return MT_TO_MODULATION[bare]
    except KeyError:
        raise ValueError(
            f"Unsupported modulation technique {identity!r}; "
            f"this twin supports {sorted(MT_TO_MODULATION)}"
        ) from None


def get_params(fmt: str | ModulationFormat) -> ModulationParams:
    """Return ModulationParams for a given format string or enum value."""
    key = ModulationFormat(fmt) if isinstance(fmt, str) else fmt
    return MODULATION_TABLE[key]


def slots_required_for_modulation(
    modulation_format: str | ModulationFormat,
    slot_width_hz: float,
) -> int:
    """Return the number of spectrum slots required for one channel.

    Current formats use 50 GHz channel spacing (MODULATION_TABLE). Slots
    are ceil(channel_spacing / slot_width) on the ITU-T G.694.1 flexible
    grid — e.g. 50/6.25 = 8 slots.
    """
    import math
    params = get_params(modulation_format)
    return max(1, math.ceil(params.spacing_hz / slot_width_hz))
