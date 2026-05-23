"""Unit tests for the runtime mutation allow-list (``physics/element_params``).

Exercises reads, writes, coercion, and validation against *real* GNPy
element instances loaded from the test fixture topology + the gnpy-bundled
equipment file. Using the real classes (rather than mocks) catches
attribute-path drift in the GNPy library.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from gnpy.core.elements import Edfa, Fiber, Roadm

from tapi_twin.physics.element_params import (
    ALLOWED,
    ParamValidationError,
    read_all,
    read_attr,
    schema,
    specs_for,
    write_attr,
)
from tapi_twin.physics.gnpy_adapter import build_gnpy_network, build_uid_map


FIXTURES = Path(__file__).parent.parent / "fixtures"
GNPY_EXAMPLE_DATA = (
    Path(__file__).resolve().parents[2]
    / "venv/lib/python3.12/site-packages/gnpy/example-data"
)


@pytest.fixture(scope="module")
def uid_map() -> dict[str, object]:
    """Real GNPy network → {uid: element} map for the test topology."""
    topology = FIXTURES / "edfa_example_network.json"
    equipment = GNPY_EXAMPLE_DATA / "eqpt_config.json"
    if not equipment.exists():
        pytest.skip(f"gnpy equipment file not installed at {equipment}")
    network, _ = build_gnpy_network(topology, equipment)
    return build_uid_map(network)


@pytest.fixture(scope="module")
def fiber(uid_map: dict[str, object]) -> Fiber:
    f = next(el for el in uid_map.values() if isinstance(el, Fiber))
    return f


@pytest.fixture(scope="module")
def edfa(uid_map: dict[str, object]) -> Edfa:
    e = next(el for el in uid_map.values() if isinstance(el, Edfa))
    return e


@pytest.fixture(scope="module")
def roadm(uid_map: dict[str, object]) -> Roadm:
    r = next(el for el in uid_map.values() if isinstance(el, Roadm))
    return r


class TestSchemaAndDiscovery:
    def test_schema_lists_all_three_element_types(self) -> None:
        s = schema()
        assert set(s) == {"Fiber", "Edfa", "Roadm"}

    def test_schema_entries_have_type_and_unit(self) -> None:
        s = schema()
        for cls_name, attrs in s.items():
            for attr, descriptor in attrs.items():
                msg = f"{cls_name}.{attr}"
                assert "type" in descriptor, msg
                assert "unit" in descriptor, msg
                assert "min" in descriptor, msg
                assert "max" in descriptor, msg

    def test_specs_for_unknown_element_returns_empty(self) -> None:
        assert specs_for(object()) == {}


class TestFiber:
    def test_loss_coef_displays_db_per_km(self, fiber: Fiber) -> None:
        # Fixture topology declares loss_coef: 0.2 (dB/km).
        v = read_attr(fiber, "loss_coef")
        assert isinstance(v, float)
        assert v == pytest.approx(0.2, abs=1e-9)

    def test_loss_coef_round_trip_via_write(self, fiber: Fiber) -> None:
        original = read_attr(fiber, "loss_coef")
        try:
            write_attr(fiber, "loss_coef", 0.27)
            assert read_attr(fiber, "loss_coef") == pytest.approx(0.27, abs=1e-9)
            # Internal storage is ndarray in dB/m.
            internal = fiber.params._loss_coef
            assert isinstance(internal, np.ndarray)
            assert float(np.asarray(internal).flat[0]) == pytest.approx(
                0.27e-3, abs=1e-12
            )
        finally:
            write_attr(fiber, "loss_coef", original)

    def test_att_in_round_trip(self, fiber: Fiber) -> None:
        original = read_attr(fiber, "att_in")
        try:
            write_attr(fiber, "att_in", 2.5)
            assert fiber.params.att_in == pytest.approx(2.5, abs=1e-9)
            assert read_attr(fiber, "att_in") == pytest.approx(2.5, abs=1e-9)
        finally:
            write_attr(fiber, "att_in", original)

    def test_loss_coef_rejects_out_of_range(self, fiber: Fiber) -> None:
        with pytest.raises(ParamValidationError, match="above maximum"):
            write_attr(fiber, "loss_coef", 99.0)
        with pytest.raises(ParamValidationError, match="below minimum"):
            write_attr(fiber, "loss_coef", -0.1)

    def test_unknown_attribute_rejected(self, fiber: Fiber) -> None:
        with pytest.raises(ParamValidationError, match="not writable"):
            write_attr(fiber, "length", 100.0)
        with pytest.raises(ParamValidationError, match="not exposed"):
            read_attr(fiber, "length")


class TestEdfa:
    def test_gain_target_round_trip_updates_effective_gain(
        self, edfa: Edfa
    ) -> None:
        original = read_attr(edfa, "gain_target")
        try:
            write_attr(edfa, "gain_target", 18.0)
            assert edfa.operational.gain_target == pytest.approx(18.0)
            # Critical: ``effective_gain`` must mirror — propagation reads it
            # in ``interpol_params`` before saturation clamping.
            assert edfa.effective_gain == pytest.approx(18.0)
            assert read_attr(edfa, "gain_target") == pytest.approx(18.0)
        finally:
            write_attr(edfa, "gain_target", original)

    def test_tilt_target_mirrors_to_element(self, edfa: Edfa) -> None:
        original = read_attr(edfa, "tilt_target")
        try:
            write_attr(edfa, "tilt_target", 0.7)
            assert edfa.operational.tilt_target == pytest.approx(0.7)
            assert edfa.tilt_target == pytest.approx(0.7)
        finally:
            # tilt_target may be None on the fixture's amp; restore via the
            # element attr directly to avoid the None check in write_attr.
            edfa.operational.tilt_target = original
            edfa.tilt_target = original

    def test_out_voa_round_trip(self, edfa: Edfa) -> None:
        original = read_attr(edfa, "out_voa")
        try:
            write_attr(edfa, "out_voa", 1.5)
            assert edfa.operational.out_voa == pytest.approx(1.5)
            assert edfa.out_voa == pytest.approx(1.5)
        finally:
            edfa.operational.out_voa = original
            edfa.out_voa = original

    def test_nf0_writes_through_to_params(self, edfa: Edfa) -> None:
        original = edfa.params.nf0
        try:
            write_attr(edfa, "nf0", 6.5)
            assert edfa.params.nf0 == pytest.approx(6.5)
        finally:
            edfa.params.nf0 = original

    def test_gain_target_rejects_string(self, edfa: Edfa) -> None:
        with pytest.raises(ParamValidationError, match="expected float"):
            write_attr(edfa, "gain_target", "20")

    def test_gain_target_rejects_bool(self, edfa: Edfa) -> None:
        # ``True`` would silently coerce to 1.0 without the bool guard.
        with pytest.raises(ParamValidationError, match="got bool"):
            write_attr(edfa, "gain_target", True)


class TestRoadm:
    def test_target_pch_out_db_round_trip(self, roadm: Roadm) -> None:
        original = read_attr(roadm, "target_pch_out_db")
        try:
            write_attr(roadm, "target_pch_out_db", -18.0)
            assert roadm.params.target_pch_out_db == pytest.approx(-18.0)
            # Element mirror — used during propagation.
            assert roadm.target_pch_out_dbm == pytest.approx(-18.0)
            assert read_attr(roadm, "target_pch_out_db") == pytest.approx(-18.0)
        finally:
            write_attr(roadm, "target_pch_out_db", original)


class TestReadAll:
    def test_returns_every_allowed_attr(
        self, fiber: Fiber, edfa: Edfa, roadm: Roadm
    ) -> None:
        assert set(read_all(fiber)) == set(ALLOWED[Fiber])
        assert set(read_all(edfa)) == set(ALLOWED[Edfa])
        assert set(read_all(roadm)) == set(ALLOWED[Roadm])
