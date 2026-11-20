"""YANG 64-bit numbers in JSON: RFC 7951 §6.1.

    "A value of the "int64", "uint64", or "decimal64" type is represented
    as a JSON string whose content is the lexical representation of the
    corresponding YANG type as specified in Sections 9.2.1 and 9.3.1 of
    [RFC7950]."

while "int8" … "uint32" stay JSON numbers. T-API v2.6.0 types every
numeric leaf it defines as ``uint64``, ``int64`` or ``decimal64`` (all
with ``fraction-digits 7``), so on the T-API surface every number is a
string. The twin keeps plain Python numbers internally — in its models,
its snapshots and its proprietary endpoints, none of which is YANG-JSON —
and converts at the T-API boundary:

* :func:`encode_64bit_leaves` turns the leaves listed in
  :data:`TAPI_64BIT_LEAVES` into their canonical strings (RFC 7950 §9.2.2,
  §9.3.2) on the way out;
* :func:`parse_uint64` and :func:`parse_decimal64` read a request: the
  standard string and, as a documented leniency, the JSON number earlier
  releases emitted. Anything else raises ``ValueError``, which the T-API
  layer reports as ``400`` ``invalid-value`` (RFC 7950 §8.3.1: a value
  that "does not match the type constraints for the leaf … MUST reply with
  an "invalid-value" <error-tag>").
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

UINT64_MAX = 2**64 - 1
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

#: Every decimal64 leaf of T-API v2.6.0 has ``fraction-digits 7``.
TAPI_FRACTION_DIGITS = 7

# RFC 7950 §9.2.1: "an optional sign ("+" or "-"), followed by a sequence of
# decimal digits". §9.3.1 adds "optionally followed by a period ('.') as a
# decimal indicator and a sequence of decimal digits". The hexadecimal and
# octal notations of §9.2.1 are for default values in a module, not instance
# data. [0-9] rather than \d, which also matches non-ASCII digits.
_INTEGER = re.compile(r"[+-]?[0-9]+")
_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")


# ---------------------------------------------------------------------------
# Output: canonical strings
# ---------------------------------------------------------------------------


def format_uint64(value: int) -> str:
    """The canonical uint64 string (RFC 7950 §9.2.2): no sign, no leading zero."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= UINT64_MAX:
        raise TypeError(f"not a uint64: {value!r}")
    return str(value)


def format_int64(value: int) -> str:
    """The canonical int64 string (RFC 7950 §9.2.2)."""
    if isinstance(value, bool) or not isinstance(value, int) or not INT64_MIN <= value <= INT64_MAX:
        raise TypeError(f"not an int64: {value!r}")
    return str(value)


def format_decimal64(value: float | Decimal, fraction_digits: int) -> str:
    """The canonical decimal64 string (RFC 7950 §9.3.2).

    "The decimal point is required. Leading and trailing zeros are
    prohibited, subject to the rule that there MUST be at least one digit
    before and after the decimal point. The value zero is represented as
    "0.0"." A float is read through its shortest round-tripping decimal
    (``repr``), so 1.5 is "1.5", not its binary expansion; a value with
    more digits than the leaf keeps is rounded to ``fraction_digits``.
    """
    if isinstance(value, bool):
        raise TypeError(f"not a decimal64: {value!r}")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"not a decimal64: {value!r}")
        value = Decimal(repr(value))
    elif isinstance(value, int):
        value = Decimal(value)
    elif not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"not a decimal64: {value!r}")
    quantum = Decimal(1).scaleb(-fraction_digits)
    scaled = value.quantize(quantum)
    steps = int(scaled.scaleb(fraction_digits))
    if not INT64_MIN <= steps <= INT64_MAX:
        raise TypeError(f"{value} is outside decimal64 with fraction-digits {fraction_digits}")
    sign = "-" if steps < 0 else ""
    whole, fraction = divmod(abs(steps), 10**fraction_digits)
    digits = f"{fraction:0{fraction_digits}d}".rstrip("0") or "0"
    return f"{sign}{whole}.{digits}"


# ---------------------------------------------------------------------------
# Input: the lexical representation, or (leniency) a JSON number
# ---------------------------------------------------------------------------


def _integer(value: Any, where: str, kind: str, low: int, high: int) -> int:
    example = '"191278125000000"' if kind == "uint64" else '"-42"'
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        number = int(value)
    elif isinstance(value, int) and not isinstance(value, bool):
        number = value  # the JSON number earlier releases sent and emitted
    else:
        raise ValueError(
            f"{where} must be a {kind}: a decimal integer string such as "
            f"{example} (RFC 7951 §6.1), got {value!r}"
        )
    if not low <= number <= high:
        raise ValueError(f"{where}={value!r} is outside the {kind} range {low}..{high}")
    return number


def parse_uint64(value: Any, where: str) -> int:
    """A uint64 request value: ``"123"`` (RFC 7951 §6.1) or the number 123.

    Refused with ``ValueError``: a fraction or exponent (``"1.0"``,
    ``"1e14"``, a JSON ``1.0``), an empty or blank string, a boolean, a
    negative or out-of-range value.
    """
    return _integer(value, where, "uint64", 0, UINT64_MAX)


