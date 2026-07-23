"""Unit tests for first-fit spectrum assignment and path_uids_to_edges."""

from __future__ import annotations


from twinlight.algorithms.spectrum_assignment import first_fit, path_uids_to_edges
from twinlight.state.spectrum_state import SpectrumState


class TestPathUidsToEdges:
    def test_empty_path(self) -> None:
        assert path_uids_to_edges([]) == []
        assert path_uids_to_edges(["a"]) == []

    def test_two_nodes(self) -> None:
        assert path_uids_to_edges(["a", "b"]) == [("a", "b")]

    def test_three_nodes(self) -> None:
        assert path_uids_to_edges(["a", "b", "c"]) == [("a", "b"), ("b", "c")]


class TestFirstFit:
    def test_finds_start_zero_when_empty(self) -> None:
        state = SpectrumState(num_slots=64)
        edges = [("a", "b")]
        assert first_fit(state, edges, slots_required=8, guard_slots=1) == 0

    def test_returns_none_when_block_too_large(self) -> None:
        state = SpectrumState(num_slots=10)
        edges = [("a", "b")]
        assert first_fit(state, edges, slots_required=8, guard_slots=3) is None

    def test_returns_next_available_after_allocated(self) -> None:
        state = SpectrumState(num_slots=64)
        edges = [("a", "b")]
        # Allocate slots 0..8 (8+1 guard)
        state.allocate(edges, 0, 9)
        # Next block should start at 9
        assert first_fit(state, edges, slots_required=8, guard_slots=1) == 9

    def test_returns_none_when_no_contiguous_block(self) -> None:
        state = SpectrumState(num_slots=20)
        edges = [("a", "b")]
        # Fill so no block of 8+1 exists
        for start in [0, 10]:
            state.allocate(edges, start, 9)
        assert first_fit(state, edges, slots_required=8, guard_slots=1) is None
