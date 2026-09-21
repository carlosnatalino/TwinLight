# Configuration

TwinLight is configured with a YAML file, optionally overridden by CLI flags.

**Precedence:** CLI arguments > YAML file > model defaults.

**Path resolution:** every relative path inside a YAML file resolves against
*that file's own directory*, not the working directory. A scenario directory can
therefore be moved or mounted anywhere without editing its paths.

**Validation:** the configuration is a Pydantic model tree with
`extra="forbid"` at every level, so a misspelled key is a startup error rather
than a silently ignored setting. The authoritative definition is
[`src/twinlight/config.py`](../src/twinlight/config.py).

Two fully commented examples ship with the project:
[`examples/twin_config.yaml`](../examples/twin_config.yaml) (small) and
[`examples/coronet_conus_config.yaml`](../examples/coronet_conus_config.yaml)
(75-node backbone).

## `gnpy` — topology and equipment (required)

```yaml
gnpy:
  topology: "gnpy-data/CORONET_CONUS_Topology.json"   # required
  equipment: "gnpy-data/eqpt_config.json"
  sim_params: null
  extra_equipment: []
  extra_config: []
  no_insert_edfas: true
```

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `topology` | path | *required* | GNPy network topology (JSON, or xls/xlsx) |
| `equipment` | path | `null` | GNPy equipment library. **Required by the `gnpy` backend** — without it, propagation falls back to mock sinusoidal OPM |
| `sim_params` | path | `null` | GNPy simulation parameters; needed only for Raman |
| `extra_equipment` | list of paths | `[]` | Additional equipment libraries merged into the main one |
| `extra_config` | list of paths | `[]` | Additional GNPy config files |
| `no_insert_edfas` | bool | `false` | Set `true` for topologies with bare fiber spans and no pre-placed amplifiers (CORONET is one) so the builder does not treat missing EDFAs as an error |

The topology and equipment files for the shipped examples are provisioned by
`twinlight-fetch-examples` — see
[examples/gnpy-data/README.md](../examples/gnpy-data/README.md).

## `physics` — propagation backend

```yaml
physics:
  backend: gnpy      # gnpy | egn
```

| Backend | Method | Requires |
|---------|--------|----------|
| `gnpy` (default) | Split-step propagation over the designed network, 88-channel C-band comb | `gnpy.equipment` |
| `egn` | Closed-form GN/EGN kernel (`physics/egn_kernel.py`) | nothing beyond the topology |

This is a **startup choice**: all baselines in a process come from one backend,
and a snapshot taken under one backend is refused by the other, because the GSNR
values would not be comparable.

## `server` — listening sockets

```yaml
server:
  rest_host: "0.0.0.0"
  rest_port: 8080
  grpc_port: 50051
  cors_origins:
    - "http://localhost:5173"
```

`cors_origins` must contain the origin the web UI is served from. In the Compose
stack the UI is published on `http://localhost:5173`, which the shipped examples
already allow.

## `simulation` — clock and checkpoints

```yaml
simulation:
  clock_mode: "wall"
  time_scale: 1.0
  auto_snapshot_interval: 60
  snapshot_dir: "snapshots/"
```

| Field | Default | Meaning |
|-------|---------|---------|
| `clock_mode` | `wall` | `wall` \| `accelerated` \| `step`. Transient models are functions of time; this selects which clock feeds them |
| `time_scale` | `1.0` | Multiplier for `accelerated` mode |
| `auto_snapshot_interval` | `60` | Seconds between automatic snapshots; `0` disables |
| `snapshot_dir` | `snapshots/` | Where `/admin/snapshot` writes, and where the checkpoint lives |
| `checkpoint_file` | `checkpoint.json` | Name of the shutdown checkpoint inside `snapshot_dir` |
| `auto_checkpoint` | `true` | Write the checkpoint on graceful shutdown |

## `transients` — time-varying impairment models

Each of the four models can be enabled independently, which is the usual way to
isolate one effect in an experiment. The physics is described in
[PHYSICS.md](PHYSICS.md); this section is the parameter list.

### `transients.edfa_reservoir`

```yaml
edfa_reservoir:
  enabled: true
  tau_ms: 10.0
  gain_per_channel_db: 0.3
  tau_add_factor: 0.001
  tau_drop_factor: 0.01
  drift_period_multiplier: 100.0
  gain_drift_amp_db: 0.3
  solver_method: "RK45"
  solver_max_step_ms: 0.1
```

