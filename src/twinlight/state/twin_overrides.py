"""Allow-list and helpers for runtime-mutable twin configuration.

The `/config` plane exposes a small, deliberately curated subset of the
YAML-startup config that is safe to change at runtime:

* `transients.<model>.enabled` — flip a transient impairment model on
  or off without restarting; the next OPM sample reflects it because
  `apply_all_transients` reads the live config every call.
* `rmsa.qot_margin_db` — change the QoT admission margin; affects only
  *future* `add_service` admissions, not already-admitted services.

Anything else (topology paths, equipment files, transient *parameters*
other than enable flags) stays restart-only — those interact with
designed-network state, file paths, or solver caches in ways that don't
round-trip cleanly without a process restart.
"""

from __future__ import annotations

from typing import Any

from twinlight.config import TwinConfig

# Dotted attribute path → tuple of accepted Python types. The path is
# walked verbatim against the live ``TwinConfig`` (mutable pydantic
# model). Order is irrelevant; the dict is used as a set.
TWIN_ALLOWED: dict[str, tuple[type, ...]] = {
    "transients.edfa_reservoir.enabled": (bool,),
    "transients.polarization.enabled": (bool,),
    "transients.phase_noise.enabled": (bool,),
    "transients.environmental.enabled": (bool,),
    "rmsa.qot_margin_db": (int, float),
}


class TwinOverrideError(ValueError):
    """Raised when a twin-config override fails validation."""


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


def validate(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate a flat dotted-key updates dict against TWIN_ALLOWED.

    Returns the (unchanged) dict for chaining. Raises ``TwinOverrideError``
    on unknown key or wrong type. Like the element-params allow-list, we
    reject bool-where-number to avoid the ``isinstance(True, int)`` trap.
    """
    for key, value in updates.items():
        if key not in TWIN_ALLOWED:
            raise TwinOverrideError(
                f"Twin config key {key!r} is not runtime-mutable. "
                f"Allowed: {sorted(TWIN_ALLOWED)!r}"
            )
        accepted = TWIN_ALLOWED[key]
        # bool must be exact-typed: a numeric spec must reject ``True``.
        if bool not in accepted and isinstance(value, bool):
            raise TwinOverrideError(
                f"{key}: expected one of "
                f"{tuple(t.__name__ for t in accepted)}, got bool"
            )
        if not isinstance(value, accepted):
            raise TwinOverrideError(
                f"{key}: expected one of "
                f"{tuple(t.__name__ for t in accepted)}, "
                f"got {type(value).__name__}"
            )
        if key == "rmsa.qot_margin_db":
            # ``value`` has been verified above to be int or float.
            assert isinstance(value, (int, float))
            if float(value) < 0:
                raise TwinOverrideError(f"{key}={value} must be >= 0")
    return updates


def apply(config: TwinConfig, updates: dict[str, Any]) -> None:
    """Write each validated (key, value) into the live ``config`` model.

    Caller is responsible for validating first (or accepting that
    ``validate`` is called here for safety). We re-validate to keep the
    function safe to call standalone (e.g. from tests).
    """
    validate(updates)
    for key, value in updates.items():
        parent, leaf = _walk_for_write(config, key)
        setattr(parent, leaf, value)


def read_all(config: TwinConfig) -> dict[str, Any]:
    """Return every allow-listed twin-config value as a flat dotted-key dict."""
    out: dict[str, Any] = {}
    for key in TWIN_ALLOWED:
        parts = key.split(".")
        cur: Any = config
        for part in parts:
            cur = getattr(cur, part)
        out[key] = cur
    return out


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
