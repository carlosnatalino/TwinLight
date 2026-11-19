"""examples/demo_services.py reads the spectrum off the twin's own bodies.

The twin sends its uint64 band edges as strings (RFC 7951 §6.1); the
stdlib-only demo script parses them, still reads the numbers a twin before
0.2.0 sent, and stops on anything else instead of printing a wrong number.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.models.connectivity import MCG_CSEP_SPEC

SCRIPT = Path(__file__).parents[2] / "examples" / "demo_services.py"
CC = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_services", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def service(tmp_path: Path) -> dict:
    """A connectivity-service as the twin's 201 returns it."""
    app = TestClient(create_app(_mesh_config(tmp_path, "egn")))
    refs = MeshRefs(app.app.state.context)
    resp = app.post(CC, json={"tapi-connectivity:connectivity-service": [{"end-point": [
        tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]}]})
    assert resp.status_code == 201, resp.text
    entry: dict = resp.json()["tapi-connectivity:connectivity-service"][0]
    return entry


def _band(service: dict) -> dict:
    band: dict = service["end-point"][0]["layer-protocol-constraint"][0][MCG_CSEP_SPEC][
        "mc-spectrum-config-pac"][0]["spectrum"]
    return band


def test_reads_the_twins_strings(service: dict) -> None:
    band = _band(service)
    assert isinstance(band["lower-frequency"], str)
    lower, upper = int(band["lower-frequency"]), int(band["upper-frequency"])
    centre, width = _load()._spectrum_of(service)
    assert centre == pytest.approx((lower + upper) / 2e12)
    assert width == pytest.approx((upper - lower) / 1e9)
    assert 191.0 < centre < 196.0 and width == pytest.approx(56.25)


def test_reads_an_earlier_twins_numbers(service: dict) -> None:
    numbers = copy.deepcopy(service)
    band = _band(numbers)
    band.update({k: int(v) for k, v in band.items()})
    assert _load()._spectrum_of(numbers) == _load()._spectrum_of(service)


@pytest.mark.parametrize("bad", ["1.9e14", "", "abc", "-1", 1.9e14, True, None])
def test_stops_on_anything_else(service: dict, bad: object) -> None:
    _band(service)["upper-frequency"] = bad
    with pytest.raises(ValueError, match="upper-frequency"):
        _load()._spectrum_of(service)
