"""Tests for backend selection (M2).

Verifies:
* ``physics.backend`` defaults to ``"gnpy"`` and produces a working
  ``GnpyBackend`` (this is also implicitly covered by every other test —
  here we just assert the dispatch path explicitly),
* an unknown backend name in the config raises ``ValueError``,
* selecting ``"egn"`` raises a clear ``ImportError`` *until* M4 lands
  the implementation (and after M4 lands, this test will need an
  ``importorskip`` instead — flagged below),
* the ``--physics-backend`` CLI flag overrides the YAML value.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

from twinlight.cli import load_config
from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig
from twinlight.physics.backend import build_backend
from twinlight.physics.gnpy_backend import GnpyBackend


FIXTURES = Path(__file__).parent.parent / "fixtures"
_GNPY_EQUIPMENT = (
    Path(__file__).resolve().parents[2]
    / "venv/lib/python3.12/site-packages/gnpy/example-data/eqpt_config.json"
)


class TestPhysicsConfig:
    def test_default_backend_is_gnpy(self) -> None:
        cfg = PhysicsConfig()
        assert cfg.backend == "gnpy"

    def test_unknown_backend_rejected_by_pydantic(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PhysicsConfig(backend="random-walk")  # type: ignore[arg-type]


class TestBuildBackend:
    def _gnpy_config(self) -> TwinConfig:
        if not _GNPY_EQUIPMENT.exists():
            pytest.skip(f"gnpy equipment not installed at {_GNPY_EQUIPMENT}")
        return TwinConfig(
            gnpy=GnpyConfig(
                topology=FIXTURES / "edfa_example_network.json",
                equipment=_GNPY_EQUIPMENT,
            ),
        )

    def test_gnpy_dispatch_returns_gnpybackend(self) -> None:
        cfg = self._gnpy_config()
        assert cfg.physics.backend == "gnpy"
        backend = build_backend(cfg)
        assert isinstance(backend, GnpyBackend)
        assert backend.name == "gnpy"
        assert backend.available is True

    def test_egn_dispatch_returns_egnbackend(self) -> None:
        from twinlight.physics.egn_backend import EgnBackend

        cfg = self._gnpy_config()
        cfg.physics.backend = "egn"
        backend = build_backend(cfg)
        assert isinstance(backend, EgnBackend)
        assert backend.name == "egn"
        assert backend.available is True


class TestCliOverride:
    def _argv(self, *args: str) -> list[str]:
        return [
            "--topology", str(FIXTURES / "edfa_example_network.json"),
            *args,
        ]

    def test_default_when_flag_not_passed(self) -> None:
        cfg = load_config(self._argv())
        assert cfg.physics.backend == "gnpy"

    def test_cli_flag_sets_backend(self) -> None:
        cfg = load_config(self._argv("--physics-backend", "egn"))
        assert cfg.physics.backend == "egn"

    def test_cli_flag_rejects_unknown_value(self) -> None:
        # argparse should reject anything outside choices=[gnpy, egn].
        with patch("sys.stderr", new_callable=io.StringIO):
            with pytest.raises(SystemExit):
                load_config(self._argv("--physics-backend", "manakov"))
