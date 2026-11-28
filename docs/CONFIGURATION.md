# Configuration

TwinLight is configured with a YAML file, optionally overridden by CLI flags.

**Precedence:** CLI arguments > YAML file > model defaults. The two settings
read from the environment (or `./.env`) instead, `TWINLIGHT_API_TOKENS` and
`TWINLIGHT_CORS_ORIGINS`, replace what the YAML says: environment > dotenv
file > YAML file > model defaults.

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
| `no_insert_edfas` | bool | `false` | **Currently inert.** Intended for topologies with bare fiber spans and no pre-placed amplifiers, but nothing reads it: GNPy's `designed_network()` inserts amplifiers regardless (1068 of them on CORONET, which sets this `true`). See [ROADMAP.md](ROADMAP.md) |

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
| `gnpy` (default) | Split-step propagation over the designed network; the service channel on the equipment library's SI comb | `gnpy.equipment` |
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
  restconf_root: "/restconf"
  cors_origins:
    - "http://localhost:5173"
```

`cors_origins` must contain the origin the web UI is served from, written as
the browser sends it in `Origin` (RFC 6454 §6.2): `scheme://host[:port]`,
lowercase, no path or trailing slash, the port left out when it is the
scheme's default. An entry in any other form would never match, so the twin
refuses to start with it and says why; `*` allows every origin.

`TWINLIGHT_CORS_ORIGINS` replaces the list without editing the file: a
comma-separated list, each entry trimmed, empty entries skipped, validated
the same way. It is read like the API tokens, from the environment and then
from `./.env` (or `--env-file`), and an empty value counts as unset. It
replaces rather than extends the file's list, as every override does (a CLI
flag replaces its YAML value too): the allowed origins are then exactly the
ones written in one place, and one can be taken away without touching the
scenario file. There is no command-line flag for it.

```bash
TWINLIGHT_CORS_ORIGINS="http://localhost:5174, http://ui.example:8443" \
    twinlight --config examples/twin_config.yaml
```

The Compose stack sets it to the web UI's origin,
`http://localhost:${TWINLIGHT_UI_PORT:-5173}`, so publishing the UI on
another port (`TWINLIGHT_UI_PORT`) keeps it allowed; set
`TWINLIGHT_CORS_ORIGINS` in `.env` to allow other origins instead.

