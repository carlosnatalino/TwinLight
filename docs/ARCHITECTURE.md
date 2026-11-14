# Architecture

How TwinLight is put together, and why. For the endpoint catalogue see
[API.md](API.md); for the impairment models see [PHYSICS.md](PHYSICS.md).

## Overview

```
                    twinlight-ui (React SPA, port 5173)
                              │  REST polling
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI application  (src/twinlight, port 8080)                │
│                                                                 │
│  api/          T-API routers + /internal, /admin, /config,      │
│                /transparent                                     │
│  state/        TapiContext — the single owner of all state      │
│  streaming/    T-API measurement stream: sampler + record log   │
│  transparent/  transparent controller: paths, tree, datastores  │
│  physics/      QoT baseline + four transient models             │
│  algorithms/   routing (k-shortest) and spectrum assignment     │
│  loader/       GNPy JSON → internal elements → T-API objects    │
│  models/       Pydantic models of the T-API wire format         │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │  reads the record log (in-process)
                    gNMI gRPC server (port 50051)
                    T-API measurement stream, TR-548 §6
```

Both servers and the stream's sampler run on one asyncio event loop, started
together from `main.py` with `asyncio.gather`.

## Two invariants

Everything else follows from these.

### 1. The FastAPI application owns all state

`TapiContext` (`state/context.py`) is the single root object: topology, service
inventory, spectrum occupancy, physics caches, EDFA tracker state. It is created
in the app factory and attached to `app.state.context`.

The gNMI server holds **no state of its own**. It serves the T-API
performance-measurement stream (TR-548 §6) from the record log of the
`MeasurementStream` attached to `app.state.measurement_stream`. That stream's
sampler takes each reading through `api/internal.py:sample_service_opm` — the
function behind `GET /internal/opm` — so there is no second copy of the
topology, no second OPM computation, and no cache-coherency problem between the
two northbound interfaces: a gNMI record and a REST read of the same lightpath
at the same instant carry the same numbers.

The cost is that the gNMI server cannot outlive or scale independently of the
REST app. For a digital twin, where consistency between interfaces is the whole
point, that trade is worth making. The log sits behind the small `RecordLog`
interface (append, replay from a time, trim by age, oldest) so that a shared
backend such as Valkey Streams could take its place without touching the gNMI
code.

### 2. T-API surfaces stay standard

Routers under `api/common.py`, `api/topology.py`, `api/connectivity.py`,
`api/path_computation.py`, `api/equipment.py`, `api/oam.py` and
`api/streaming.py` implement **only** T-API v2.6.0: standard paths, standard hyphenated JSON keys, RESTCONF error bodies,
`application/yang-data+json` responses.

There is deliberately no photonic-media router. T-API puts the photonic layer
in *augments* — on the service-interface-point and on the connectivity-service
end-point — not in a module-level resource of its own, so it is served by the
routers that own those objects. A `tapi-photonic-media:` top-level resource
would have to invent leaves the YANG does not define, so twin-specific grid
parameters are served from `/internal/spectrum-context` instead.

Everything the standard does not cover lives on a separate prefix and in a
separate module:

| Prefix | Module | Contents |
|--------|--------|----------|
| `/internal/` | `api/internal.py`, `api/diagrams.py` | OPM readings, service path metadata, path-info estimates, eye/constellation synthesis |
| `/admin/` | `api/admin.py` | Snapshot and restore |
| `/config/` | `api/config.py`, `api/parameters.py` | Runtime parameter overrides (fiber loss, EDFA NF, link failure); path-addressed, all-or-nothing parameter batches |
| `/transparent/` | `api/transparent.py` (logic in `transparent/`) | The fully transparent controller view: every element and parameter as one data tree, ns-3-style paths, RESTCONF-shaped datastores and operations — see [TRANSPARENT_CONTROLLER.md](TRANSPARENT_CONTROLLER.md) |
| `/metrics` | `api/metrics.py` | Prometheus exposition |

A T-API client therefore never encounters a proprietary extension inside a
standard path, and the compliance boundary is visible in the directory listing
rather than buried in a router.

## Module tour

