"""Read T-API v2.6.0 connectivity-service fields that live on the end-point.

``tapi-connectivity`` has no leaf for either modulation or assigned spectrum.
The photonic module augments the end-point's ``layer-protocol-constraint``
with both, so pulling them out takes more than a ``dict.get`` — and the demo
scripts all need the same two accessors.

They live here rather than inline in each script because this shape has
already moved once, and a `python3 -c` heredoc repeated across four scripts
is four places to forget. Scripts import it with::

    sys.path.insert(0, "<scripts dir>")
    from tapi_fields import modulation_of, spectrum_of

Only the standard library, matching the rest of the demo scripts.
"""

from __future__ import annotations

OTSIA_CSEP_SPEC = "tapi-photonic-media:otsia-connectivity-service-end-point-spec"
MCG_CSEP_SPEC = "tapi-photonic-media:mcg-connectivity-service-end-point-spec"

# ONF spells 16QAM as MT_DP-QAM16, not MT_DP-16QAM.
MT_TO_MODULATION = {
    "MT_DP-QPSK": "DP-QPSK",
    "MT_DP-QAM16": "DP-16QAM",
    "MT_DP-QAM64": "DP-64QAM",
}


def _constraints(service: dict):
    """Every layer-protocol-constraint on every end-point of a service."""
    for end_point in service.get("end-point") or []:
        yield from end_point.get("layer-protocol-constraint") or []


def modulation_of(service: dict, default: str = "?") -> str:
    """The modulation format of a connectivity-service, e.g. ``DP-QPSK``."""
    for constraint in _constraints(service):
        spec = constraint.get(OTSIA_CSEP_SPEC) or {}
        for cfg in spec.get("otsi-config") or []:
            identity = (cfg.get("modulation") or {}).get(
                "standard-modulation-technique", ""
            )
            # RFC 7951 6.8 allows the module-qualified spelling too.
            name = MT_TO_MODULATION.get(identity.split(":")[-1])
            if name is not None:
                return name
    return default


def spectrum_of(service: dict) -> tuple[float, float] | None:
    """(centre THz, width GHz) of the assigned spectrum, or None.

    The augment carries band edges in uint64 Hz; these are the units the
    demo output prints.
    """
    for constraint in _constraints(service):
        spec = constraint.get(MCG_CSEP_SPEC) or {}
        for cfg in spec.get("mc-spectrum-config-pac") or []:
            band = cfg.get("spectrum") or {}
            lower, upper = (
                band.get("lower-frequency"),
                band.get("upper-frequency"),
            )
            if lower and upper:
                return (lower + upper) / 2 / 1e12, (upper - lower) / 1e9
    return None
