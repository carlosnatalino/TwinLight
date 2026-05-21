# CLAUDE.md — T-API Network Digital Twin

## Project Overview

A **multi-dimensional optical network digital twin** that exposes T-API v2.6.0 compliant REST interfaces. It accepts GNPy topology JSON files, computes static QoT baselines via GNPy propagation, layers four analytical transient models on top, and streams live OPM data.

The intended use is as a realistic emulator of an Optical Domain Controller: for validating SDN control logic, what-if scenarios, network planning research, and paper experiments.

As add-on, the project offers a Web UI that can be used to visualize the network state, and the monitoring data.

---

## Architecture

```
tapi-twin-ui (React SPA)          ← port 5173 (dev) / static build
      │ HTTP polling + REST
      ▼
FastAPI backend (src/tapi_twin/)  ← port 8080
      │ owns all state, physics, RMSA
      ▼
GNPy (gnpy PyPI package)          ← propagation engine
      +
NetworkX DiGraph                  ← topology graph / routing

gRPC server (grpcio) ← stateless adapter, queries FastAPI via httpx.ASGITransport, port 50051
```

**Key architectural constraint**: FastAPI owns ALL state. The gNMI/gRPC server is a stateless protocol adapter that calls FastAPI in-process via `httpx.ASGITransport` — no TCP round-trip, no shared-memory coordination.

---

## Repository Layout

```
src/tapi_twin/           Backend Python package
  app.py                 FastAPI app factory (registers routers, attaches context)
  main.py                Entry point; starts FastAPI + gRPC concurrently
  cli.py                 CLI argument parsing; load_config() reads YAML
  config.py              Pydantic TwinConfig (root), GnpyConfig, TransientsConfig …
  models/                Pydantic TAPI data models (common, topology, connectivity)
  state/
    context.py           TapiContext — root singleton (topology, services, GNPy, caches)
    topology_state.py    TopologyGraph (NetworkX DiGraph, UID as node keys)
  loader/
    gnpy_topology.py     GNPy JSON → internal GnpyElement list
    tapi_builder.py      GnpyElement list → TAPI Topology + SIPs
  physics/
    gnpy_adapter.py      build_gnpy_network(), compute_path_baseline(), OpmBaseline
    modulation.py        ModulationFormat enum, ModulationParams, get_params()
    ber_conversion.py    gsnr_to_ber(), ber_to_q_db() — Gaussian approx via scipy erfc
    transients/
      edfa_reservoir.py  Per-EDFA gain drift → ΔGSNR (sinusoidal, stateless)
      polarization.py    PMD random-walk + PDL OSNR penalty (hinge model)
      phase_noise.py     EEPN-aware linewidth penalty
      environmental.py   Thermal CD drift (uses timezone_offset config)
      cascade.py         Compose all four models → measurements dict; hash_phase()
  algorithms/
    routing.py           k_shortest_paths() via nx.shortest_simple_paths
  api/
    common.py            GET /data/tapi-common:context and SIP endpoints
    topology.py          GET topology / node / link / NEP endpoints
    connectivity.py      CRUD /data/tapi-connectivity:connectivity-context/…
    photonic_media.py    GET /data/tapi-photonic-media:spectrum-context
    internal.py          GET /internal/opm, /internal/opm/{uuid},
                         GET /internal/services/{uuid} (path + metadata)
    admin.py             POST /admin/snapshot, POST /admin/restore,
                         GET /admin/snapshot/latest, GET /admin/snapshots,
                         GET /admin/snapshot/content
    middleware.py        PathDecodeMiddleware (%3A→: in paths),
                         RestconfContentTypeMiddleware (application/yang-data+json)
  grpc_server.py         Starts gNMI gRPC server; wires GnmiServicer + httpx transport
  streaming/
    gnmi_service.py      GnmiServicer: Capabilities + Subscribe (ONCE/STREAM/POLL)

tapi-twin-ui/            React SPA
  src/
    api/client.ts        TapiApiClient (fetch wrapper, all REST calls)
    api/types.ts         TypeScript interfaces mirroring TAPI JSON wire format
    store/               Zustand stores (connection, topology, monitoring)
    hooks/               usePolling, useMonitoringHistory
    pages/               MonitoringPage, AddServicePage, TopologyPage, …
    components/
      monitoring/        MetricCard, MetricChart, HistoryControls
      topology/          TopologyGraph (Cytoscape.js), DetailPanel, GraphControls

examples/
  twin_config.yaml       Small example — edfa_example_network.json (2 nodes)
  coronet_conus_config.yaml  CORONET CONUS — 75 ROADMs, 198 fiber spans
```