```
src/twinlight/
  app.py                 FastAPI app factory: routers, middleware, app.state
  main.py                Entry point — REST + gRPC on one event loop
  cli.py                 Argparse CLI; load_config() merges YAML and flags
  config.py              Pydantic TwinConfig tree (validated, documented)
  example_data.py        twinlight-fetch-examples: provisions GNPy topologies

  models/                Pydantic models of the T-API wire format
    common.py              Context, ServiceInterfacePoint, NameAndValue, states
    topology.py            Topology, Node, Link, NodeEdgePoint
    connectivity.py        ConnectivityService, endpoints, photonic augments
    equipment.py           Equipment context objects
    path_computation.py    Path-computation service objects
    oam.py                 tapi-oam oam-job (provider-managed, read-only)

  state/
    context.py             TapiContext — root state owner (see below)
    topology_state.py      TopologyGraph: NetworkX DiGraph keyed by element UID
    spectrum_state.py      Per-link slot occupancy arrays (6.25 GHz flexi-grid)
    twin_overrides.py      Runtime-mutable twin config (transients, rmsa, fec):
                           allow-list and pydantic-constraint validation
    parameter_path.py      ns-3-style parameter paths: parse + resolve to (uid, attr)
    parameter_ops.py       Atomic set / to-by-by_percent batches over parameter paths

  loader/
    gnpy_topology.py       GNPy JSON → list[GnpyElement]
    egn_topology.py        Same, for the EGN backend's span abstraction
    tapi_builder.py        GnpyElement list → T-API Topology, Nodes, Links, SIPs

  physics/
    backend.py             PhysicalBackend Protocol (structural, not inherited)
    gnpy_backend.py        GNPy split-step implementation
    egn_backend.py         Closed-form GN/EGN implementation
    egn_kernel.py          The GN-model kernel itself (Carena 2012/2014)
    gnpy_adapter.py        build_gnpy_network(), compute_path_baseline(), OpmBaseline
    element_params.py      Per-element parameter read/write for the /config plane
    modulation.py          ModulationFormat enum and per-format parameters
    ber_conversion.py      GSNR → pre-FEC BER → Q-factor (Gaussian approximation)
    fec.py                 Pre-FEC → post-FEC BER: threshold and hard-decision models
    gmi.py                 GSNR → GMI / NGMI (Gauss–Hermite quadrature)
    analytical_metrics.py  CD/PMD/latency accumulation along a path
    transients/            The four time-varying impairment models + cascade.py

  transparent/           The transparent optical domain controller
    path.py                ns-3-style paths: selectors, [i-j] indices, $Kind casts
    tree.py                The data tree, built on demand from TapiContext;
                           leaf specs shared with the schema endpoint
    datastore.py           Datastores (operational, running, candidate,
                           startup), change sets, validate/apply, compare

  algorithms/
    routing.py             k_shortest_paths() over the NetworkX graph
    spectrum_assignment.py First-fit slot allocation with guard bands

  api/                   Routers (see the table above)
  streaming/             The T-API performance-measurement stream (TR-548 §6)
    measurement_stream.py  Sampler: one OPM sample per service per tick,
                           stream state (ALIGNING → ACTIVE), subscriber wake-up
    record_log.py          RecordLog interface + in-process truncated log
    records.py             OPM sample ↔ tapi.MeasurementDetails, oam-job paths
    gnmi_service.py        TapiGnmiServicer: Capabilities + TR-548 Subscribe
                           (validation, ST-6.4 resume); bearer-token interceptor
    proto/                 Vendored gNMI and TAPI .proto files and their
                           generated code (committed, not linted)
  output/
    eye_diagram.py         Statistical eye synthesis from OPM
    constellation.py       Statistical constellation synthesis from OPM
  grpc_server.py         Builds and runs the gRPC server (auth when configured)

src/twinlight_client/    Companion client library and CLI
  client.py                TapiClient — async REST wrapper (named for the
                           protocol it speaks, not for this project)
  streaming.py             TapiStreamClient — TR-548 discovery, subscription,
                           decoding and ST-6.4 resume
  cli.py                   twinlight-client: pick a service, follow its records
  soft_failure.py          twinlight-soft-failure: immediate changes, and the
                           clock and ramp logic for gradual ones
  transparent.py           TransparentClient — sync client for /transparent
  transparent_cli.py       twinlight-transparent: get/set/revert/commit/…
```

## Startup sequence

1. `cli.py:load_config()` reads the YAML file and merges CLI overrides. All
   relative paths in the YAML resolve against the YAML file's own directory, so
   a scenario directory can be relocated wholesale.
