"""Parse GNPy JSON topology format without requiring the gnpy library.

Reads the standard GNPy topology format::

    {"elements": [{"uid", "type", "params", ...}],
     "connections": [{"from_node", "to_node"}]}

This module has zero gnpy imports. When gnpy is available (Phase 2),
``physics/gnpy_adapter.py`` will use gnpy's own loader for physics
computation, but this parser remains the source for TAPI model building.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GnpyElement:
    """A single network element from the GNPy topology."""

    uid: str
    type: str  # Transceiver | Fiber | Edfa | Roadm | Fused | RamanFiber
    metadata: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    operational: dict = field(default_factory=dict)
    type_variety: str = ""


@dataclass
class GnpyConnection:
    """A directed connection between two elements."""

    from_node: str
    to_node: str


@dataclass
class GnpyTopology:
    """Parsed GNPy topology."""

    network_name: str
    elements: list[GnpyElement]
    connections: list[GnpyConnection]
    elements_by_uid: dict[str, GnpyElement]


# GNPy ``params.length`` unit spellings → metres multiplier.
# Missing ``length_units`` defaults to km (GNPy's own default).
_LENGTH_TO_METRES: dict[str, float] = {
    "km": 1_000.0,
    "kilometer": 1_000.0,
    "kilometre": 1_000.0,
    "kilometers": 1_000.0,
    "kilometres": 1_000.0,
    "m": 1.0,
    "meter": 1.0,
    "metre": 1.0,
    "meters": 1.0,
    "metres": 1.0,
}


def fiber_length_m(params: dict) -> float | None:
    """A Fiber element's ``params.length`` in metres, or None if unusable.

    GNPy stores the number in the unit named by ``params["length_units"]``;
    when that key is absent the value is kilometres (GNPy's default).
    """
    try:
        length = float(params.get("length", 0))
    except (TypeError, ValueError):
        return None
    scale = _LENGTH_TO_METRES.get(str(params.get("length_units") or "km").strip().lower())
    return None if scale is None else length * scale


def load_gnpy_topology(topology_path: Path) -> GnpyTopology:
    """Load and parse a GNPy JSON topology file.

    Args:
        topology_path: Path to the GNPy network topology JSON file.

    Returns:
        Parsed topology with elements, connections, and uid index.
    """
    return parse_gnpy_topology_dict(
        json.loads(topology_path.read_text()), name=topology_path.stem
    )


def parse_gnpy_topology_dict(data: dict, *, name: str = "") -> GnpyTopology:
    """Parse an already-decoded GNPy topology document.

    Split out from :func:`load_gnpy_topology` so a topology built in
    memory can be parsed too — the EGN backend feeds in the output of
    GNPy's ``network_to_json()`` to pick up its amplifier placement.

    Args:
        data: Decoded GNPy topology document (``elements`` + ``connections``).
        name: Fallback network name when the document omits ``network_name``.

    Returns:
        Parsed topology with elements, connections, and uid index.
    """
    network_name = data.get("network_name", name)

    elements = []
    for el in data.get("elements", []):
        elements.append(GnpyElement(
            uid=el["uid"],
            type=el["type"],
            metadata=el.get("metadata", {}),
            params=el.get("params", {}),
            operational=el.get("operational", {}),
            type_variety=el.get("type_variety", ""),
        ))

    connections = []
    for conn in data.get("connections", []):
        connections.append(GnpyConnection(
            from_node=conn["from_node"],
            to_node=conn["to_node"],
        ))

    elements_by_uid = {el.uid: el for el in elements}
    return GnpyTopology(network_name, elements, connections, elements_by_uid)