---

## Running the Project

```bash
# Backend
source venv/bin/activate   # Python 3.12 venv
tapi-twin --config examples/twin_config.yaml
# or for the large CORONET topology:
tapi-twin --config examples/coronet_conus_config.yaml

# Frontend (dev server)
npm --prefix tapi-twin-ui run dev   # http://localhost:5173

# Frontend (production build)
npm --prefix tapi-twin-ui run build
```

Backend health check: `curl http://localhost:8080/health`

---

## Key Constraints

1. **Do NOT modify anything in `related-projects/`.**  These are reference-only copies.
2. **GNPy is installed from PyPI** (`pip install gnpy`, currently 2.14.0). The documentation is available at https://gnpy.readthedocs.io/
3. **Python 3.12 venv** — the project requires Python 3.12 for gnpy compatibility.
4. **Tailwind CSS v3** in the frontend — NOT v4. Do not upgrade.
5. **Package manager: npm** (not bun, not pnpm) for the frontend.
6. **TAPI JSON keys are hyphenated** (`"modulation-format"`, `"end-point"`, etc.).
   Pydantic models use `alias=` with `by_alias=True` on serialization.
7. **Do not amend existing commits** — always create new commits when asked to commit.
8. **TAPI files are TAPI-only.** Files and routers under `api/common.py`, `api/topology.py`,
   and `api/connectivity.py` must implement only T-API v2.6.0 compliant interfaces (standard
   paths, standard JSON keys).  Any non-standard or internal interface — OPM data, service
   path metadata, admin utilities, WebSocket feeds, etc. — belongs in separate files
   (`api/internal.py`, `api/admin.py`, `api/streaming.py`, …) on separate URL prefixes
   (`/internal/`, `/admin/`, `/ws/`).  Never mix proprietary endpoints into TAPI routes.

---

## Backend API Endpoints (implemented)

### TAPI Standard Endpoints
| Method | Path |
|--------|------|
| GET | `/health` |
| GET | `/data/tapi-common:context` |
| GET | `/data/tapi-common:context/service-interface-point` |
| GET | `/data/tapi-common:context/service-interface-point={uuid}` |
| GET | `/data/tapi-common:context/tapi-topology:topology-context` |
| GET | `.../topology={uuid}` |
| GET | `.../topology={uuid}/node={uuid}` |
| GET | `.../topology={uuid}/link={uuid}` |
| POST | `/data/tapi-connectivity:connectivity-context/connectivity-service` |
| GET | `/data/tapi-connectivity:connectivity-context/connectivity-service` |
| GET | `.../connectivity-service={uuid}` |
| PUT | `.../connectivity-service={uuid}` |
| PATCH | `.../connectivity-service={uuid}` |
| DELETE | `.../connectivity-service={uuid}` |
| GET | `/data/tapi-path-computation:path-computation-context` |
| GET | `.../path-computation-service` |
| POST | `.../path-computation-service/compute-path` |
| GET | `/data/tapi-equipment:equipment-context` |
| GET | `.../equipment` |
| GET | `.../equipment={uuid}` |
| GET | `/data/tapi-photonic-media:spectrum-context` |

Connectivity-service GET (single and list) and POST/PUT/PATCH responses include **frequency-slot** (T-API L0) when the service has spectrum allocation: `nominal-central-frequency` (THz), `slot-width` (GHz). Spectrum-context returns grid parameters: `num-slots`, `slot-width-ghz`, `nominal-central-frequency-thz`.

### Internal Endpoints

