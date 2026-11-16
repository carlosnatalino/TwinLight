"""The admission policy in effect is logged once at startup."""

from __future__ import annotations

import logging

import pytest

from twinlight.config import TwinConfig
from twinlight.state.context import TapiContext


def test_effective_rmsa_settings_are_logged(
    gnpy_twin_config: TwinConfig, caplog: pytest.LogCaptureFixture,
) -> None:
    config = gnpy_twin_config.model_copy(update={
        "rmsa": gnpy_twin_config.rmsa.model_copy(update={
            "qot_margin_db": 2.25, "k_shortest_paths": 4, "default_guardband_slots": 2,
        }),
    })
    with caplog.at_level(logging.INFO, logger="twinlight.state.context"):
        ctx = TapiContext(config)
    (record,) = [r for r in caplog.records if r.getMessage().startswith("RMSA settings")]
    message = record.getMessage()
    assert "qot_margin_db=2.25 dB" in message
    assert "k_shortest_paths=4" in message
    assert "default_guardband_slots=2 (12.50 GHz)" in message
    si = ctx._backend.spectral_defaults
    assert f"default launch power={si.power_dbm:.2f} dBm" in message
    assert si.source in message


def test_launch_power_source_without_equipment(twin_config: TwinConfig, caplog) -> None:
    """With an unreadable equipment file the default comes from gnpy's
    bundled library, and the log says so."""
    with caplog.at_level(logging.INFO, logger="twinlight.state.context"):
        TapiContext(twin_config)
    message = next(r.getMessage() for r in caplog.records
                   if r.getMessage().startswith("RMSA settings"))
    assert "example-data" in message and "eqpt_config.json" in message
