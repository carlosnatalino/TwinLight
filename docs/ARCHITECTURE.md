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
│  api/          T-API routers + /internal, /admin, /config       │
│  state/        TapiContext — the single owner of all state      │
│  physics/      QoT baseline + four transient models             │
│  algorithms/   routing (k-shortest) and spectrum assignment     │
│  loader/       GNPy JSON → internal elements → T-API objects    │
│  models/       Pydantic models of the T-API wire format         │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │  httpx.ASGITransport (in-process)
                    gNMI gRPC server (port 50051)
```

Both servers run on one asyncio event loop, started together from
`main.py` with `asyncio.gather`.

## Two invariants

Everything else follows from these.

### 1. The FastAPI application owns all state

`TapiContext` (`state/context.py`) is the single root object: topology, service
inventory, spectrum occupancy, physics caches, EDFA tracker state. It is created
in the app factory and attached to `app.state.context`.

The gNMI server holds **no state of its own**. It is a protocol adapter that
translates gNMI paths into REST paths and calls the *same* FastAPI application
in-process through `httpx.AsyncClient(transport=ASGITransport(app=app))`. There
is no TCP round-trip, no second copy of the topology, and no cache-coherency
problem between the two northbound interfaces — a gNMI subscriber and a REST
client observing the same lightpath at the same instant see the same numbers.

The cost is that the gNMI server cannot outlive or scale independently of the
REST app. For a digital twin, where consistency between interfaces is the whole
point, that trade is worth making.

### 2. T-API surfaces stay standard

Routers under `api/common.py`, `api/topology.py`, `api/connectivity.py`,
`api/path_computation.py` and `api/equipment.py` implement **only** T-API
v2.6.0: standard paths, standard hyphenated JSON keys, RESTCONF error bodies,
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
| `/config/` | `api/config.py` | Runtime parameter overrides (fiber loss, EDFA NF, link failure) |
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

  state/
    context.py             TapiContext — root state owner (see below)
    topology_state.py      TopologyGraph: NetworkX DiGraph keyed by element UID
    spectrum_state.py      Per-link slot occupancy arrays (6.25 GHz flexi-grid)
    twin_overrides.py      Runtime parameter overrides from the /config plane

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
    analytical_metrics.py  CD/PMD/latency accumulation along a path
    transients/            The four time-varying impairment models + cascade.py

  algorithms/
    routing.py             k_shortest_paths() over the NetworkX graph
    spectrum_assignment.py First-fit slot allocation with guard bands

  api/                   Routers (see the table above)
  streaming/
    gnmi_service.py        GnmiServicer: Capabilities + Subscribe (ONCE/STREAM/POLL)
    proto/                 Generated gNMI protobuf/gRPC code (committed, not linted)
  output/
    eye_diagram.py         Statistical eye synthesis from OPM
    constellation.py       Statistical constellation synthesis from OPM
  grpc_server.py         Starts the gRPC server and wires the in-process transport

src/twinlight_client/    Companion client library and CLI
  client.py                TapiClient — async REST wrapper (named for the
                           protocol it speaks, not for this project)
  streaming.py             GnmiConsumer — async gNMI Subscribe consumer
  cli.py                   twinlight-client: pick a service, stream its OPM
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
6. `main.py` starts uvicorn and the gRPC server with `asyncio.gather`.

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

Deletion reverses this: slots are released, `invalidate_baseline(uuid)` drops
the cached propagation, and the EDFA tracker records the drop event.

### Reading OPM

`GET /internal/opm/{uuid}`, a gNMI sample, and a Prometheus scrape all funnel
into the same computation:

1. Fetch the cached `OpmBaseline` for the service (propagating once if absent).
2. Call `physics/transients/cascade.py:apply_all_transients(baseline, t, …)`
   with the current wall-clock time and the context's `EdfaStateTracker`.
3. The cascade evaluates the four models, combines them on separate GSNR and
   OSNR tracks, and recomputes BER and Q from the perturbed GSNR.

The baseline propagation is the expensive step and is cached; the transient
evaluation is cheap and deliberately *not* cached, which is what makes every
read reflect the current instant.

## Concurrency

- One asyncio event loop hosts uvicorn, the gRPC server, and every request.
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
| `_baseline_cache` | service UUID | service deletion, `/config` parameter change, link failure |

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