Internal endpoints are only created and used if the T-API endpoints cannot be used to achieve the same functionality.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/internal/opm` | All services; physics-based or mock fallback |
| GET | `/internal/opm/{uuid}` | Single service |
| GET | `/internal/services/{uuid}` | Name, modulation-format, path hops, total-fiber-km |
| GET | `/internal/path-info?sip_a=&sip_z=&modulation=` | Path hops, total-fiber-km, GSNR/OPM estimate (Path UI) |

### Admin Endpoints (checkpoints)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/admin/snapshot` | Serialize connectivity + spectrum state to JSON. Optional body: `{"path": "…"}`. Default: `snapshots/twin-<ISO8601>.json`. |
| POST | `/admin/restore` | Restore from snapshot. Body: `{"path": "…"}`. |
| GET | `/admin/snapshot/latest` | Path and mtime of most recent snapshot in default dir. |
| GET | `/admin/snapshots` | List available snapshot files (path + timestamp), newest first. |
| GET | `/admin/snapshot/content?path=<file>` | Return JSON content of a snapshot; path must be under snapshot dir. |

Start with a saved snapshot: `tapi-twin --config … --restore snapshots/twin-….json` or `--restore-latest`.

### Connectivity Service POST Body
```json
{
  "tapi-connectivity:connectivity-service": {
    "name": [{"value-name": "service-name", "value": "my-link"}],
    "modulation-format": "DP-QPSK",
    "end-point": [
      {"local-id": "a-end", "service-interface-point": {"service-interface-point-uuid": "<SIP_A_UUID>"}},
      {"local-id": "z-end", "service-interface-point": {"service-interface-point-uuid": "<SIP_Z_UUID>"}}
    ]
  }
}
```
`modulation-format` accepts `"DP-QPSK"` (default), `"DP-16QAM"`, or `"DP-64QAM"`.
PUT replaces the service at the given UUID (body must be full connectivity-service; body UUID must match path). PATCH supports `name`, `administrative-state`, `lifecycle-state` (not `modulation-format`). Error responses use RESTCONF format: `application/yang-data+json` with `ietf-restconf:errors` (error-tag, error-message). Links include `latency-characteristic` (propagation-delay in ns from fiber length / speed of light).

### OPM Response Format
```json
{
  "services": [
    {
      "service-uuid": "…",
      "timestamp": 1234567890.123,
      "measurements": {
        "osnr-db": 30.2, "gsnr-db": 27.4, "pre-fec-ber": 2.6e-26,
        "q-factor-db": 19.3, "chromatic-dispersion-ps-per-nm": 1.3, "pmd-ps": 0.87
      }
    }
  ],
  "timestamp": 1234567890.123
}
```

### Service Info Response Format (`/internal/services/{uuid}`)
```json
{
  "service-uuid": "…", "name": "MyLink", "modulation-format": "DP-16QAM",
  "hops": [
    {"uid": "Site_A", "type": "Transceiver", "distance_km_to_next": 80.0},
    {"uid": "Site_B", "type": "Transceiver", "distance_km_to_next": null}
  ],
  "total-fiber-km": 80.0
}
```

---

## Physics Engine

### GNPy Integration (gnpy 2.14.0 specifics)

Using existing functionalities from the public interface of GNPy should be preferred over implementing the functionalities in our own digital twin.

- Network nodes ARE element objects (not uid→element dicts). Access UID via `node.uid`.
- `designed_network(equipment, network)` — correct entry point for post-design (not the old `build_network(pref_ch_db=...)` which has changed signature).
- `network_from_json(topo_data: dict, equipment)` — takes a parsed dict, not a Path.
- EDFA propagation needs ≥ 2 channels (`interpol_params` computes slot width from `channel_freq[1] - channel_freq[0]`).  Use 88-channel C-band comb.
- `si.pmd` and `si.latency` are arrays (per channel), not scalars.
- Path elements must be deep-copied before propagation to avoid mutating cached state.

### OPM Baseline Caching
`TapiContext` maintains:
- `_gnpy_uid_map`: `{uid: gnpy_element}` built once at startup
- `_route_cache`: `{(src_uid, dst_uid): [uid_list]}` — k-shortest path results
- `_baseline_cache`: `{service_uuid: OpmBaseline}` — one GNPy propagation per service
- `_propagation_lock`: asyncio.Lock serialises concurrent propagation requests

Baseline is cleared on service deletion (`invalidate_baseline(uuid)`).

### Transient Models
Three models are pure functions of wall-clock time `t`; the EDFA model is **stateful**
via `EdfaStateTracker` (tracks per-EDFA channel count and last event). Phase offsets
for sinusoidal models are seeded via MD5 hash of `(uid, metric)` for per-element uniqueness.

