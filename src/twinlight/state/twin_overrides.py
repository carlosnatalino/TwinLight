"""Allow-list and helpers for runtime-mutable twin configuration.

The `/config` plane and the transparent controller (`/transparent`) expose
the part of the YAML-startup config that is safe to change at runtime: every
scalar field of the sections that are read *live*, on each use —

* ``transients.*`` — the four time-varying impairment models. Every OPM
  sample calls ``apply_all_transients`` with the live config, so a change
  shows on the next read. The EDFA reservoir's time constants are captured
  per add/drop event, so they apply from the next event on.
* ``fec.*`` — the post-FEC BER models, also evaluated per OPM sample. The
  hard-decision code's cross-field rules (k < n, Singleton bound) are
  checked against the whole section as it would be after the change.
* ``rmsa.*`` — admission policy; affects only *future* admissions, never a
  service already admitted.

Fields that no model reads (:data:`INERT`) stay read-only: accepting a write
that changes nothing would mislead a client into thinking it had an effect.

Everything else (topology and equipment files, the backend, the spectrum
grid, server and logging settings) stays restart-only — those interact with
designed-network state, file paths or slot arrays sized at startup in ways
that don't round-trip without a process restart.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Literal, get_args, get_origin

import annotated_types
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from twinlight.config import TwinConfig

# Sections whose every scalar field is read live on each use.
RUNTIME_SECTIONS: tuple[str, ...] = ("transients", "rmsa", "fec")

# Fields in those sections that no code reads. Kept out of TWIN_ALLOWED (and
# reported as such by the transparent controller's schema) so that a write
# cannot appear to succeed while changing nothing.
INERT: dict[str, str] = {
    "transients.edfa_reservoir.solver_method": "not read by any model",
    "transients.edfa_reservoir.solver_max_step_ms": "not read by any model",
    "transients.polarization.pmd_coeff_ps_per_sqrt_km": (
        "not read by any model: PMD comes from the physical backend's "
        "per-fiber pmd_coef"
    ),
    "transients.polarization.sop_drift_rate_rad_per_s": "not read by any model",
}


class TwinOverrideError(ValueError):
    """Raised when a twin-config override fails validation."""


# -- Introspection of the pydantic config tree --------------------------------


def _unwrap_optional(annotation: Any) -> Any:
    """``X | None`` → ``X``; anything else unchanged."""
    if get_origin(annotation) in (typing.Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def section_model(model: type[BaseModel], name: str) -> type[BaseModel] | None:
    """The pydantic model class of sub-section ``name``, or None for a leaf."""
    field = model.model_fields.get(name)
    if field is None:
        return None
    annotation = _unwrap_optional(field.annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def iter_leaves(
    model: type[BaseModel] = TwinConfig, prefix: str = "",
) -> list[tuple[str, FieldInfo]]:
    """Every scalar field under ``model`` as ``(dotted key, FieldInfo)``.

    Excluded fields (``exclude=True``: process flags such as ``reset``) are
    not configuration and are skipped.
    """
    out: list[tuple[str, FieldInfo]] = []
    for name, field in model.model_fields.items():
        if field.exclude:
            continue
        key = f"{prefix}.{name}" if prefix else name
        sub = section_model(model, name)
        if sub is not None:
            out.extend(iter_leaves(sub, key))
        else:
            out.append((key, field))
    return out


def leaf_types(field: FieldInfo) -> tuple[type, ...]:
    """Python types a JSON value for this field may have."""
    annotation = _unwrap_optional(field.annotation)
    if annotation is bool:
        return (bool,)
    if annotation is int:
        return (int,)
    if annotation is float:
        return (int, float)
    if get_origin(annotation) is Literal or annotation is str:
        return (str,)
    return (object,)


def leaf_choices(field: FieldInfo) -> list[Any] | None:
    """Allowed values of a ``Literal`` field, else None."""
    annotation = _unwrap_optional(field.annotation)
    if get_origin(annotation) is Literal:
        return list(get_args(annotation))
    return None


def leaf_bounds(field: FieldInfo) -> dict[str, float]:
    """``{"ge"|"gt"|"le"|"lt": bound}`` from the field's constraints."""
    out: dict[str, float] = {}
    for meta in field.metadata:
        for attr in ("ge", "gt", "le", "lt"):
            if isinstance(meta, getattr(annotated_types, attr.capitalize())):
                out[attr] = getattr(meta, attr)
    return out


def _runtime_allowed() -> dict[str, tuple[type, ...]]:
    allowed: dict[str, tuple[type, ...]] = {}
    for key, field in iter_leaves():
        if key.split(".", 1)[0] not in RUNTIME_SECTIONS or key in INERT:
            continue
        allowed[key] = leaf_types(field)
    return allowed


# Dotted attribute path → tuple of accepted Python types. The path is
# walked verbatim against the live ``TwinConfig`` (mutable pydantic model).
TWIN_ALLOWED: dict[str, tuple[type, ...]] = _runtime_allowed()


# -- Validation and application ------------------------------------------------


def flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict into dotted-key form.

    ``{"transients": {"phase_noise": {"enabled": False}}}`` →
    ``{"transients.phase_noise.enabled": False}``.

    Non-dict leaves end the recursion; lists are treated as leaves.
    """
    flat: dict[str, Any] = {}
    for key, value in d.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, full))
        else:
            flat[full] = value
    return flat


def _walk_for_write(config: TwinConfig, dotted: str) -> tuple[Any, str]:
    parts = dotted.split(".")
    parent: Any = config
    for part in parts[:-1]:
        parent = getattr(parent, part)
    return parent, parts[-1]


def read_key(config: TwinConfig, dotted: str) -> Any:
    """Read one dotted key from a config object."""
    parent, leaf = _walk_for_write(config, dotted)
    return getattr(parent, leaf)


_BOUND_TEXT = {
    "greater_than_equal": (">=", "ge"),
    "greater_than": (">", "gt"),
    "less_than_equal": ("<=", "le"),
    "less_than": ("<", "lt"),
}


def _describe_error(key: str, value: Any, err: dict[str, Any]) -> str:
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind in _BOUND_TEXT:
        op, name = _BOUND_TEXT[kind]
        return f"{key}={value!r} must be {op} {ctx.get(name)}"
    return f"{key}={value!r}: {err.get('msg', 'invalid')}"


def validate(
    updates: dict[str, Any], config: TwinConfig | None = None,
) -> dict[str, Any]:
    """Validate a flat dotted-key updates dict against TWIN_ALLOWED.

    Checks, collecting every error before raising:

    * the key is runtime-mutable;
    * the value's JSON type (``True`` is refused where a number is expected,
      to avoid the ``isinstance(True, int)`` trap);
    * the field's own constraints (``ge``/``gt``/``le``/``lt``, ``Literal``
      choices), taken from the pydantic model rather than restated here;
    * cross-field rules of the section, evaluated on the section as it
      would be with *all* the updates applied — so ``k`` and ``n`` of the
      hard-decision code can move together in one batch.

    ``config`` supplies the current values of fields the batch leaves
    alone; without it the section defaults are used.

    Returns the (unchanged) dict for chaining. Raises ``TwinOverrideError``.
    """
    errors: list[str] = []
    by_section: dict[str, dict[str, Any]] = {}
    for key, value in updates.items():
        if key not in TWIN_ALLOWED:
            hint = f" ({INERT[key]})" if key in INERT else ""
            errors.append(
                f"Twin config key {key!r} is not runtime-mutable{hint}. "
                f"Runtime-mutable: the scalar fields of "
                f"{', '.join(RUNTIME_SECTIONS)} (listed by GET /config/get "
                f"as 'allowed_twin_keys')"
            )
            continue
        accepted = TWIN_ALLOWED[key]
        if bool not in accepted and isinstance(value, bool):
            errors.append(
                f"{key}: expected one of "
                f"{tuple(t.__name__ for t in accepted)}, got bool"
            )
            continue
        if not isinstance(value, accepted):
            errors.append(
                f"{key}: expected one of "
                f"{tuple(t.__name__ for t in accepted)}, "
                f"got {type(value).__name__}"
            )
            continue
        section, leaf = key.rsplit(".", 1)
        by_section.setdefault(section, {})[leaf] = value

    for section, leaves in by_section.items():
        model: type[BaseModel] = TwinConfig
        for part in section.split("."):
            sub = section_model(model, part)
            assert sub is not None, section
            model = sub
        current: dict[str, Any] = (
            read_key(config, section).model_dump() if config is not None
            else model().model_dump()
        )
        try:
            model.model_validate({**current, **leaves})
        except ValidationError as exc:
            for err in exc.errors():
                loc = err.get("loc") or ()
                leaf = str(loc[0]) if loc else ""
                if leaf in leaves:
                    errors.append(
                        _describe_error(f"{section}.{leaf}", leaves[leaf], dict(err))
                    )
                else:  # a model-level rule spanning several fields
                    errors.append(f"{section}: {err.get('msg', 'invalid')}")
    if errors:
        raise TwinOverrideError("; ".join(errors))
    return updates


def apply(config: TwinConfig, updates: dict[str, Any]) -> None:
    """Write each validated (key, value) into the live ``config`` model.

    Re-validates first, so the function is safe to call standalone (e.g.
    from tests). Nothing is written unless the whole batch is valid.
    """
    validate(updates, config)
    for key, value in updates.items():
        parent, leaf = _walk_for_write(config, key)
        setattr(parent, leaf, value)


def read_all(config: TwinConfig) -> dict[str, Any]:
    """Return every allow-listed twin-config value as a flat dotted-key dict."""
    return {key: read_key(config, key) for key in TWIN_ALLOWED}


def to_nested(flat: dict[str, Any]) -> dict[str, Any]:
    """Inverse of ``flatten``: dotted-key → nested dict (for JSON responses)."""
    nested: dict[str, Any] = {}
    for key, value in flat.items():
        parts = key.split(".")
        cur = nested
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
    return nested