| Field | Default | Meaning and source |
|-------|---------|--------------------|
| `tau_ms` | `10.0` | Erbium metastable lifetime [ms] — Sun, Zyskind & Srivastava 1997 |
| `gain_per_channel_db` | `0.3` | Peak gain excursion [dB] per channel of load step, at the moment of an add/drop; the cascade accumulates linearly in dB (Sun 1997). It decays to zero with τ_e — it is **not** a standing per-channel penalty, see [PHYSICS.md § 2.1](PHYSICS.md) |
| `tau_add_factor` | `0.001` | τ_e/τ for channel-add events — Bononi & Rusch 1998 Eq. 29 (≈1–10 µs) |
| `tau_drop_factor` | `0.01` | τ_e/τ for channel-drop events — ibid. (≈100–500 µs) |
| `drift_period_multiplier` | `100.0` | Sinusoidal-fallback drift period multiplier |
| `gain_drift_amp_db` | `0.3` | Sinusoidal-fallback peak gain excursion [dB] |
| `solver_method` | `RK45` | `scipy.integrate.solve_ivp` method |
| `solver_max_step_ms` | `0.1` | Maximum solver step [ms] |

The last four apply only to the **stateless fallback**, used when no
`EdfaStateTracker` is available. In normal operation the stateful exponential
step response is used instead.

### `transients.polarization`

```yaml
polarization:
  enabled: true
  pmd_coeff_ps_per_sqrt_km: 0.04
  pmd_drift_period_s: 90.0
  pmd_drift_amplitude: 0.1
  pdl_per_roadm_db: 0.5
  pdl_per_edfa_db: 0.1
  sop_drift_rate_rad_per_s: 1000.0
  sop_drift_period_s: 300.0
  ase_distribution: distributed
```

| Field | Default | Meaning and source |
|-------|---------|--------------------|
| `pmd_coeff_ps_per_sqrt_km` | `0.04` | PMD coefficient [ps/√km] — ITU-T G.652 SSMF; Gordon & Kogelnik 2000 |
| `pmd_drift_period_s` | `90.0` | PMD drift cycle [s] |
| `pmd_drift_amplitude` | `0.1` | Per-fiber PMD drift as a fraction of baseline (0.1 = ±10 %) |
| `pdl_per_roadm_db` | `0.5` | Mean of the per-ROADM/WSS Maxwell-distributed PDL [dB] — D'Amico OFC 2023 measured 0.2–0.8 dB per WSS |
| `pdl_per_edfa_db` | `0.1` | Mean of the per-EDFA Maxwell PDL [dB] |
| `sop_drift_rate_rad_per_s` | `1000.0` | SOP drift rate [rad/s] — ≈1 krad/s for buried fiber, Czegledi *et al.* 2016 |
| `sop_drift_period_s` | `300.0` | Base period over which each hinge's alignment `cosθ` sweeps [−1, 1]. Hinges are detuned ±25 % by UID so their periods are incommensurate and the joint alignment space is covered ergodically |
| `ase_distribution` | `distributed` | Where ASE enters relative to the PDL hinges — `distributed` (equal ASE at each EDFA, Miotto OFC 2025), `rx` (all at the receiver, worst case), `tx` (all at the transmitter, OSNR conserved) |

`ase_distribution` is the parameter most worth understanding before quoting PDL
results: it changes the sign and magnitude of the OSNR penalty, and the three
options bracket the realistic range.

### `transients.phase_noise`

```yaml
phase_noise:
  enabled: true
  tx_linewidth_hz: 100000.0
  lo_linewidth_hz: 100000.0
  linewidth_variation_period_s: 120.0
  linewidth_variation_amplitude: 0.1
```

| Field | Default | Meaning and source |
|-------|---------|--------------------|
| `tx_linewidth_hz` | `100000.0` | Transmitter laser linewidth [Hz] — typical coherent 100 kHz; Henry 1982 |
| `lo_linewidth_hz` | `100000.0` | Local-oscillator linewidth [Hz]. **Only this one drives the EEPN penalty** (Shieh & Ho 2008) |
| `linewidth_variation_period_s` | `120.0` | Period of slow linewidth variation (aging/thermal) [s] |
| `linewidth_variation_amplitude` | `0.1` | Variation as ±fraction |

### `transients.environmental`

```yaml
environmental:
  enabled: false
  temp_variation_c: 5.0
  temp_cycle_period_s: 86400.0
  timezone_offset: 0.0
  cd_temp_coeff_ps_nm_km_c: 0.002
  alpha_temp_coeff_db_km_c: 0.0002
```

| Field | Default | Meaning and source |
|-------|---------|--------------------|
| `enabled` | `false` | **Off by default** — the diurnal timescale is far longer than a typical experiment, so it is opt-in |
| `temp_variation_c` | `5.0` | Ambient temperature swing [°C] |
| `temp_cycle_period_s` | `86400.0` | Diurnal period [s] |
| `timezone_offset` | `0.0` | Hours to shift the cycle relative to wall-clock time |
| `cd_temp_coeff_ps_nm_km_c` | `0.002` | dD/dT [ps/(nm·km·°C)] — G.652 SSMF, Kato *et al.* 2000 |
| `alpha_temp_coeff_db_km_c` | `0.0002` | dα/dT [dB/(km·°C)] on deployed G.652 fiber |

## `rmsa` — routing, modulation and spectrum assignment

```yaml
rmsa:
  k_shortest_paths: 5
  qot_margin_db: 1.5
  default_guardband_slots: 1
```