| Model | Effect | Mechanism | Timescale |
|-------|--------|-----------|-----------|
| EDFA reservoir | ±ΔGSNR, ±ΔOSNR | Bononi-Rusch exponential step response (stateful `EdfaStateTracker`); falls back to sinusoidal when no tracker | µs–ms (add ~10 µs, drop ~100 µs) |
| Polarization | ΔPMD + PDL OSNR penalty (GSNR+OSNR) | PMD: per-fiber sinusoidal drift, quadrature sum; PDL: Zarkosvky-Shtaif 2020 hinge model (Eq. 3-5), per-hinge transfer cascaded over ROADM/EDFA hinges, each with a UID-seeded Maxwell-distributed PDL and an incommensurate drift period | 90 s / 300 s |
| Phase noise (EEPN) | ΔGSNR only | Shieh-Ho dispersion-dependent: `α = π·c/(2f₀²)·|D_t|·B·Δν_LO`; LO linewidth only | ~120 s |
| Environmental | ΔCD + loss penalty (GSNR+OSNR) | Kato dD/dT thermal CD drift + fiber loss variation | diurnal (86 400 s) |

`cascade.py:apply_all_transients()` composes all four via a QoT combiner with separate
GSNR and OSNR tracks. GSNR receives all four models; OSNR receives only EDFA, PDL, and
environmental loss (not EEPN, which is a DSP-domain impairment). BER and Q are recomputed
from perturbed GSNR using Gaussian approximation (scipy `erfc` / `erfcinv`).

The `EdfaStateTracker` is owned by `TapiContext` and updated on service add/delete.
`apply_all_transients()` accepts an optional `edfa_tracker` parameter; when `None`,
the EDFA model falls back to stateless sinusoidal approximation.

### Modulation Formats
Three formats supported, selectable per-service at creation:

| Format | Bits/sym | req. GSNR | Baud rate |
|--------|----------|-----------|-----------|
| DP-QPSK | 2 | 8.5 dB | 32 Gbaud |
| DP-16QAM | 4 | 14.5 dB | 32 Gbaud |
| DP-64QAM | 6 | 20.5 dB | 32 Gbaud |

---

## SIP ↔ GNPy UID Mapping

`TapiBuilder` stores the GNPy element UID in each SIP's name list:
```python
sip.name = [NameAndValue(value_name="node-name", value=el.uid)]
```
Recovered in `TapiContext.__init__` as `_sip_to_gnpy_uid`.

---

## gNMI / gRPC Interface

### Transport
`grpc_server.py:start_grpc_server()` is called from `main.py` via `asyncio.gather` alongside
uvicorn, so both servers share the same event loop.  The `GnmiServicer` holds an
`httpx.AsyncClient` with `ASGITransport(app=app)` — all gNMI→REST calls are in-process
(no TCP).

### Supported RPCs
| RPC | Status |
|-----|--------|
| `Capabilities` | Returns tapi-common + tapi-topology model data, JSON_IETF encoding |
| `Subscribe` ONCE | Fetch all subscribed paths once, then `sync_response=True` |
| `Subscribe` STREAM | Initial sync, then re-poll at `sample_interval` (default 10 s) |
| `Subscribe` POLL | Like ONCE; each gNMI poll message re-fetches |
| `Get` / `Set` | Not implemented (gRPC UNIMPLEMENTED error) |

### Path Mapping (`_PATH_MAP` in `gnmi_service.py`)
gNMI path elements are joined with `/` and looked up in a static map first, then fall
back to `/data/<joined>`:

```
tapi-common:context                   → /data/tapi-common:context
tapi-common:context/tapi-topology:topology-context
                                      → /data/tapi-common:context/tapi-topology:topology-context
topology[uuid=X]                      → topology=X  (key → =value)
```

OPM and connectivity paths are **not** in `_PATH_MAP` yet — add entries there when
extending gNMI coverage to those resources.

### Middleware Notes
Two Starlette middlewares in `api/middleware.py` (applied in `app.py`):

| Middleware | Purpose |
|------------|---------|
| `PathDecodeMiddleware` | URL-decodes `%3A` → `:` before routing. Browsers per WHATWG URL spec encode colons in paths; TAPI RESTCONF paths contain literal colons. Applied innermost (last added). |
| `RestconfContentTypeMiddleware` | Sets `Content-Type: application/yang-data+json` on all `/data/` responses. |

---

