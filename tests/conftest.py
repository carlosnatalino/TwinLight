"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tapi_twin.app import create_app
from tapi_twin.config import GnpyConfig, TwinConfig


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def edfa_topology_path() -> Path:
    return FIXTURES / "edfa_example_network.json"


@pytest.fixture
def twin_config(edfa_topology_path: Path, tmp_path: Path) -> TwinConfig:
    """A TwinConfig pointing at the test fixture topology."""
    # Create a dummy equipment file (not used in Phase 1 but required by schema)
    eqpt = tmp_path / "equipment.json"
    eqpt.write_text("{}")
    return TwinConfig(
        gnpy=GnpyConfig(
            topology=edfa_topology_path,
            equipment=eqpt,
        ),
    )


@pytest.fixture
def app(twin_config: TwinConfig) -> TestClient:
    """FastAPI TestClient wired to our test config."""
    fastapi_app = create_app(twin_config)
    return TestClient(fastapi_app)


# ---------------------------------------------------------------------------
# Real-GNPy fixtures (M2+): the /config plane and snapshot v2 tests need a
# TapiContext where the GNPy network actually loaded, so they can verify
# baseline invalidation, GSNR shifts on parameter mutation, etc.
# ---------------------------------------------------------------------------

_GNPY_EQUIPMENT = (
    Path(__file__).resolve().parents[1]
    / "venv/lib/python3.12/site-packages/gnpy/example-data/eqpt_config.json"
)


@pytest.fixture
def gnpy_twin_config(edfa_topology_path: Path) -> TwinConfig:
    """TwinConfig that successfully loads the bundled GNPy equipment file.

    Skips the test if the equipment file isn't installed (e.g. CI image
    without the gnpy package). Use this in tests that exercise GNPy
    propagation or the ``/config`` element overrides.
    """
    if not _GNPY_EQUIPMENT.exists():
        pytest.skip(
            f"gnpy equipment file not installed at {_GNPY_EQUIPMENT}"
        )
    return TwinConfig(
        gnpy=GnpyConfig(
            topology=edfa_topology_path,
            equipment=_GNPY_EQUIPMENT,
        ),
    )


@pytest.fixture
def gnpy_context(gnpy_twin_config: TwinConfig):
    """A TapiContext with a real GNPy network loaded."""
    from tapi_twin.state.context import TapiContext

    return TapiContext(gnpy_twin_config)