| Field | Default | Meaning |
|-------|---------|---------|
| `k_shortest_paths` | `3` | Candidate paths evaluated per request. Raise it for large meshes (the CORONET example uses 5) |
| `qot_margin_db` | `1.5` | System margin added to the format's required GSNR before admission |
| `default_guardband_slots` | `1` | Guard band in 6.25 GHz slots on each side of an allocation |

`qot_margin_db` is the main admission-strictness knob, and is also exposed at
run time through `POST /config/set` so a single process can sweep it.

## `spectrum` — the flexi-grid

```yaml
spectrum:
  num_slots: 768
  slot_width_ghz: 6.25
  center_frequency_thz: 193.1
```

Defaults describe the standard C-band: 768 × 6.25 GHz = 4.8 THz centred on the
ITU-T G.694.1 anchor at 193.1 THz.

## `logging`

```yaml
logging:
  level: "INFO"      # DEBUG | INFO | WARNING | ERROR | CRITICAL
  format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
```

## CLI reference

`twinlight --help` prints the full list. Every flag below overrides the
corresponding YAML field.

| Flag | Overrides |
|------|-----------|
| `-c`, `--config CONFIG.yaml` | — (selects the YAML file) |
| `--topology`, `-e/--equipment`, `--sim-params` | `gnpy.*` |
| `--no-insert-edfas` | `gnpy.no_insert_edfas` |
| `--extra-equipment`, `--extra-config` | `gnpy.extra_*` |
| `--rest-host`, `--rest-port`, `--grpc-port` | `server.*` |
| `--clock-mode`, `--time-scale`, `--auto-snapshot-interval`, `--snapshot-dir` | `simulation.*` |
| `--k-shortest-paths`, `--qot-margin-db`, `--guardband-slots` | `rmsa.*` |
| `--physics-backend {gnpy,egn}` | `physics.backend` |
| `--num-slots`, `--slot-width-ghz`, `--center-frequency-thz` | `spectrum.*` |
| `-v` / `-vv`, `--log-level`, `--log-format` | `logging.*` |
| `--restore` | restore the shutdown checkpoint |
| `--restore SNAPSHOT.json` | restore this snapshot file |
| `--reset` | ignore the checkpoint and start from scratch |
| `--restore-latest` | restore the newest timestamped snapshot in `simulation.snapshot_dir` |

### Startup state

The twin writes a **checkpoint** when it shuts down gracefully (SIGINT or
SIGTERM, so `docker compose down` and Ctrl+C both count) and picks it up again
on the next start. Nothing has to be passed for that to happen:

| Invocation | Starts from |
|---|---|
| *(no flag)* | the checkpoint if one exists, otherwise a clean twin |
| `--restore` | the checkpoint; **errors** if there is none |
| `--restore PATH` | that snapshot file; errors if it does not exist |
| `--reset` | a clean twin; the old checkpoint is kept as `checkpoint.json.bak` |
| `--restore-latest` | the newest timestamped snapshot, ignoring the checkpoint |

These four are mutually exclusive.

Absence of a checkpoint is an error for a bare `--restore` but not for the
no-flag case, and the asymmetry is deliberate: asking to restore and silently
getting an empty twin would falsify whatever experiment follows, whereas a first
run with nothing to resume is the ordinary case.

The checkpoint is a single file, `simulation.checkpoint_file` inside
`simulation.snapshot_dir` — distinct from the timestamped snapshots
`POST /admin/snapshot` writes into the same directory, which are never loaded
automatically. `--reset` keeps exactly one generation of backup, so a second
reset overwrites the first one's `.bak`.

Set `simulation.auto_checkpoint: false` to stop the twin checkpointing itself at
shutdown. A checkpoint that cannot be written is logged and the shutdown still
completes cleanly, so a read-only or full `snapshot_dir` will not make the
process exit non-zero.

> **Where the checkpoint actually lands.** `snapshot_dir` is a path field, so a
> value set **in a YAML file** is resolved relative to *that file's* directory,
> while `--snapshot-dir` on the command line resolves against the working
> directory. `snapshot_dir: "snapshots/"` inside `examples/twin_config.yaml`
> therefore means `examples/snapshots/`, not `./snapshots/`. This is why the
> Dockerfile and Compose stack pass `--snapshot-dir /app/snapshots` explicitly:
> without it the checkpoint would be written inside the container layer rather
> than the mounted volume, and lost on every rebuild. Note that
> `POST /admin/snapshot` does not use this setting at all — it always writes to
> `snapshots/` relative to the working directory.

## Runtime overrides

Some parameters can be changed while the twin is running, through
`POST /config/set` (see [API.md](API.md#config--runtime-parameter-overrides)):
per-fiber loss coefficient, per-EDFA noise figure and gain target, fiber failure
state, the RMSA margin, and the per-model transient enable flags. Changing one
invalidates the affected cached baselines, so the next OPM read reflects it.

Everything else — topology, backend, spectrum grid — is fixed at startup by
design: changing it mid-run would make measurements taken before and after the
change incomparable.
