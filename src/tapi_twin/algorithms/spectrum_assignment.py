"""First-fit spectrum assignment for RMSA.

GNPy does not provide spectrum allocation; this module implements a
first-fit contiguous slot search along a path. The strategy is standard
in RMSA (cf. IMPLEMENTATION_PLAN Phase 3: "Find first-fit contiguous slot
block (with guard bands) across all links"; ONG uses similar abstractions
— "available spectrum slots per link" in Optical network emulator and
digital twin.md).
"""

from __future__ import annotations

from tapi_twin.state.spectrum_state import Edge, SpectrumState


def path_uids_to_edges(path_uids: list[str]) -> list[Edge]:
    """Convert a path (ordered list of GNPy element UIDs) to a list of edges.

    The path is the sequence of nodes traversed (e.g. Transceiver -> ...
    -> Transceiver). Each consecutive pair (path[i], path[i+1]) is one
    directed link for spectrum purposes.

    Args:
        path_uids: Ordered list of element UIDs from source to destination.

    Returns:
        List of (from_uid, to_uid) edges. Empty if path has fewer than 2 nodes.
    """
    if len(path_uids) < 2:
        return []
    return [
        (path_uids[i], path_uids[i + 1])
        for i in range(len(path_uids) - 1)
    ]


def first_fit(
    spectrum_state: SpectrumState,
    edges: list[Edge],
    slots_required: int,
    guard_slots: int,
) -> int | None:
    """Find the lowest start slot index with a contiguous free block.

    Searches from slot 0 upward. The block size is slots_required + guard_slots
    (guard slots can be used to separate adjacent channels; we place them
    after the channel so the block is [start, start + slots_required + guard_slots)).
    For simplicity we allocate one contiguous block that includes the guard;
    the "channel" occupies the first slots_required slots, the next guard_slots
    are reserved as guard band.

    Args:
        spectrum_state: Current per-link slot occupancy.
        edges: List of (from_uid, to_uid) along the path.
        slots_required: Number of slots needed for the channel.
        guard_slots: Guard band slots (e.g. 1) after the channel.

    Returns:
        The smallest start index such that [start, start + slots_required + guard_slots)
        is free on all edges, or None if no such block exists.
    """
    if not edges or slots_required <= 0:
        return None
    block = slots_required + guard_slots
    n = spectrum_state.num_slots
    if block > n:
        return None
    for start in range(n - block + 1):
        if spectrum_state.is_available(edges, start, block):
            return start
    return None
