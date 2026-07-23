"""Argparse CLI definition, YAML loading, and config merging logic.

Supports two invocation modes:
    # GNPy-native (backward compat)
    twinlight --topology net.json -e eqpt.json

    # YAML superset config
    twinlight --config twin_config.yaml

Both can be combined; CLI args always override YAML values.
Precedence: CLI args > YAML file > Pydantic model defaults.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from twinlight.config import TwinConfig

_logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser with groups mirroring YAML sections."""

    parser = argparse.ArgumentParser(
        prog="twinlight",
        description=(
            "TwinLight \u2014 optical network digital twin "
            "with GNPy physical-layer simulation and T-API interfaces."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # -- Top-level: config file -------------------------------------------
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=None,
        metavar="CONFIG.yaml",
        help="Path to YAML superset configuration file. "
             "CLI arguments override values from the config file.",
    )

    # -- GNPy files -------------------------------------------------------
    gnpy_group = parser.add_argument_group(
        "GNPy files",
        "Paths to GNPy JSON topology and equipment files.",
    )
    gnpy_group.add_argument(
        "--topology", type=Path, default=None,
        metavar="TOPOLOGY.(json|xls|xlsx)",
        help="Path to GNPy network topology file.",
    )
    gnpy_group.add_argument(
        "-e", "--equipment", type=Path, default=None,
        metavar="FILE.json",
        help="Path to GNPy equipment library JSON.",
    )
    gnpy_group.add_argument(
        "--sim-params", type=Path, default=None,
        metavar="FILE.json",
        help="Path to GNPy simulation parameters JSON.",
    )
    gnpy_group.add_argument(
        "--no-insert-edfas", action="store_true", default=None,
        help="Disable automatic EDFA insertion.",
    )
    gnpy_group.add_argument(
        "--extra-equipment", nargs="+", type=Path, default=None,
        metavar="FILE.json",
        help="Additional equipment configuration files.",
    )
    gnpy_group.add_argument(
        "--extra-config", nargs="+", type=Path, default=None,
        metavar="FILE.json",
        help="Additional GNPy config files.",
    )

    # -- Server -----------------------------------------------------------
    server_group = parser.add_argument_group("Server")
    server_group.add_argument("--rest-host", type=str, default=None)
    server_group.add_argument("--rest-port", type=int, default=None)
    server_group.add_argument("--grpc-port", type=int, default=None)

    # -- Simulation -------------------------------------------------------
    sim_group = parser.add_argument_group("Simulation")
    sim_group.add_argument(
        "--clock-mode", type=str, default=None,
        choices=["wall", "accelerated", "step"],
    )
    sim_group.add_argument("--time-scale", type=float, default=None)
    sim_group.add_argument("--auto-snapshot-interval", type=int, default=None)
    sim_group.add_argument("--snapshot-dir", type=Path, default=None)

    # -- RMSA -------------------------------------------------------------
    rmsa_group = parser.add_argument_group("RMSA")
    rmsa_group.add_argument("--k-shortest-paths", type=int, default=None)
    rmsa_group.add_argument("--qot-margin-db", type=float, default=None)
    rmsa_group.add_argument("--guardband-slots", type=int, default=None)

    # -- Physics backend --------------------------------------------------
    physics_group = parser.add_argument_group(
        "Physics",
        "QoT propagation engine selection. Both engines are built in; "
        "EGN needs no equipment library.",
    )
    physics_group.add_argument(
        "--physics-backend",
        type=str,
        default=None,
        choices=["gnpy", "egn"],
        help="Propagation engine for QoT baselines (default: gnpy).",
    )

    # -- Spectrum ---------------------------------------------------------
    spec_group = parser.add_argument_group("Spectrum")
    spec_group.add_argument("--num-slots", type=int, default=None)
    spec_group.add_argument("--slot-width-ghz", type=float, default=None)
    spec_group.add_argument("--center-frequency-thz", type=float, default=None)

    # -- Logging ----------------------------------------------------------
    log_group = parser.add_argument_group("Logging")
    log_group.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Increase verbosity (-v = INFO, -vv = DEBUG).",
    )
    log_group.add_argument(
        "--log-level", type=str, default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    log_group.add_argument("--log-format", type=str, default=None)

    # -- State restoration ------------------------------------------------
    parser.add_argument(
        "--restore", type=Path, default=None,
        metavar="SNAPSHOT.json",
        help="Restore state from this snapshot file.",
    )
    parser.add_argument(
        "--restore-latest",
        action="store_true",
        help="Restore state from the most recent snapshot in snapshots/.",
    )

    return parser


def _load_yaml(config_path: Path) -> Dict[str, Any]:
    """Load YAML config as a raw dict."""
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data if data is not None else {}


def _resolve_path(value: Any, base_dir: Path) -> str | None:
    """Resolve a single path relative to base_dir if not absolute."""
    if value is None:
        return None
    p = Path(value)
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return str(p)


def _resolve_path_list(values: List[Any] | None, base_dir: Path) -> List[str]:
    """Resolve a list of paths relative to base_dir."""
    if not values:
        return []
    return [
        resolved
        for v in values
        if (resolved := _resolve_path(v, base_dir)) is not None
    ]


def _resolve_paths(data: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    """Resolve all path-valued fields relative to the config file's directory."""
    gnpy = data.get("gnpy", {})
    for key in ("topology", "equipment", "sim_params"):
        if key in gnpy and gnpy[key] is not None:
            gnpy[key] = _resolve_path(gnpy[key], base_dir)
    for key in ("extra_equipment", "extra_config"):
        if key in gnpy:
            gnpy[key] = _resolve_path_list(gnpy.get(key), base_dir)

    sim = data.get("simulation", {})
    if "snapshot_dir" in sim and sim["snapshot_dir"] is not None:
        sim["snapshot_dir"] = _resolve_path(sim["snapshot_dir"], base_dir)

    return data


def _apply_cli_overrides(
    yaml_data: Dict[str, Any],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    """Layer non-None CLI arguments on top of YAML data."""
    data = yaml_data

    # GNPy
    gnpy = data.setdefault("gnpy", {})
    if args.topology is not None:
        gnpy["topology"] = str(args.topology.resolve())
    if args.equipment is not None:
        gnpy["equipment"] = str(args.equipment.resolve())
    if args.sim_params is not None:
        gnpy["sim_params"] = str(args.sim_params.resolve())
    if args.no_insert_edfas is not None:
        gnpy["no_insert_edfas"] = args.no_insert_edfas
    if args.extra_equipment is not None:
        gnpy["extra_equipment"] = [str(p.resolve()) for p in args.extra_equipment]
    if args.extra_config is not None:
        gnpy["extra_config"] = [str(p.resolve()) for p in args.extra_config]

    # Server
    server = data.setdefault("server", {})
    if args.rest_host is not None:
        server["rest_host"] = args.rest_host
    if args.rest_port is not None:
        server["rest_port"] = args.rest_port
    if args.grpc_port is not None:
        server["grpc_port"] = args.grpc_port

    # Simulation
    sim = data.setdefault("simulation", {})
    if args.clock_mode is not None:
        sim["clock_mode"] = args.clock_mode
    if args.time_scale is not None:
        sim["time_scale"] = args.time_scale
    if args.auto_snapshot_interval is not None:
        sim["auto_snapshot_interval"] = args.auto_snapshot_interval
    if args.snapshot_dir is not None:
        sim["snapshot_dir"] = str(args.snapshot_dir.resolve())

    # RMSA
    rmsa = data.setdefault("rmsa", {})
    if args.k_shortest_paths is not None:
        rmsa["k_shortest_paths"] = args.k_shortest_paths
    if args.qot_margin_db is not None:
        rmsa["qot_margin_db"] = args.qot_margin_db
    if args.guardband_slots is not None:
        rmsa["default_guardband_slots"] = args.guardband_slots

    # Physics backend
    physics = data.setdefault("physics", {})
    if args.physics_backend is not None:
        physics["backend"] = args.physics_backend

    # Spectrum
    spec = data.setdefault("spectrum", {})
    if args.num_slots is not None:
        spec["num_slots"] = args.num_slots
    if args.slot_width_ghz is not None:
        spec["slot_width_ghz"] = args.slot_width_ghz
    if args.center_frequency_thz is not None:
        spec["center_frequency_thz"] = args.center_frequency_thz

    # Logging
    log = data.setdefault("logging", {})
    if args.verbose >= 2:
        log["level"] = "DEBUG"
    elif args.verbose == 1:
        log["level"] = "INFO"
    elif args.log_level is not None:
        log["level"] = args.log_level
    if args.log_format is not None:
        log["format"] = args.log_format

    return data


class ConfigError(SystemExit):
    """Raised when configuration is invalid."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(2)


def load_config(argv: Optional[list[str]] = None) -> TwinConfig:
    """Parse CLI, load YAML, merge, validate. Returns a validated TwinConfig.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Validated TwinConfig instance with an extra ``restore_path`` attribute.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # Step 1: Load YAML if provided
    if args.config is not None:
        config_path = args.config.resolve()
        if not config_path.exists():
            parser.error(f"Config file not found: {config_path}")
        yaml_data = _load_yaml(config_path)
        _resolve_paths(yaml_data, base_dir=config_path.parent)
    else:
        yaml_data = {}

    # Step 2: Apply CLI overrides
    merged = _apply_cli_overrides(yaml_data, args)

    # Step 3: Validate topology is present
    gnpy = merged.get("gnpy", {})
    if not gnpy.get("topology"):
        parser.error(
            "A topology file is required. Provide it via:\n"
            "  --config CONFIG.yaml  (with gnpy.topology set), or\n"
            "  --topology TOPOLOGY.json"
        )

    # Step 4: Attach restore path (explicit path wins over --restore-latest)
    if args.restore is not None:
        merged["restore_path"] = str(args.restore.resolve())
    elif args.restore_latest:
        _snapshot_dir = Path("snapshots")
        candidates = list(_snapshot_dir.glob("*.json")) if _snapshot_dir.is_dir() else []
        if not candidates:
            parser.error(
                "No snapshot files found in snapshots/. "
                "Use --restore PATH to specify a snapshot file, or create one via "
                "POST /admin/snapshot first."
            )
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        merged["restore_path"] = str(latest.resolve())

    # Step 5: Validate with Pydantic
    try:
        config = TwinConfig(**merged)
    except Exception as e:
        parser.error(f"Configuration validation failed:\n{e}")

    return config
