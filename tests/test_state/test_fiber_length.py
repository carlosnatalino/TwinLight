"""Fiber span length resolution for path distance reporting.

The ``gnpy`` backend exposes live GNPy elements whose ``params.length`` is in
metres; the ``egn`` backend builds no GNPy network at all, so the only source
of span lengths is the parsed topology JSON, which carries the file's own
``length`` plus a ``length_units`` field. Both must yield the same kilometre
figure, otherwise ``/internal/services/{uuid}`` reports a null distance
whenever the EGN backend is selected.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from twinlight.loader.gnpy_topology import GnpyElement
from twinlight.state.context import _fiber_length_km


def _gnpy_fiber(length_m: float) -> SimpleNamespace:
    """Stand-in for gnpy.core.elements.Fiber (params.length is in metres)."""
    return SimpleNamespace(params=SimpleNamespace(length=length_m))


def _parsed_fiber(**params: object) -> GnpyElement:
    return GnpyElement(uid="fiber A->B", type="Fiber", params=dict(params))


class TestGnpyElementSource:
    def test_metres_are_converted_to_km(self) -> None:
        assert _fiber_length_km(_gnpy_fiber(80_000.0), None) == pytest.approx(80.0)

    def test_gnpy_element_wins_over_parsed(self) -> None:
        # The live element reflects any design-time span splitting, so it is
        # the more accurate of the two when both are present.
        length = _fiber_length_km(_gnpy_fiber(50_000.0), _parsed_fiber(length=80, length_units="km"))
        assert length == pytest.approx(50.0)

    def test_falls_back_when_element_has_no_length(self) -> None:
        broken = SimpleNamespace(params=SimpleNamespace())
        assert _fiber_length_km(broken, _parsed_fiber(length=80, length_units="km")) == pytest.approx(80.0)


class TestParsedTopologySource:
    """The EGN path: no GNPy element exists, only the parsed JSON."""

    def test_km_units_are_used_as_is(self) -> None:
        assert _fiber_length_km(None, _parsed_fiber(length=336.951, length_units="km")) == pytest.approx(336.951)

    def test_length_units_defaults_to_km(self) -> None:
        assert _fiber_length_km(None, _parsed_fiber(length=80)) == pytest.approx(80.0)

    @pytest.mark.parametrize("units", ["m", "M", "meter", "metres", " m "])
    def test_metre_units_are_converted(self, units: str) -> None:
        assert _fiber_length_km(None, _parsed_fiber(length=80_000, length_units=units)) == pytest.approx(80.0)

    def test_string_length_is_accepted(self) -> None:
        assert _fiber_length_km(None, _parsed_fiber(length="80", length_units="km")) == pytest.approx(80.0)


class TestUnknownLength:
    """A span of unknown length must be skipped, never counted as zero."""

    @pytest.mark.parametrize(
        "parsed",
        [
            None,
            _parsed_fiber(),                       # no length key
            _parsed_fiber(length=None),            # explicit null
            _parsed_fiber(length="not-a-number"),
        ],
    )
    def test_returns_none(self, parsed: GnpyElement | None) -> None:
        assert _fiber_length_km(None, parsed) is None

    def test_params_not_a_dict(self) -> None:
        assert _fiber_length_km(None, SimpleNamespace(params="unexpected")) is None