## Configuration (YAML)

All paths in YAML resolve **relative to the YAML file's directory**.

Key fields:
```yaml
gnpy:
  topology: "../related-projects/oopt-gnpy/gnpy/example-data/edfa_example_network.json"
  equipment: "../related-projects/oopt-gnpy/gnpy/example-data/eqpt_config.json"
  no_insert_edfas: false   # true for CORONET (no inline EDFAs in source topology)

transients:
  edfa_reservoir: { enabled: true, tau_ms: 10.0 }
  polarization:   { enabled: true, pdl_per_roadm_db: 0.5, pdl_per_edfa_db: 0.1,
                    ase_distribution: distributed }
  phase_noise:    { enabled: true, tx_linewidth_hz: 100000.0 }
  environmental:  { enabled: false, temp_variation_c: 5.0,
                    temp_cycle_period_s: 86400.0, timezone_offset: 0.0 }

rmsa:
  k_shortest_paths: 3
  qot_margin_db: 1.5
```

`GNPy is only loaded if `gnpy.equipment` is configured`.  Without it, OPM falls back to sinusoidal mock data.

---

## Frontend Stack

- React 18, TypeScript 5, Vite 6, Tailwind CSS **v3** (not v4)
- Zustand v5, Cytoscape.js + react-cytoscapejs, Recharts
- `vitest.config.ts` and `vite.config.ts` are **separate** — do NOT merge them (type
  conflicts between `vitest/config` and `vite` exports)
- `@types/node` installed as devDep for path aliases in `vite.config.ts`
- Custom type declarations: `src/types/react-cytoscapejs.d.ts`,
  `src/types/cytoscape-dagre.d.ts`
- Cytoscape stylesheet type: `Array<cytoscape.StylesheetStyle | cytoscape.StylesheetCSS>`
  (not `cytoscape.Stylesheet[]`)
- Use `globalThis` (not `global`) in test files — no Node.js types in browser tsconfig
- localStorage key prefix: `tapi-twin-ui:`

---

## What Is and Is Not Yet Implemented

### Implemented ✓

**Backend:**
- Full TAPI topology read endpoints (context, SIPs, topology, nodes, links, NEPs)
- Connectivity service CRUD (POST, GET, PUT, PATCH, DELETE)
- Per-service modulation format (DP-QPSK / DP-16QAM / DP-64QAM)
- GNPy propagation baseline with 88-channel C-band SI
- All four analytical transient models with literature-accurate formulas:
  EDFA Bononi exponential step (stateful tracker), Shieh-Ho EEPN,
  Zarkosvky-Shtaif hinge-model PDL OSNR penalty, Kato environmental drift
- `/internal/services/{uuid}` path topology endpoint
- `/internal/path-info` path computation with QoT estimate
- gNMI gRPC server (ONCE / STREAM / POLL subscribe modes, Capabilities RPC)
- **RMSA:** spectrum state (per-link slot arrays), first-fit spectrum assignment,
  QoT-aware admission (path via NetworkX k-shortest, spectrum first-fit, GSNR ≥ req + margin).
  Path computation uses our graph (GNPy does not provide path-finding); spectrum allocation
  is our own (GNPy has no spectrum/slot APIs).
- Admin snapshot/restore endpoints (POST snapshot, POST restore, GET latest/list/content)
- Path computation RPC endpoint (POST compute-path)
- Equipment context endpoints (GET equipment list and detail)
- Photonic media spectrum context endpoint

**Frontend (React SPA):**
- Dashboard: connection status, topology summary cards, node/link state breakdown
- Topology: interactive Cytoscape.js graph, node/link detail panel, layout controls
- Device detail: NEP table, SIP badges, state indicators, connected links, OPM dashboard
- Link detail: endpoints, span elements table, state indicators
- Monitoring: service selector, 6 metric charts (OSNR, GSNR, BER, Q, CD, PMD),
  time range selector, pause/resume, CSV export, min/max/avg stats, auto-scaled Y-axis
- Services: service list, logical topology graph, service detail panel with spectrum info,
  delete with confirmation
