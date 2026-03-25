"""Per-link spectrum slot state for RMSA.

This module maintains occupancy of the frequency grid on each directed link
(edge) of the optical network. GNPy does not provide spectrum allocation
algorithms; the digital twin implements its own slot-based spectrum state
and first-fit assignment (see algorithms/spectrum_assignment.py).

Design decisions:
- Spectrum is modeled as a fixed grid of slots per link (e.g. 768 slots
  at 6.25 GHz = 4.8 THz C-band). Each directed edge (from_uid, to_uid)
  in the GNPy path has an independent slot array.
- Allocation is contiguous: a service gets a block [start, start+width)
  that must be free on every link along its path. This matches
  standard flexible grid / slot-based RMSA (cf. IMPLEMENTATION_PLAN Phase 3,
  literature: Optical network emulator and digital twin — ONG observation
  space "available spectrum slots per link").
"""

from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger(__name__)


# Type alias: edge = (from_uid, to_uid) for a directed link in the path
Edge = tuple[str, str]


class SpectrumState:
    """Per-link slot occupancy for the optical network.

    Each directed edge (GNPy connection from_node -> to_node) has a boolean
    array of length num_slots; True = occupied, False = free.
    """

    def __init__(self, num_slots: int) -> None:
        """Initialise spectrum state with a global slot count.

        Args:
            num_slots: Total number of slots per link (e.g. 768 for 6.25 GHz
                slot width over C-band). Must be positive.
        """
        if num_slots < 1:
            raise ValueError("num_slots must be >= 1")
        self._num_slots = num_slots
        # Lazy allocation: only create slot arrays for edges that have
        # ever been used (on first allocation or first check).
        self._slots: dict[Edge, np.ndarray] = {}

    def _get_or_create(self, edge: Edge) -> np.ndarray:
        """Return the slot array for an edge, creating it if needed."""
        if edge not in self._slots:
            # False = free (no occupancy)
            self._slots[edge] = np.zeros(self._num_slots, dtype=bool)
        return self._slots[edge]

    def is_available(
        self,
        edges: list[Edge],
        start: int,
        width: int,
    ) -> bool:
        """Return True if the block [start, start+width) is free on all edges.

        Args:
            edges: List of (from_uid, to_uid) along the path.
            start: First slot index (inclusive).
            width: Number of contiguous slots.

        Returns:
            True if every edge has slots [start, start+width) free.
        """
        if width <= 0 or start < 0 or start + width > self._num_slots:
            return False
        for edge in edges:
            arr = self._get_or_create(edge)
            if np.any(arr[start:start + width]):
                return False
        return True

    def allocate(
        self,
        edges: list[Edge],
        start: int,
        width: int,
    ) -> bool:
        """Mark slots [start, start+width) as occupied on all edges.

        Caller must ensure the block is available (e.g. via first-fit)
        before calling allocate. This method does not check for conflicts;
        it performs an atomic allocation on all edges.

        Args:
            edges: List of (from_uid, to_uid) along the path.
            start: First slot index (inclusive).
            width: Number of contiguous slots.

        Returns:
            True if allocation was applied (always True if start/width valid).
        """
        if width <= 0 or start < 0 or start + width > self._num_slots:
            return False
        for edge in edges:
            arr = self._get_or_create(edge)
            arr[start:start + width] = True
        return True

    def release(
        self,
        edges: list[Edge],
        start: int,
        width: int,
    ) -> None:
        """Mark slots [start, start+width) as free on all edges.

        Idempotent: safe to call even if some slots were already free.
        """
        if width <= 0 or start < 0 or start + width > self._num_slots:
            return
        for edge in edges:
            if edge not in self._slots:
                continue
            arr = self._slots[edge]
            arr[start:start + width] = False

    @property
    def num_slots(self) -> int:
        """Global number of slots per link."""
        return self._num_slots

    def to_dict(self) -> dict:
        """Serialize for snapshot: num_slots and per-edge slot occupancy."""
        slots_ser: dict[str, list[int]] = {}
        for (from_uid, to_uid), arr in self._slots.items():
            key = f"{from_uid}|{to_uid}"
            slots_ser[key] = arr.astype(int).tolist()
        return {"num_slots": self._num_slots, "slots": slots_ser}

    @classmethod
    def from_dict(cls, data: dict) -> "SpectrumState":
        """Deserialize from snapshot; restores per-edge occupancy."""
        num_slots = int(data["num_slots"])
        inst = cls(num_slots)
        for key, values in data.get("slots", {}).items():
            parts = key.split("|", 1)
            if len(parts) != 2:
                continue
            from_uid, to_uid = parts[0], parts[1]
            arr = np.array(values, dtype=bool)
            if len(arr) == num_slots:
                inst._slots[(from_uid, to_uid)] = arr
        return inst