`restconf_root` is the RFC 8040 §3.1 root resource the T-API modules are served
under — at `{restconf_root}/data/…` and nowhere else — and the value advertised
by `GET /.well-known/host-meta`. It must be absolute; a trailing slash is
trimmed. Changing it moves T-API: the bundled clients (web UI, client library,
CLIs, examples, ONOS adapter) read it from host-meta, but a client that
hard-codes `/restconf` will not find the new location. Set it to `""` to put the
data resource at `/data/…` and the API resource at `/`; host-meta then names
the root as the absolute URI the request reached (`http://host:port`, an
empty path), which the bundled clients read as the empty root (see
[API.md](API.md#finding-the-restconf-root)). That mode is as discoverable as
any other root (RFC 8040 §3.1, §3.3); see
[TAPI_COMPLIANCE.md § 2.7](TAPI_COMPLIANCE.md#27-rfc-8040-and-rfc-7951-conformance-matrix).

## Authentication — API tokens

Authentication is the one setting that has no YAML section, on purpose. Scenario
files get committed next to their topologies, so a token in one would end up in
git history. The token set comes from **`TWINLIGHT_API_TOKENS`** instead, read once
at startup:

1. from the process environment, else
2. from a dotenv file: `./.env` in the working directory, or the file named by
   `--env-file`.

An environment variable wins over the same name in the file. An *empty* variable
counts as unset, so it falls through to the file: a stray `export
TWINLIGHT_API_TOKENS=`, or Compose's `${TWINLIGHT_API_TOKENS:-}`, cannot
silently turn authentication off. A YAML file with an `api_tokens` key is
rejected at startup.

**Tokens configured** means REST and gNMI both require one on every call. **None
configured** means both are open and the startup log warns; the HTTP middleware
and the gRPC interceptor are then not installed at all, so no authentication
code runs per request.

```dotenv
# Comma- or whitespace-separated; each entry is `token` or `name:token`.
TWINLIGHT_API_TOKENS=ui:Zm9vYmFyLXVpLXRva2VuLTAx,onos:Zm9vYmFyLW9ub3MtdG9rZW4
```

| Rule | Why |
|------|-----|
| At least 16 characters | Rejects placeholders such as `changeme`. Generate tokens with `python -c 'import secrets; print(secrets.token_urlsafe(32))'` |
| Letters, digits and `-._~+/`, optional trailing `=` | RFC 6750 §2.1 `b64token`: what may legally follow `Bearer ` in a header |
| Names: 1–64 of letters, digits, `.`, `_`, `-` | A name is what the logs and `GET /internal/whoami` report. An unnamed token is reported as `token-<8 hex>`, a SHA-256 fingerprint that is safe to log |
| No duplicate tokens or names | Each token names one client, so one client can be revoked without touching the others |

A malformed value stops startup with every problem listed by position or name.
The message never contains a token.

Nothing else is configurable. The one public path, `GET /health`, is fixed (the
Compose healthcheck calls it without credentials); everything else, including
`/.well-known/host-meta`, `/metrics` and the OpenAPI docs, needs a token. Every
accepted token grants full access: there are no per-token scopes. See
[API.md § Authentication](API.md#authentication) for the wire behaviour.

The clients read **`TWINLIGHT_API_TOKEN`** (singular): the one token they present,
from the environment or `./.env`, overridden by `--token`. A single `.env` can
hold both variables when the twin and its clients run in one directory, as in
[`.env.example`](../.env.example). The ONOS integration's adapter reads the same
variable and presents the same token on ONOS's behalf, which makes the token's
rights available to anyone who can reach the adapter. See
[integrations/onos/README.md § Authentication](../integrations/onos/README.md#authentication).

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
  cd_temp_coeff_ps_nm_km_c: -0.0015
  alpha_temp_coeff_db_km_c: 0.0002
```

| Field | Default | Meaning and source |
|-------|---------|--------------------|
| `enabled` | `false` | **Off by default** — the diurnal timescale is far longer than a typical experiment, so it is opt-in |
| `temp_variation_c` | `5.0` | Ambient temperature swing [°C] |
| `temp_cycle_period_s` | `86400.0` | Diurnal period [s] |
| `timezone_offset` | `0.0` | Hours to shift the cycle relative to wall-clock time |
| `cd_temp_coeff_ps_nm_km_c` | `-0.0015` | dD/dT [ps/(nm·km·°C)]. Negative: dispersion falls as the fiber warms. Kato *et al.* 2000 measured −0.0005 to −0.0038 and give no G.652 value, so the default is a value inside their range (see [PHYSICS.md § Known limitations](PHYSICS.md#known-limitations)) |
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
| `default_guardband_slots` | `1` | Guard slots added to each media channel (8 + 1 = 9 slots for a 50 GHz channel). The carrier is centred in the media channel, so the guard is shared between its two edges |

At startup the twin logs the settings in effect — margin, `k_shortest_paths`,
guard slots and the default launch power with the equipment file it came
from — as one `RMSA settings: …` line at INFO, so a run's admission decisions
can be reproduced from its log.

There is no launch-power key here. The default launch power is a property of
the line and comes from the equipment library's `SI` entry (`power_dbm`, the
per-channel power the amplifiers are designed for; GNPy's bundled library when
`gnpy.equipment` is unset). A service can ask for its own through the T-API
`otsi-config/channel-output-power` leaf, in 0.01 dBm — see
[PHYSICS.md § Launch power](PHYSICS.md#launch-power).

`qot_margin_db` is the main admission-strictness knob, and is also exposed at
run time — through `POST /config/set` or `/transparent/data/Config/rmsa` — so a
single process can sweep it. All three fields are runtime-mutable; they affect
future admissions only, never a service already admitted. The margin is not
part of a lightpath's `operational-state`, which goes `DISABLED` only below
the format's required GSNR itself.

## `fec` — post-FEC BER models

Two independent models turn the pre-FEC BER into a post-FEC BER, reported as
the separate KPIs `post-fec-ber-threshold` and `post-fec-ber-hd`. Neither
affects admission. The physics is in
[PHYSICS.md § 3](PHYSICS.md#3-from-gsnr-to-ber-q-post-fec-ber-and-gmi).

```yaml
fec:
  threshold:
    pre_fec_ber_threshold: 3.8e-3
    post_fec_ber_floor: 1.0e-15
  hard_decision:          # RS(255,239), ITU-T G.709 GFEC
    n: 255
    k: 239
    t: 8
    symbol_bits: 8
```

| Field | Default | Meaning |
|-------|---------|---------|
| `threshold.pre_fec_ber_threshold` | `3.8e-3` | Highest pre-FEC BER the code corrects. The default is the 7%-overhead HD-FEC limit that the modulation table's required GSNRs refer to. Other values in use: `1.22e-2` for 400ZR CFEC (OIF 400ZR IA; vendor datasheets often quote `1.25e-2`) and `2.0e-2` for oFEC (OpenROADM MSA) |
| `threshold.post_fec_ber_floor` | `1e-15` | Post-FEC BER reported at or below the threshold — the output BER the code is specified at |
| `hard_decision.n`, `.k` | `255`, `239` | Code length and information length, in symbols |
| `hard_decision.t` | `8` | Symbol errors corrected per codeword; at most (n − k)/2 |
| `hard_decision.symbol_bits` | `8` | Bits per symbol: m for a Reed–Solomon code over GF(2^m), `1` for a binary code such as BCH. For m > 1, n ≤ 2^m − 1 |

Invalid codes (k ≥ n, t above (n − k)/2, an RS code longer than its field) are
rejected at startup, and the same rules are checked when the section is changed
at run time (`POST /config/set` or `/transparent`) — on the section as it would
be after the whole change, so `k` and `t` can move together in one request.

## `spectrum` — the flexi-grid

```yaml
spectrum:
  num_slots: 768
  slot_width_ghz: 6.25
  center_frequency_thz: 193.1
```

Defaults describe the standard C-band: 768 × 6.25 GHz = 4.8 THz centred on the
ITU-T G.694.1 anchor at 193.1 THz.

The grid is the spectrum the twin *accounts* for; what it *assigns* on a route
is further limited to the band every amplifier on the route passes, which comes
from the equipment library (each EDFA type's `f_min`/`f_max`, GNPy's
`find_elements_common_range`). With GNPy's reference EDFAs that band starts at
191.275 THz, so the lowest ~90 slots of the default grid are never assigned.

## `streaming` — the T-API measurement stream

```yaml
streaming:
  sample_interval_s: 1.0
  record_retention_min: 5
  advertised_address: ""
```

The twin publishes one T-API v2.6.0 performance-measurement stream over gNMI
(TR-548 §6) on `server.grpc_port`; [API.md](API.md#gnmi-streaming) describes
discovery, subscription and the record format.

| Field | Default | Meaning |
|-------|---------|---------|
| `sample_interval_s` | `1.0` | Seconds between ticks. Each tick takes one OPM sample of every connectivity service — the same computation `GET /internal/opm` serves — and appends one record per OAM job to the log, whether or not a client is subscribed. Also reported as each record's `sample-interval`. |
| `record_retention_min` | `5` | Minutes a record stays in the truncated log, published as the stream type's `record-retention`. A client that reconnects within this window and asks to resume from its last record's timestamp loses nothing (TR-548 ST-6.4). |
| `advertised_address` | `""` | The `connection-address` published on the available-stream, as `grpc://<host>:<port>`. Empty derives it, once at startup, from the host's FQDN and `server.grpc_port`; the startup log shows the result. A client cannot reach it when that name does not resolve where the client runs (often on a laptop), from the host of a Compose stack (`grpc://twin:50051`), or through NAT or a port mapping. Set it to an address clients can reach, or have each client pass `--gnmi-target`; otherwise `twinlight-client` stops at the first connection with an error naming the address ([API.md](API.md#when-the-advertised-address-cannot-be-reached)). |

All three are restart-only, and the effective values are logged at startup. The
stream has no token of its own: gNMI accepts the twin-wide API tokens
([Authentication](#authentication--api-tokens)).

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
| `--env-file FILE` | — (dotenv file to read `TWINLIGHT_API_TOKENS` and `TWINLIGHT_CORS_ORIGINS` from, instead of `./.env`; see [Authentication](#authentication--api-tokens) and [`server`](#server--listening-sockets)) |
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
`POST /config/set` (see [API.md](API.md#config--runtime-parameter-overrides)),
`POST /config/parameters`, or the transparent controller
([TRANSPARENT_CONTROLLER.md](TRANSPARENT_CONTROLLER.md)):

- per-element physical parameters — fiber loss, padding, connector losses, PMD
  coefficient and dispersion; amplifier gain, tilt, VOAs, NF offset, saturation
  power and PMD; ROADM power target (the list depends on the backend; see
  `allowed_attributes` in `GET /config/get`);
- fiber failure state;
- every field of `transients`, `rmsa` and `fec` — the sections read live on
  each OPM sample or admission — except four that no model reads
  (`transients.edfa_reservoir.solver_method`, `….solver_max_step_ms`,
  `transients.polarization.pmd_coeff_ps_per_sqrt_km`,
  `….sop_drift_rate_rad_per_s`), which stay read-only so a write cannot appear
  to succeed while changing nothing.

Changing an element parameter invalidates the affected cached baselines, so the
next OPM read reflects it. Transient and FEC parameters take effect on the next
OPM sample (the EDFA reservoir's time constants from the next add/drop event);
RMSA parameters on the next admission.

Everything else — topology, backend, spectrum grid, server, simulation and
logging settings — is fixed at startup by design: changing it mid-run would make
measurements taken before and after the change incomparable. The transparent
controller's `startup` datastore is these files as loaded.