2. `TapiContext.__init__` loads the GNPy topology JSON into `GnpyElement`
   objects, hands them to `TapiBuilder` to produce the T-API `Topology`, `Node`,
   `Link` and `ServiceInterfacePoint` objects, and builds the `TopologyGraph`.
3. The selected `PhysicalBackend` is constructed. GNPy additionally runs
   `designed_network(equipment, network)` here, which is where amplifier
   placement and power design happen.
4. `SpectrumState` allocates the per-link slot arrays.
5. If `--restore` / `--restore-latest` was given, the snapshot is replayed:
   services are re-created and spectrum occupancy is reinstated. A snapshot
   taken under a different physical-layer backend is rejected.
6. `main.py` starts uvicorn, the gRPC server and the measurement stream's
   sampler with `asyncio.gather`. The stream is `ALIGNING` until the sampler's
   first tick is logged, then `ACTIVE`.

## Request lifecycles

### Creating a connectivity service

`POST /data/tapi-connectivity:connectivity-context/connectivity-service`

1. Resolve both endpoint SIP UUIDs to GNPy element UIDs via
   `TapiContext._sip_to_gnpy_uid` (populated at build time — `TapiBuilder`
   stores each element's UID in the SIP's name list).
2. Compute candidate paths: GNPy's `compute_constrained_path` when an equipment
   library is loaded, otherwise `algorithms/routing.py:k_shortest_paths`.
   Results are memoised in `_route_cache`.
3. For each candidate, in order: assign spectrum first-fit
   (`algorithms/spectrum_assignment.py`), propagate the path through the active
   backend to obtain an `OpmBaseline`, and check
   `GSNR ≥ required_gsnr(format) + rmsa.qot_margin_db`.
4. The first candidate that satisfies both spectrum and QoT is admitted. The
   baseline is cached in `_baseline_cache` keyed by service UUID, the EDFA
   tracker is told a channel was added, and the T-API object is returned with
   the assigned spectrum on its end-points.
5. If no candidate passes, the request is refused with a RESTCONF error body
   explaining which constraint failed.

Admission also creates the service's `tapi-oam` oam-job, the anchor of its
records on the measurement stream. Deletion reverses all of this: slots are
released, `invalidate_baseline(uuid)` drops the cached propagation, the EDFA
tracker records the drop event, and the oam-job is removed.

### Reading OPM

`GET /internal/opm/{uuid}`, a gNMI stream record, and a Prometheus scrape all
funnel into the same computation (`sample_service_opm` for the first two):

1. Fetch the cached `OpmBaseline` for the service (propagating once if absent).
2. Call `physics/transients/cascade.py:apply_all_transients(baseline, t, …)`
   with the current wall-clock time and the context's `EdfaStateTracker`.
3. The cascade evaluates the four models, combines them on separate GSNR and
   OSNR tracks, and recomputes BER and Q from the perturbed GSNR, then the
   post-FEC BERs and GMI from those (`physics/fec.py`, configured by `fec`).

The baseline propagation is the expensive step and is cached; the transient
evaluation is cheap and deliberately *not* cached, which is what makes every
read reflect the current instant.

### Changing parameters by path

`POST /config/parameters` and `POST /config/parameters/changes`

1. `state/parameter_path.py` parses each path and resolves it against the
   backend's `uid_map` and `supported_attributes()` schema, so the same path
   language works under GNPy and EGN. `/Service` scopes use the
   element → service reverse index; `/Link` scopes use an inline-element →
   terminal index built at startup by walking the backend's own graph (GNPy's
   designed network contains booster amplifiers the parsed topology lacks).
2. `state/parameter_ops.py` computes every resulting value from the values
   before the batch and checks them all — range, type, no parameter written
   twice — collecting every error before refusing. A dry run stops here.
3. `TapiContext.apply_element_overrides_atomic` writes the batch through
   `apply_element_overrides`, which records the overrides and invalidates the
   affected baselines. A write failing despite validation rolls back the earlier
   ones, so a batch lands entirely or not at all.

The twin keeps no clock for these. A gradual change (a soft failure) is the
`twinlight-soft-failure` client calling step 3 repeatedly: it plans a scenario
with one dry run of `/changes`, then sends each tick's values for every due ramp
in one `POST /config/parameters`. Timing, ramp shapes and recovery therefore
live entirely in the client, and the twin's state is always just its current
parameter values — which is also all a snapshot needs to capture.

### Changing parameters through the transparent controller