- Add Service: SIP selection, modulation format, direction, admin/lifecycle state
- Equipment: list/filter by type, grouped by category (Transceiver, Roadm, Fiber, Edfa)
- Spectrum: grid context display, service allocation table
- Spectrum Grid: visual slot allocation heatmap (per ROADM-ROADM link), hover tooltips
- Path Computation: A/Z SIP selection, k-shortest paths, QoT estimate display
- Settings: DT URL, test connection, poll interval, cache management
- Constellation diagram placeholder (component exists, rendering not implemented)
- Eye diagram placeholder (component exists, rendering not implemented)

### Not Yet Implemented
- gNMI OPM/connectivity path subscriptions (only topology paths currently in `_PATH_MAP`)
- Eye diagram and constellation diagram rendering (placeholders exist in UI)
- Event-driven simulation engine (clock, event queue)
- WebSocket OPM fallback
- Post-FEC BER (only pre-FEC BER implemented)
- Multi-band (C-band only)
- Geographic map view in UI

---

## Example Topologies

| Config | Topology | Nodes | Fibers |
|--------|----------|-------|--------|
| `examples/twin_config.yaml` | edfa_example_network.json | 2 TRX + inline EDFAs | short |
| `examples/coronet_conus_config.yaml` | CORONET_CONUS_Topology.json | 75 TRX + 75 ROADM | 198 |

CORONET uses `no_insert_edfas: true` — it has bare Fiber spans with no pre-placed EDFAs.

---

## Known Physics Model Limitations (vs. Literature)

The transient models use literature-accurate analytical formulas. Remaining simplifications:

| Model | Current Implementation | Literature Says | Status |
|-------|----------------------|-----------------|--------|
| **EDFA reservoir** | Bononi-Rusch exponential step response (Eq. 19/29 [16]) with stateful `EdfaStateTracker`, asymmetric τ_add/τ_drop, linear dB cascade (Sun 1997) | Full ODE dr/dt (Bononi Eq. 5) with spectral hole burning, gain clamping | Fixed — exponential step is accurate for channel add/drop; full ODE is future work |
| **Phase noise (EEPN)** | Shieh-Ho dispersion-dependent: `α = π·c/(2f₀²)·|D_t|·B·Δν_LO`, penalty uses pre-EEPN GSNR, LO linewidth only | Shieh-Ho 2008 Eq. 33-41 | Fixed — matches literature |
| **PDL OSNR penalty** | Zarkosvky-Shtaif 2020 hinge model: per-hinge transfer `aⱼ = (1+γⱼ·cosθⱼ)/√(1−γⱼ²)` (Eq. 5) cascaded over ROADM/EDFA hinges; per-hinge PDL is UID-seeded Maxwell-distributed (Miotto 2025) and each hinge drifts with an incommensurate period for ergodic joint coverage; OSNR penalty depends on ASE distribution (D'Amico 2023 / Miotto 2025) | Zarkosvky-Shtaif 2020 Eq. 3-5; coherent-system usage per D'Amico OFC 2023, Miotto OFC 2025 | Fixed — coherent hinge model (replaces IMDD-era Lichtman formula); deterministic time-drift covers the alignment ensemble without explicit Monte Carlo |
| **PMD drift** | Sinusoidal, 10% amplitude, 90 s period | Gordon-Kogelnik 2000: Maxwell-distributed DGD; quadrature accumulation correct; drift timescale not specified (field-dependent) | Low severity — reasonable approximation |
| **BER conversion** | erfc-based Gaussian approximation per format | Standard textbook M-QAM (correct); Curri 2022 uses b2b thresholds instead | Correct |
| **QoT combiner** | GSNR receives all 4 models; OSNR receives EDFA + PDL + Env loss only (not EEPN) | EEPN is DSP-domain, should not affect OSNR | Correct routing |

---

## Literature

Physics model references:
- `Analytical Models for Time-Varying Optical Impairments…` — EDFA reservoir, PMD,
  PDL, phase noise, environmental models (primary reference for transients)
- `Optical network emulator and digital twin.md` — DT architecture survey

Key citation keys in code comments: `Carena_2014` (GN model), `Curri_2022` (LP GSNR),
`Bononi-Rusch` (EDFA reservoir, Eq. 19/29), `Shieh-Ho` (EEPN), `Gordon-Kogelnik` (PMD),
`Zarkosvky-Shtaif 2020` (PDL hinge model, Eq. 3-5), `D'Amico OFC 2023` /
`Miotto OFC 2025` (PDL→OSNR penalty), `Kato` (thermal CD drift).
