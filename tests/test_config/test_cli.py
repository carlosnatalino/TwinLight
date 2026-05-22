"""Tests for CLI parsing, YAML loading, merge precedence, and validation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from tapi_twin.cli import build_parser, load_config, _resolve_paths
from tapi_twin.config import ClockMode, LogLevel


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helper to write a temp YAML config
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, data: dict, name: str = "config.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.dump(data, default_flow_style=False))
    return p


def _write_text(tmp_path: Path, content: str, name: str = "config.yaml") -> Path:
    p = tmp_path / name
    p.write_text(dedent(content))
    return p


# ===========================================================================
# CLI-only mode
# ===========================================================================

class TestCLIOnly:
    """Test the --topology / --equipment backward-compatible mode."""

    def test_minimal_topology_only(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        config = load_config(["--topology", str(topo)])
        assert config.gnpy.topology == topo.resolve()
        assert config.gnpy.equipment is None
        # Defaults applied
        assert config.server.rest_port == 8080
        assert config.simulation.clock_mode == ClockMode.WALL
        assert config.rmsa.k_shortest_paths == 3
        assert config.spectrum.num_slots == 768
        assert config.logging.level == LogLevel.INFO

    def test_topology_and_equipment(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        eqpt = tmp_path / "eqpt.json"
        topo.write_text("{}")
        eqpt.write_text("{}")
        config = load_config(["--topology", str(topo), "-e", str(eqpt)])
        assert config.gnpy.topology == topo.resolve()
        assert config.gnpy.equipment == eqpt.resolve()

    def test_all_gnpy_flags(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        eqpt = tmp_path / "eqpt.json"
        sim = tmp_path / "sim.json"
        extra1 = tmp_path / "extra1.json"
        extra2 = tmp_path / "extra2.json"
        for f in (topo, eqpt, sim, extra1, extra2):
            f.write_text("{}")

        config = load_config([
            "--topology", str(topo),
            "-e", str(eqpt),
            "--sim-params", str(sim),
            "--no-insert-edfas",
            "--extra-equipment", str(extra1),
            "--extra-config", str(extra2),
        ])
        assert config.gnpy.topology == topo.resolve()
        assert config.gnpy.equipment == eqpt.resolve()
        assert config.gnpy.sim_params == sim.resolve()
        assert config.gnpy.no_insert_edfas is True
        assert len(config.gnpy.extra_equipment) == 1
        assert len(config.gnpy.extra_config) == 1

    def test_server_overrides(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        config = load_config([
            "--topology", str(topo),
            "--rest-host", "127.0.0.1",
            "--rest-port", "9090",
            "--grpc-port", "50052",
        ])
        assert config.server.rest_host == "127.0.0.1"
        assert config.server.rest_port == 9090
        assert config.server.grpc_port == 50052

    def test_simulation_overrides(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        config = load_config([
            "--topology", str(topo),
            "--clock-mode", "accelerated",
            "--time-scale", "10.0",
        ])
        assert config.simulation.clock_mode == ClockMode.ACCELERATED
        assert config.simulation.time_scale == 10.0

    def test_rmsa_overrides(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        config = load_config([
            "--topology", str(topo),
            "--k-shortest-paths", "5",
            "--qot-margin-db", "2.5",
            "--guardband-slots", "2",
        ])
        assert config.rmsa.k_shortest_paths == 5
        assert config.rmsa.qot_margin_db == 2.5
        assert config.rmsa.default_guardband_slots == 2

    def test_spectrum_overrides(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        config = load_config([
            "--topology", str(topo),
            "--num-slots", "384",
            "--slot-width-ghz", "12.5",
        ])
        assert config.spectrum.num_slots == 384
        assert config.spectrum.slot_width_ghz == 12.5

    def test_verbose_v(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        config = load_config(["--topology", str(topo), "-v"])
        assert config.logging.level == LogLevel.INFO

    def test_verbose_vv(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        config = load_config(["--topology", str(topo), "-vv"])
        assert config.logging.level == LogLevel.DEBUG

    def test_log_level_explicit(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        config = load_config(["--topology", str(topo), "--log-level", "ERROR"])
        assert config.logging.level == LogLevel.ERROR

    def test_restore_path(self, tmp_path: Path) -> None:
        topo = tmp_path / "net.json"
        snap = tmp_path / "snap.json"
        topo.write_text("{}")
        snap.write_text("{}")
        config = load_config(["--topology", str(topo), "--restore", str(snap)])
        assert config.restore_path == snap

    def test_restore_latest_uses_most_recent_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir()
        older = snap_dir / "twin-old.json"
        newer = snap_dir / "twin-new.json"
        older.write_text("{}")
        newer.write_text("{}")
        monkeypatch.chdir(tmp_path)
        config = load_config(["--topology", str(topo), "--restore-latest"])
        assert config.restore_path is not None
        assert Path(config.restore_path).name == "twin-new.json"

    def test_restore_latest_errors_when_no_snapshots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        topo = tmp_path / "net.json"
        topo.write_text("{}")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            load_config(["--topology", str(topo), "--restore-latest"])


# ===========================================================================
# YAML-only mode
# ===========================================================================

class TestYAMLOnly:
    """Test --config with YAML file."""

    def test_load_sample_fixture(self, tmp_path: Path) -> None:
        # Create dummy files that the config references
        (tmp_path / "dummy_topology.json").write_text("{}")
        (tmp_path / "dummy_equipment.json").write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {
                "topology": "dummy_topology.json",
                "equipment": "dummy_equipment.json",
            },
            "server": {"rest_port": 9090},
            "simulation": {"clock_mode": "accelerated", "time_scale": 10.0},
            "rmsa": {"k_shortest_paths": 5, "qot_margin_db": 2.0},
        })
        config = load_config(["--config", str(cfg_path)])
        assert config.server.rest_port == 9090
        assert config.simulation.clock_mode == ClockMode.ACCELERATED
        assert config.simulation.time_scale == 10.0
        assert config.rmsa.k_shortest_paths == 5
        assert config.rmsa.qot_margin_db == 2.0
        # Defaults still applied for unspecified sections
        assert config.spectrum.num_slots == 768
        assert config.transients.edfa_reservoir.enabled is True

    def test_relative_paths_resolve_from_yaml_dir(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "net.json").write_text("{}")

        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir()
        cfg_path = _write_yaml(cfg_dir, {
            "gnpy": {"topology": "../data/net.json"},
        })
        config = load_config(["--config", str(cfg_path)])
        assert config.gnpy.topology == (data_dir / "net.json").resolve()

    def test_transients_config(self, tmp_path: Path) -> None:
        (tmp_path / "net.json").write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {"topology": "net.json"},
            "transients": {
                "edfa_reservoir": {"enabled": False, "tau_ms": 15.0},
                "polarization": {"pmd_coeff_ps_per_sqrt_km": 0.1},
                "phase_noise": {"tx_linewidth_hz": 50000.0},
                "environmental": {"enabled": True, "temp_variation_c": 10.0},
            },
        })
        config = load_config(["--config", str(cfg_path)])
        assert config.transients.edfa_reservoir.enabled is False
        assert config.transients.edfa_reservoir.tau_ms == 15.0
        assert config.transients.polarization.pmd_coeff_ps_per_sqrt_km == 0.1
        assert config.transients.phase_noise.tx_linewidth_hz == 50000.0
        assert config.transients.environmental.enabled is True
        assert config.transients.environmental.temp_variation_c == 10.0


# ===========================================================================
# CLI + YAML merge (precedence tests)
# ===========================================================================

class TestMergePrecedence:
    """CLI args must override YAML values."""

    def test_cli_overrides_yaml_rest_port(self, tmp_path: Path) -> None:
        (tmp_path / "net.json").write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {"topology": "net.json"},
            "server": {"rest_port": 9090},
        })
        config = load_config([
            "--config", str(cfg_path),
            "--rest-port", "7070",
        ])
        assert config.server.rest_port == 7070

    def test_cli_overrides_yaml_topology(self, tmp_path: Path) -> None:
        (tmp_path / "yaml_net.json").write_text("{}")
        cli_topo = tmp_path / "cli_net.json"
        cli_topo.write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {"topology": "yaml_net.json"},
        })
        config = load_config([
            "--config", str(cfg_path),
            "--topology", str(cli_topo),
        ])
        assert config.gnpy.topology == cli_topo.resolve()

    def test_cli_overrides_yaml_clock_mode(self, tmp_path: Path) -> None:
        (tmp_path / "net.json").write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {"topology": "net.json"},
            "simulation": {"clock_mode": "wall"},
        })
        config = load_config([
            "--config", str(cfg_path),
            "--clock-mode", "step",
        ])
        assert config.simulation.clock_mode == ClockMode.STEP

    def test_verbose_overrides_yaml_log_level(self, tmp_path: Path) -> None:
        (tmp_path / "net.json").write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {"topology": "net.json"},
            "logging": {"level": "ERROR"},
        })
        config = load_config([
            "--config", str(cfg_path),
            "-vv",
        ])
        assert config.logging.level == LogLevel.DEBUG


# ===========================================================================
# Validation error cases
# ===========================================================================

class TestValidationErrors:
    """Test that invalid configs are caught."""

    def test_missing_topology_exits(self) -> None:
        with pytest.raises(SystemExit):
            load_config([])

    def test_yaml_typo_caught_by_extra_forbid(self, tmp_path: Path) -> None:
        (tmp_path / "net.json").write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {"topology": "net.json"},
            "sever": {"rest_port": 9090},  # typo: "sever" instead of "server"
        })
        with pytest.raises(SystemExit):
            load_config(["--config", str(cfg_path)])

    def test_invalid_clock_mode_in_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "net.json").write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {"topology": "net.json"},
            "simulation": {"clock_mode": "turbo"},
        })
        with pytest.raises(SystemExit):
            load_config(["--config", str(cfg_path)])

    def test_negative_time_scale(self, tmp_path: Path) -> None:
        (tmp_path / "net.json").write_text("{}")
        cfg_path = _write_yaml(tmp_path, {
            "gnpy": {"topology": "net.json"},
            "simulation": {"time_scale": -1.0},
        })
        with pytest.raises(SystemExit):
            load_config(["--config", str(cfg_path)])

    def test_config_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            load_config(["--config", str(tmp_path / "nonexistent.yaml")])


# ===========================================================================
# Path resolution
# ===========================================================================

class TestPathResolution:
    """Test that relative paths resolve correctly."""

    def test_resolve_paths_function(self, tmp_path: Path) -> None:
        data = {
            "gnpy": {
                "topology": "data/net.json",
                "equipment": "/absolute/eqpt.json",
                "sim_params": None,
                "extra_equipment": ["extra1.json", "/abs/extra2.json"],
            },
            "simulation": {
                "snapshot_dir": "snaps/",
            },
        }
        base_dir = tmp_path / "configs"
        result = _resolve_paths(data, base_dir)

        assert result["gnpy"]["topology"] == str(base_dir / "data/net.json")
        assert result["gnpy"]["equipment"] == "/absolute/eqpt.json"
        assert result["gnpy"]["sim_params"] is None
        assert result["gnpy"]["extra_equipment"][0] == str(base_dir / "extra1.json")
        assert result["gnpy"]["extra_equipment"][1] == "/abs/extra2.json"
        assert result["simulation"]["snapshot_dir"] == str(base_dir / "snaps/")


# ===========================================================================
# Argparse parser structure
# ===========================================================================

class TestParserStructure:
    """Test that the parser is well-formed."""

    def test_help_does_not_error(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
