"""Allow-list of mutable physical-layer attributes on GNPy elements.

Used by the runtime ``/config`` endpoints (see ``api/config.py``) and by
``TapiContext.apply_element_overrides`` to validate and write parameter
changes to live GNPy element objects. Each spec captures:

* the user-facing value type and accepted range,
* where to *read* the current value from (a dotted attribute path),
* where to *write* the new value to (one or more dotted paths, because
  GNPy mirrors some operational fields onto the element instance in
  addition to its ``params``/``operational`` sub-objects), and
* the small coerce/display transforms needed when GNPy stores the
  value in different internal units than the user sees (notably
  ``Fiber.loss_coef``, which is exposed in dB/km but stored as an
  ndarray in dB/m on ``params._loss_coef``).

The single source of truth for what may be mutated is the ``ALLOWED``
mapping at the bottom of this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from gnpy.core.elements import Edfa, Fiber, Roadm


class ParamValidationError(ValueError):
    """Raised when a config mutation fails validation."""


@dataclass(frozen=True)
class AttrSpec:
    """Describes one writable physical-layer attribute.

    Attributes:
        py_type: Expected Python type of the *user-facing* value.
        min_value: Inclusive lower bound on the user-facing value, or None.
        max_value: Inclusive upper bound on the user-facing value, or None.
        read_path: Dotted attribute path used to read the current value
            for ``GET /config``. May target a "private" attribute when the
            public property has no setter (e.g. ``params._loss_coef``).
        write_paths: Dotted attribute paths to which the coerced value is
            written. More than one entry is used when GNPy mirrors a value
            onto the element instance in addition to its sub-object (e.g.
            ``operational.tilt_target`` and ``tilt_target`` on Edfa).
        unit: Free-form unit label for documentation.
        coerce: User-value → internal-value transform (applied once, before
            writing to every path). Default: cast to ``py_type``.
        display: Internal-value → user-value transform (applied to the value
            read from ``read_path``). Default: cast to ``py_type``.
        reader: ``element → user value``, replacing ``read_path`` for an
            attribute that is derived rather than stored (``nf_offset``).
        writer: ``(element, coerced value) → None``, replacing
            ``write_paths`` for the same kind of attribute.
    """

    py_type: type
    read_path: str
    write_paths: tuple[str, ...]
    min_value: float | None = None
    max_value: float | None = None
    unit: str = ""
    coerce: Callable[[Any], Any] = field(default=lambda v: v)
    display: Callable[[Any], Any] = field(default=lambda v: v)
    reader: Callable[[Any], Any] | None = None
    writer: Callable[[Any, Any], None] | None = None


def _walk(obj: Any, dotted: str) -> tuple[Any, str]:
    """Return (parent_obj, leaf_attr_name) for a dotted path.

    For ``"params.loss_coef"`` against an element ``el`` returns
    ``(el.params, "loss_coef")``. Raises AttributeError on a missing
    intermediate.
    """
    parts = dotted.split(".")
    parent: Any = obj
    for part in parts[:-1]:
        parent = getattr(parent, part)
    return parent, parts[-1]


def _scalar_loss_coef_display(v: Any) -> float:
    """``params._loss_coef`` is an ndarray in dB/m; show as dB/km float."""
    return float(np.asarray(v).flat[0]) * 1e3


def _scalar_loss_coef_coerce(v: Any) -> np.ndarray:
    """User-facing dB/km float → internal 0-d ndarray in dB/m."""
    return np.asarray(float(v) * 1e-3)


# EDFA noise-figure offset. GNPy derives an amplifier's NF from its equipment
# model (``params.nf_model``: nf1/nf2 for variable gain, nf0 for fixed gain)
# at the operating gain, then adds the per-channel ``params.nf_ripple`` when it
# propagates (``Edfa._calc_nf``). ``params.nf0`` is an equipment-library input
# that propagation never reads, so it cannot serve as a runtime knob. A
# frequency-flat offset folded into the ripple raises or lowers the modelled NF
# at every operating point instead, for every ``type_def`` — the shape an
# ageing pump gives. The ripple GNPy loaded is kept on the element so the
# offset is always applied to it, never stacked on a previous offset.
_NF_OFFSET = "_twinlight_nf_offset"
_NF_RIPPLE_BASE = "_twinlight_nf_ripple_base"


def _read_nf_offset(element: Any) -> float:
    return float(getattr(element, _NF_OFFSET, 0.0))


def _write_nf_offset(element: Any, offset: float) -> None:
    base = getattr(element, _NF_RIPPLE_BASE, None)
    if base is None:
        base = np.array(element.params.nf_ripple, dtype=float)
        setattr(element, _NF_RIPPLE_BASE, base)
    # A new array, never an in-place add: the loaded ripple may be shared.
    element.params.nf_ripple = base + offset
    setattr(element, _NF_OFFSET, offset)


# ---------------------------------------------------------------------------
# ALLOW-LIST
# ---------------------------------------------------------------------------
# Per-class mapping of {user-visible attribute name → AttrSpec}. Anything not
# listed here is rejected by ``write_attr``. Bounds are deliberately loose —
# they reject clearly nonsensical inputs (e.g. negative loss) without
# preventing legitimate operator experiments.

ALLOWED: dict[type, dict[str, AttrSpec]] = {
    Fiber: {
        "loss_coef": AttrSpec(
            py_type=float,
            read_path="params._loss_coef",
            write_paths=("params._loss_coef",),
            min_value=0.0,
            max_value=2.0,
            unit="dB/km",
            coerce=_scalar_loss_coef_coerce,
            display=_scalar_loss_coef_display,
        ),
        "att_in": AttrSpec(
            py_type=float,
            read_path="params.att_in",
            write_paths=("params.att_in",),
            min_value=0.0,
            max_value=20.0,
            unit="dB",
            coerce=float,
            display=float,
        ),
    },
    # The operational knobs are read from the element instance, which is what
    # ``Edfa.propagate`` uses. ``operational.*`` is the topology-file input:
    # ``designed_network`` sets the element values (a 16 dB gain_target in
    # the file may run at an effective 18 dB, and a booster with no gain in
    # the file still has one), and GNPy's own ``to_json`` exports the element
    # values for the same reason.
    Edfa: {
        "gain_target": AttrSpec(
            py_type=float,
            read_path="effective_gain",
            write_paths=("operational.gain_target", "effective_gain"),
            min_value=0.0,
            max_value=40.0,
            unit="dB",
            coerce=float,
            display=float,
        ),
        "tilt_target": AttrSpec(
            py_type=float,
            read_path="tilt_target",
            write_paths=("operational.tilt_target", "tilt_target"),
            min_value=-5.0,
            max_value=5.0,
            unit="dB",
            coerce=float,
            display=float,
        ),
        "out_voa": AttrSpec(
            py_type=float,
            read_path="out_voa",
            write_paths=("operational.out_voa", "out_voa"),
            min_value=0.0,
            max_value=20.0,
            unit="dB",
            coerce=float,
            display=float,
        ),
        "nf_offset": AttrSpec(
            py_type=float,
            read_path="",
            write_paths=(),
            min_value=-3.0,
            max_value=10.0,
            unit="dB",
            coerce=float,
            display=float,
            reader=_read_nf_offset,
            writer=_write_nf_offset,
        ),
    },
    Roadm: {
        "target_pch_out_db": AttrSpec(
            py_type=float,
            read_path="params.target_pch_out_db",
            write_paths=("params.target_pch_out_db", "target_pch_out_dbm"),
            min_value=-25.0,
            max_value=0.0,
            unit="dBm",
            coerce=float,
            display=float,
        ),
    },
}


def specs_for(element: Any) -> dict[str, AttrSpec]:
    """Return the AttrSpec mapping for the element's exact class, or {}."""
    return ALLOWED.get(type(element), {})


