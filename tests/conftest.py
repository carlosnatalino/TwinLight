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