`PUT`, `PATCH`, YANG Patch and `DELETE` on `/transparent/data/…` (running) or
`/transparent/ds/candidate/…`, and `POST /transparent/operations/commit`

1. `transparent/path.py` parses the path — from the *undecoded* request path,
   so `%2F` stays inside a segment — and `transparent/tree.py` resolves it
   against a tree built on demand from the live context. The tree is a view:
   `/ElementList`, `/NodeList`, `/LinkList/…/ElementList` and
   `/ServiceList/…/ElementList` all reach the same element leaves.
2. `transparent/datastore.py` turns the request into a change set
   `{LeafKey: value | REVERT}`. Only two kinds of key exist — an element
   attribute and a twin-config field — so every view writes the same leaf.
3. The whole change set is validated (backend allow-list bounds for element
   attributes, the pydantic model for twin config, cross-field rules on the
   section as it would be after the change), reporting every error.
4. A running write applies it: `apply_element_overrides_atomic` for element
   values and failures, `revert_element_overrides` / `revert_twin_overrides`
   for reverts, `apply_twin_overrides` for twin config. These are the methods
   `/config` uses, so baselines, override records and snapshots follow. A
   candidate write stores the change set instead, and `commit` applies the
   accumulated set the same way.

Two pieces of `TapiContext` state exist for this interface:

- **the running revision** (`ctx.revision`), bumped by every configuration
  change through any of those methods — whichever router called them. It is
  the running datastore's ETag, so a conditional write through `/transparent`
  notices a change made through `/config/set`;
- **startup values** (`_element_startup`, `_startup_config`): before an element
  attribute is first written, its value and the backend's capture token are
  recorded; the token restores exactly what the engine stored, an unset value
  included. Reverts — and `restore_from`, which now reverts before reapplying a
  snapshot — use them.

The QoT what-if (`validate` with `with-qot-impact`) is
`TapiContext.preview_qot`: under `_propagation_lock` it writes the changed
attributes to the live elements, propagates each affected service's allocated
path, and restores every attribute from its capture token in a `finally`.
Backend propagation does not yield to the event loop, so no other request can
observe the tentative values; nothing is cached.

## Concurrency

- One asyncio event loop hosts uvicorn, the gRPC server, the stream sampler,
  and every request. The sampler samples every service once per tick whether or
  not anyone is subscribed; subscribers wait on an `asyncio.Condition` and are
  handed the same logged record objects.
- `TapiContext._propagation_lock` (an `asyncio.Lock`) serialises physical-layer
  propagation. GNPy propagation mutates element state, so concurrent
  propagations would interfere; path elements are additionally deep-copied
  before propagation so cached network state is never mutated.
- Everything else is single-threaded by construction, so no further locking is
  needed.

## Caches

| Cache | Key | Invalidated by |
|-------|-----|----------------|
| `_gnpy_uid_map` | element UID | never (built once at startup) |
| `_route_cache` | `(src_uid, dst_uid)` | topology mutation (e.g. link failure) |
| `_baseline_cache` | service UUID | service deletion, `/config` parameter change (including `/config/parameters` batches), link failure |

## Middleware

Two Starlette middlewares, applied in `app.py` (order matters — the last added
runs innermost):

| Middleware | Purpose |
|------------|---------|
| `PathDecodeMiddleware` | URL-decodes `%3A` → `:` before routing. Browsers percent-encode colons in paths per the WHATWG URL spec, but T-API RESTCONF paths contain literal colons. |
| `RestconfContentTypeMiddleware` | Sets `Content-Type: application/yang-data+json` on all `/data/` responses, as RESTCONF requires. |

## Extension points

- **A new physical-layer backend**: implement the `PhysicalBackend` Protocol in
  `physics/backend.py` and register it in the backend selection in `app.py`.
  The Protocol is structural, so no inheritance is required.
- **A new transient model**: add a pure function (or a stateful tracker, as the
  EDFA model does) under `physics/transients/`, then compose it in
  `cascade.py:apply_all_transients` — deciding explicitly whether it feeds the
  GSNR track, the OSNR track, or both.
- **A new RMSA policy**: `algorithms/` holds routing and spectrum assignment as
  independent functions; both are called from the admission path in
  `TapiContext`.
- **Multi-band**: the spectrum grid is parameterised (`spectrum.num_slots`,
  `slot_width_ghz`, `center_frequency_thz`) but the physics currently assumes
  C-band only.
