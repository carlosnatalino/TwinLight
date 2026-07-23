"""Unit tests for per-link spectrum slot state."""

from __future__ import annotations

import pytest

from twinlight.state.spectrum_state import SpectrumState


class TestSpectrumState:
    def test_allocate_and_release(self) -> None:
        state = SpectrumState(num_slots=32)
        edges = [("a", "b"), ("b", "c")]
        state.allocate(edges, 0, 8)
        assert state.is_available(edges, 0, 8) is False
        state.release(edges, 0, 8)
        assert state.is_available(edges, 0, 8) is True

    def test_is_available_true_when_empty(self) -> None:
        state = SpectrumState(num_slots=64)
        edges = [("x", "y")]
        assert state.is_available(edges, 0, 10) is True
        assert state.is_available(edges, 50, 14) is True

    def test_is_available_false_after_allocate(self) -> None:
        state = SpectrumState(num_slots=64)
        edges = [("a", "b")]
        state.allocate(edges, 10, 5)
        assert state.is_available(edges, 10, 5) is False
        assert state.is_available(edges, 8, 5) is False  # overlaps
        assert state.is_available(edges, 16, 5) is True

    def test_num_slots_property(self) -> None:
        state = SpectrumState(num_slots=768)
        assert state.num_slots == 768

    def test_invalid_num_slots_raises(self) -> None:
        with pytest.raises(ValueError, match="num_slots"):
            SpectrumState(num_slots=0)
