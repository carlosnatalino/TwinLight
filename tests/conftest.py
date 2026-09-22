"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.config import GnpyConfig, TwinConfig
from twinlight.example_data import find_example_file
from twinlight.models.connectivity import OTSIA_CSEP_SPEC
from twinlight.physics.modulation import MODULATION_TO_MT, ModulationFormat

FIXTURES = Path(__file__).parent / "fixtures"


def tapi_end_point(
    local_id: str,
    sip_uuid: str,
    modulation: str = "DP-QPSK",
) -> dict:
    """A connectivity-service end-point in T-API v2.6.0 shape.

    T-API expresses modulation as a ``tapi-photonic-media`` augment on the
    end-point's layer-protocol-constraint, never as a field on the service
    itself. Built here once so the modules that create services do not each
    re-encode the augment — and so a future shape change is one edit.
    """
    return {
        "local-id": local_id,
        "service-interface-point": {
            "service-interface-point-uuid": sip_uuid,
        },
        "layer-protocol-constraint": [
            {
                "local-id": "otsi",
                OTSIA_CSEP_SPEC: {
                    "otsi-config": [
                        {
                            "local-id": "1",
                            "modulation": {
                                "standard-modulation-technique":
                                    MODULATION_TO_MT[
                                        ModulationFormat(modulation)
                                    ],
                            },
                        },
                    ],
                },
            },
        ],
    }


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

# Resolved from the installed gnpy package, never from a hardcoded virtualenv
# path: a fixed "venv/..." path silently skips every GNPy-backed test whenever
# the layout differs (CI builds a ".venv/" via uv), hiding failures in a green run.
_GNPY_EQUIPMENT = find_example_file("eqpt_config.json")


@pytest.fixture
def gnpy_twin_config(edfa_topology_path: Path) -> TwinConfig:
    """TwinConfig that successfully loads the bundled GNPy equipment file.

    Skips the test if the equipment file isn't installed (e.g. CI image
    without the gnpy package). Use this in tests that exercise GNPy
    propagation or the ``/config`` element overrides.
    """
    if _GNPY_EQUIPMENT is None:
        pytest.skip("gnpy is not installed with its example-data equipment library")
    return TwinConfig(
        gnpy=GnpyConfig(
            topology=edfa_topology_path,
            equipment=_GNPY_EQUIPMENT,
        ),
    )


@pytest.fixture
def gnpy_context(gnpy_twin_config: TwinConfig):
    """A TapiContext with a real GNPy network loaded."""
    from twinlight.state.context import TapiContext

    return TapiContext(gnpy_twin_config)


@pytest.fixture
def gnpy_app(gnpy_twin_config: TwinConfig) -> TestClient:
    """FastAPI TestClient with a real GNPy network loaded.

    Use this for endpoint tests that need GNPy-dependent features
    (/config, OPM with real baselines, etc.).
    """
    return TestClient(create_app(gnpy_twin_config))