def read_attr(element: Any, attr: str) -> Any:
    """Read an allow-listed attribute as a JSON-friendly user-facing value.

    Raises:
        ParamValidationError: If ``attr`` is not writable on this element
            class (so we don't expose attributes the user can't also set).
    """
    specs = specs_for(element)
    spec = specs.get(attr)
    if spec is None:
        raise ParamValidationError(
            f"{type(element).__name__} {element.uid!r}: "
            f"attribute {attr!r} is not exposed"
        )
    if spec.reader is not None:
        return spec.reader(element)
    parent, leaf = _walk(element, spec.read_path)
    raw = getattr(parent, leaf)
    if raw is None:
        # An optional field the topology and design left unset (e.g. an
        # amplifier's tilt). Surface ``None`` to the API rather than
        # crashing the read.
        return None
    return spec.display(raw)


def read_all(element: Any) -> dict[str, Any]:
    """Return every allow-listed attribute → current display value."""
    out: dict[str, Any] = {}
    for attr in specs_for(element):
        out[attr] = read_attr(element, attr)
    return out


def write_attr(element: Any, attr: str, value: Any) -> Any:
    """Validate ``value`` and write it through to every spec ``write_path``.

    Returns the coerced internal value that was written (useful for tests).

    Raises:
        ParamValidationError: On unknown attribute, wrong type, or
            out-of-range value.
    """
    specs = specs_for(element)
    spec = specs.get(attr)
    if spec is None:
        cls = type(element).__name__
        raise ParamValidationError(
            f"{cls} {element.uid!r}: attribute {attr!r} is not writable. "
            f"Allowed: {sorted(specs)!r}"
        )

    # bools are a subclass of int, so reject them explicitly when a number
    # is expected — otherwise ``True`` would silently coerce to 1.0.
    if spec.py_type in (int, float) and isinstance(value, bool):
        raise ParamValidationError(
            f"{type(element).__name__} {element.uid!r}.{attr}: "
            f"expected {spec.py_type.__name__}, got bool"
        )
    if not isinstance(value, (int, float)) and spec.py_type in (int, float):
        raise ParamValidationError(
            f"{type(element).__name__} {element.uid!r}.{attr}: "
            f"expected {spec.py_type.__name__}, got {type(value).__name__}"
        )

    numeric = float(value)
    if spec.min_value is not None and numeric < spec.min_value:
        raise ParamValidationError(
            f"{type(element).__name__} {element.uid!r}.{attr}={numeric} "
            f"below minimum {spec.min_value} {spec.unit}"
        )
    if spec.max_value is not None and numeric > spec.max_value:
        raise ParamValidationError(
            f"{type(element).__name__} {element.uid!r}.{attr}={numeric} "
            f"above maximum {spec.max_value} {spec.unit}"
        )

    coerced = spec.coerce(value)
    if spec.writer is not None:
        spec.writer(element, coerced)
        return coerced
    for path in spec.write_paths:
        parent, leaf = _walk(element, path)
        setattr(parent, leaf, coerced)
    return coerced


def schema() -> dict[str, dict[str, dict[str, Any]]]:
    """Machine-readable description of the allow-list, for GET /config.

    Returns a nested dict keyed by element class name → attr name → spec
    descriptor (type, range, unit). Stable JSON-friendly types only.
    """
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for cls, attrs in ALLOWED.items():
        out[cls.__name__] = {
            attr: {
                "type": spec.py_type.__name__,
                "unit": spec.unit,
                "min": spec.min_value,
                "max": spec.max_value,
            }
            for attr, spec in attrs.items()
        }
    return out