def parse_int64(value: Any, where: str) -> int:
    """An int64 request value: ``"-42"`` (RFC 7951 §6.1) or the number -42."""
    return _integer(value, where, "int64", INT64_MIN, INT64_MAX)


def parse_decimal64(value: Any, fraction_digits: int, where: str) -> Decimal:
    """A decimal64 request value: ``"150.5"`` (RFC 7951 §6.1) or the number 150.5.

    The value must lie in the type's value space, "i x 10^-n" with i an
    int64 and n the leaf's ``fraction-digits`` (RFC 7950 §9.3): more
    fraction digits than that, an exponent, a non-finite or out-of-range
    value, a boolean or an empty string raise ``ValueError``.
    """
    if isinstance(value, str) and _DECIMAL.fullmatch(value):
        number = Decimal(value)
    elif isinstance(value, bool):
        number = None
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float) and math.isfinite(value):
        number = Decimal(repr(value))
    else:
        number = None
    if number is None:
        raise ValueError(
            f"{where} must be a decimal64: a decimal string such as \"150.5\" "
            f"(RFC 7951 §6.1), got {value!r}"
        )
    try:
        steps = number.scaleb(fraction_digits)
        exact = steps == steps.to_integral_value()
    except InvalidOperation:  # pragma: no cover - finite input, cannot happen
        exact = False
    if not exact:
        raise ValueError(
            f"{where}={value!r} has more than the {fraction_digits} fraction "
            "digits its decimal64 type keeps"
        )
    if not INT64_MIN <= int(steps) <= INT64_MAX:
        raise ValueError(
            f"{where}={value!r} is outside decimal64 with fraction-digits {fraction_digits}"
        )
    return number


# ---------------------------------------------------------------------------
# The T-API bodies' 64-bit leaves
# ---------------------------------------------------------------------------

_SIP = "/tapi-common:context/service-interface-point"
_SIP_SPECTRUM = (
    f"{_SIP}/tapi-photonic-media:photonic-media-service-interface-point-spec"
    "/spectrum-capability-pac"
)
_LPC = (
    "/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"
    "/end-point/layer-protocol-constraint"
)
_OTSIA = f"{_LPC}/tapi-photonic-media:otsia-connectivity-service-end-point-spec"
_MCG = f"{_LPC}/tapi-photonic-media:mcg-connectivity-service-end-point-spec"

#: Every int64, uint64 and decimal64 leaf the twin emits in a T-API body, by
#: schema path (RFC 7951 member names, module-qualified where the module
#: changes, list keys left out), with its type as tapi-photonic-media.yang
#: v2.6.0 declares it. tests/test_api/test_rfc7951_numbers.py walks every
#: T-API body against a table of all the numeric leaves of v2.6.0, so a leaf
#: the twin starts to emit and this table misses fails there.
TAPI_64BIT_LEAVES: Mapping[str, str] = {
    # spectrum-band (grouping spectrum-band): upper- and lower-frequency, uint64 Hz.
    **{
        f"{_SIP_SPECTRUM}/{band}/{edge}": "uint64"
        for band in ("supportable-spectrum", "available-spectrum", "occupied-spectrum")
        for edge in ("lower-frequency", "upper-frequency")
    },
    f"{_MCG}/number-of-mc": "uint64",
    f"{_MCG}/mc-spectrum-config-pac/spectrum/lower-frequency": "uint64",
    f"{_MCG}/mc-spectrum-config-pac/spectrum/upper-frequency": "uint64",
    f"{_OTSIA}/number-of-otsi": "uint64",
    # grouping otsi-config-pac: central-frequency uint64 Hz;
    # channel-output-power decimal64, fraction-digits 7, in 0.01 dBm.
    f"{_OTSIA}/otsi-config/central-frequency": "uint64",
    f"{_OTSIA}/otsi-config/channel-output-power": "decimal64",
}

#: Schema paths of the entries the T-API builders encode.
SIP_PATH = _SIP
CONNECTIVITY_SERVICE_PATH = (
    "/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"
)


def _encode_leaf(value: Any, kind: str, path: str) -> str:
    try:
        if kind == "uint64":
            return format_uint64(value)
        if kind == "int64":
            return format_int64(value)
        return format_decimal64(value, TAPI_FRACTION_DIGITS)
    except TypeError as exc:
        # A producer handed over something that is not a number: a bug in
        # the twin, which must not reach a client as a plausible string.
        raise TypeError(f"{path}: {exc}") from exc


def encode_64bit_leaves(
    value: Any, path: str, leaves: Mapping[str, str] = TAPI_64BIT_LEAVES,
) -> Any:
    """A copy of a T-API body subtree with its 64-bit leaves as strings.

    ``path`` is the schema path of ``value`` itself (a list entry is at its
    list's path). Containers and lists are copied, never changed in place:
    the builders share sub-dicts between end-points.
    """
    if isinstance(value, dict):
        return {
            key: encode_64bit_leaves(child, f"{path}/{key}", leaves)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [encode_64bit_leaves(item, path, leaves) for item in value]
    kind = leaves.get(path)
    if kind is None or value is None:
        return value
    return _encode_leaf(value, kind, path)
