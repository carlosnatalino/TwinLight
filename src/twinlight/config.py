"""Pydantic configuration models for TwinLight, the optical network digital twin.

Hierarchy:
    TwinConfig (root)
    ├── gnpy: GnpyConfig
    ├── server: ServerConfig
    ├── simulation: SimulationConfig
    ├── transients: TransientsConfig
    │   ├── edfa_reservoir: EdfaReservoirConfig
    │   ├── polarization: PolarizationConfig
    │   ├── phase_noise: PhaseNoiseConfig
    │   └── environmental: EnvironmentalConfig
    ├── rmsa: RmsaConfig
    ├── spectrum: SpectrumConfig
    └── logging: LoggingConfig
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClockMode(str, Enum):
    WALL = "wall"
    ACCELERATED = "accelerated"
    STEP = "step"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# -- GNPy section ----------------------------------------------------------

class GnpyConfig(BaseModel):
    """References to external GNPy JSON files."""

    model_config = ConfigDict(extra="forbid")

    topology: Path
    equipment: Path | None = None
    sim_params: Path | None = None
    extra_equipment: list[Path] = Field(default_factory=list)
    extra_config: list[Path] = Field(default_factory=list)
    no_insert_edfas: bool = False


# -- Server section ---------------------------------------------------------

class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rest_host: str = "0.0.0.0"
    rest_port: int = 8080
    grpc_port: int = 50051
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    restconf_root: str = Field(
        default="/restconf",
        description=(
            "RESTCONF root resource (RFC 8040 §3.1). The T-API modules are "
            "served under {restconf_root}/data/... and advertised at "
            "/.well-known/host-meta. They stay reachable at the bare "
            "/data/... as well, for clients written against earlier "
            "TwinLight releases. Empty string serves only the bare paths."
        ),
    )

    @field_validator("restconf_root")
    @classmethod
    def _check_restconf_root(cls, value: str) -> str:
        """A root is "" or an absolute path with no trailing slash.

        RFC 8040 §3.1 lets a server pick any root, so this is validated
        rather than fixed — but a malformed one would silently produce
        unroutable paths like ``restconf/data`` or ``/restconf//data``.
        """
        if value == "":
            return value
        if not value.startswith("/"):
            raise ValueError(
                f"restconf_root must start with '/' (got {value!r})"
            )
        return value.rstrip("/")


# -- Simulation section -----------------------------------------------------

class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clock_mode: ClockMode = ClockMode.WALL
    time_scale: float = Field(default=1.0, gt=0)
    auto_snapshot_interval: int = Field(default=60, ge=0)
    snapshot_dir: Path = Path("snapshots/")
    # The checkpoint is written on graceful shutdown and picked up again on the
    # next start, so a twin survives a restart without anyone asking it to.
    # It is a single well-known file, distinct from the timestamped snapshots
    # that POST /admin/snapshot writes into the same directory.
    checkpoint_file: str = Field(default="checkpoint.json", min_length=1)
    auto_checkpoint: bool = True


# -- Transient model sections -----------------------------------------------
# Defaults and citations: literature/deep research/
#   "Analytical Models for Time-Varying Optical Impairments in WDM Systems - A Digital Twin Foundation.md"

class EdfaReservoirConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tau_ms: float = Field(
        default=10.0, gt=0,
        description=(
            "Erbium metastable lifetime [ms]. "
            "Sun-Saleh-Zyskind 1997, Electron. Lett. vol. 33."
        ),
    )
    gain_per_channel_db: float = Field(
        default=0.3, ge=0,
        description=(
            "Per-channel GSNR contribution [dB] for Bononi "
            "reservoir model. Cascade accumulates linearly "
            "(Sun 1997)."
        ),
    )
    tau_add_factor: float = Field(
        default=0.001, gt=0,
        description=(
            "tau_e/tau ratio for channel-add events. "
            "Bononi-Rusch JLT 1998 Eq. 29: ~1-10 us."
        ),
    )
    tau_drop_factor: float = Field(
        default=0.01, gt=0,
        description=(
            "tau_e/tau ratio for channel-drop events. "
            "Bononi-Rusch JLT 1998 Eq. 29: ~100-500 us."
        ),
    )
    # Legacy sinusoidal fallback (used when no event tracker)
    drift_period_multiplier: float = Field(
        default=100.0, gt=0,
        description="Sinusoidal fallback: drift period multiplier.",
    )
    gain_drift_amp_db: float = Field(
        default=0.3, ge=0,
        description="Sinusoidal fallback: peak gain excursion [dB].",
    )
    solver_method: str = "RK45"
    solver_max_step_ms: float = Field(default=0.1, gt=0)


class PolarizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    pmd_coeff_ps_per_sqrt_km: float = Field(
        default=0.04, ge=0,
        description="PMD coefficient [ps/√km]. Default: ITU-T G.652 SSMF (Gordon-Kogelnik, PNAS 2000).",
    )
    # --- PDL: hinge model of Zarkosvky & Shtaif, Opt. Lett. 45(5):1224 (2020),
    #     Eqs. 3-5; OSNR-penalty usage per D'Amico OFC 2023 / Miotto OFC 2025.
    pdl_per_roadm_db: float = Field(
        default=0.5, ge=0,
        description="Mean of the per-ROADM/WSS Maxwell-distributed PDL [dB]; each hinge draws an individual value seeded by its UID (Miotto OFC 2025). Default: D'Amico OFC 2023 measured 0.2-0.8 dB per WSS.",
    )
    pdl_per_edfa_db: float = Field(
        default=0.1, ge=0,
        description="Mean of the per-EDFA Maxwell-distributed PDL [dB]; each hinge draws an individual value seeded by its UID. Default: small residual PDL of an inline amplifier.",
    )
    sop_drift_rate_rad_per_s: float = Field(
        default=1000.0, ge=0,
        description="SOP drift rate [rad/s]. Default: ~1 krad/s buried fiber (Czegledi et al., Scientific Reports 2016).",
    )
    pmd_drift_period_s: float = Field(
        default=90.0, gt=0,
        description="SOP drift cycle period [s] for PMD random-walk time variation. Default: field-timescale approximation.",
    )
    pmd_drift_amplitude: float = Field(
        default=0.1, ge=0, le=1.0,
        description="Per-fiber PMD drift amplitude as fraction of baseline (e.g. 0.1 = 10%). Default: Analytical Models §5.",
    )
    sop_drift_period_s: float = Field(
        default=300.0, gt=0,
        description="Base period [s] over which each hinge's SOP alignment cosθ sweeps [-1,1] as fiber birefringence drifts. Each hinge is detuned by a small UID-dependent factor (±25%) so periods are incommensurate and the joint alignment space is ergodically covered. Default: minutes-scale.",
    )
    ase_distribution: Literal["distributed", "rx", "tx"] = Field(
        default="distributed",
        description="ASE injection assumption for the PDL/OSNR interplay (D'Amico OFC 2023): 'distributed' = equal ASE at each path EDFA (Miotto OFC 2025); 'rx' = all ASE at receiver (worst case); 'tx' = all ASE at transmitter (OSNR conserved).",
    )


class PhaseNoiseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tx_linewidth_hz: float = Field(
        default=100_000.0, ge=0,
        description="Transmitter laser linewidth [Hz]. Default: typical 100 kHz for coherent (Henry, IEEE JQE 1982; Analytical Models §4).",
    )
    lo_linewidth_hz: float = Field(
        default=100_000.0, ge=0,
        description="Local oscillator linewidth [Hz]. Default: same as Tx for balanced EEPN (Shieh-Ho, Opt. Express 2008).",
    )
    linewidth_variation_period_s: float = Field(
        default=120.0, gt=0,
        description="Period [s] of linewidth time variation (laser aging/thermal). Default: minutes-scale drift.",
    )
    linewidth_variation_amplitude: float = Field(
        default=0.1, ge=0, le=1.0,
        description="Linewidth variation ±fraction (e.g. 0.1 = ±10%). Default: Analytical Models §4.",
    )


class EnvironmentalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    temp_variation_c: float = Field(
        default=5.0, ge=0,
        description="Ambient temperature variation [°C] over diurnal cycle. Default: typical daily swing.",
    )
    temp_cycle_period_s: float = Field(
        default=86400.0, gt=0,
        description="Diurnal cycle period [s]. Default: 24 h.",
    )
    timezone_offset: float = Field(
        default=0.0,
        description="Hours to shift diurnal cycle relative to wall clock",
    )
    cd_temp_coeff_ps_nm_km_c: float = Field(
        default=0.002, ge=0,
        description="dD/dT [ps/(nm·km·°C)]. Default: G.652 SSMF, Kato et al. Opt. Lett. 2000 (range -0.0015 to -0.0038).",
    )
    alpha_temp_coeff_db_km_c: float = Field(
        default=2e-4, ge=0,
        description="dα/dT [dB/(km·°C)]. Default: measured on deployed G.652 fibers (Analytical Models §6).",
    )


class TransientsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edfa_reservoir: EdfaReservoirConfig = Field(default_factory=EdfaReservoirConfig)
    polarization: PolarizationConfig = Field(default_factory=PolarizationConfig)
    phase_noise: PhaseNoiseConfig = Field(default_factory=PhaseNoiseConfig)
    environmental: EnvironmentalConfig = Field(default_factory=EnvironmentalConfig)


# -- RMSA section -----------------------------------------------------------

class RmsaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    k_shortest_paths: int = Field(default=3, ge=1)
    qot_margin_db: float = Field(default=1.5, ge=0)
    default_guardband_slots: int = Field(default=1, ge=0)


# -- Spectrum section -------------------------------------------------------

class SpectrumConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    num_slots: int = Field(default=768, ge=1)
    slot_width_ghz: float = Field(default=6.25, gt=0)
    center_frequency_thz: float = Field(default=193.1, gt=0)


# -- Logging section --------------------------------------------------------

class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: LogLevel = LogLevel.INFO
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


# -- Physics backend section ------------------------------------------------

class PhysicsConfig(BaseModel):
    """Selects which propagation engine computes QoT baselines.

    ``gnpy`` (default): split-step propagation via the gnpy 2.x library;
    needs ``gnpy.equipment`` to be configured.
    ``egn``: the closed-form GN/EGN kernel in ``physics/egn_kernel.py``.
    Self-contained — no extra install, and no equipment file required.

    Selection is a startup choice: once the process is running, all
    baselines come from the same backend, and a snapshot taken under one
    backend cannot be restored under the other.
    """

    model_config = ConfigDict(extra="forbid")

    backend: Literal["gnpy", "egn"] = "gnpy"


# -- Root config ------------------------------------------------------------

class TwinConfig(BaseModel):
    """Root validated configuration for TwinLight, the optical network digital twin."""

    model_config = ConfigDict(extra="forbid")

    gnpy: GnpyConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    transients: TransientsConfig = Field(default_factory=TransientsConfig)
    rmsa: RmsaConfig = Field(default_factory=RmsaConfig)
    spectrum: SpectrumConfig = Field(default_factory=SpectrumConfig)
    physics: PhysicsConfig = Field(default_factory=PhysicsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    restore_path: Path | None = Field(default=None, exclude=True)
    # Set by --reset. Startup state is decided entirely in cli.py, but the file
    # operation the flag implies (moving an existing checkpoint aside) belongs
    # with the other startup side effects in main.py, not in a config loader.
    reset: bool = Field(default=False, exclude=True)

    def checkpoint_path(self) -> Path:
        """Absolute-ish path of the shutdown checkpoint."""
        return self.simulation.snapshot_dir / self.simulation.checkpoint_file
