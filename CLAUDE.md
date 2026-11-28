# CLAUDE.md — TwinLight

Guidance for Claude Code (and other AI coding agents) working in this
repository.

**Read the real documentation first — this file does not duplicate it:**

| Question | Document |
|----------|----------|
| What is this project, how do I run it? | [README.md](README.md) |
| How is the code organised, what owns what? | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| What endpoints exist? | [docs/API.md](docs/API.md) |
| What does this config field do? | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| Which paper does this equation come from? | [docs/PHYSICS.md](docs/PHYSICS.md) |
| Is this endpoint T-API compliant? | [docs/TAPI_COMPLIANCE.md](docs/TAPI_COMPLIANCE.md) |
| Is this a known issue, and is a fix planned? | [docs/ROADMAP.md](docs/ROADMAP.md) |
| How does the transparent controller (`/transparent`) work? | [docs/TRANSPARENT_CONTROLLER.md](docs/TRANSPARENT_CONTROLLER.md) |
| How do I set up, test and submit a change? | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Orientation

TwinLight is an optical network digital twin: it loads a GNPy topology,
computes a per-lightpath QoT baseline (GNPy split-step propagation or a
closed-form GN/EGN kernel), layers four analytical models of time-varying
impairments on top, and serves the result over T-API v2.6.0 REST, the T-API
performance-measurement stream over gNMI (TR-548) and Prometheus — and, as a *fully transparent* optical domain controller, exposes
every element and parameter under `/transparent` (ns-3-style paths, RESTCONF-shaped
datastores), independently of T-API objects.

```
twinlight-ui (React SPA)          port 5173
      ▼ REST
FastAPI app (src/twinlight)       port 8080  ← owns ALL state
      ▼
PhysicalBackend Protocol → GnpyBackend | EgnBackend
      + NetworkX routing graph + transient layer
gNMI gRPC server                  port 50051 ← T-API measurement stream (TR-548)
```

Packages: `src/twinlight` (backend), `src/twinlight_client` (client library and
CLIs), `twinlight-ui/` (React SPA). Console scripts: `twinlight`,
`twinlight-client`, `twinlight-soft-failure`, `twinlight-transparent`,
`twinlight-fetch-examples`.

## Hard constraints

These are the ones that are expensive to discover by breaking them.

1. **T-API files are T-API only.** `api/common.py`, `api/topology.py`,
   `api/connectivity.py`, `api/path_computation.py`, `api/equipment.py`,
   `api/oam.py` and `api/streaming.py` must implement only T-API v2.6.0 — standard paths, standard hyphenated JSON
   keys. Anything proprietary (OPM, path metadata, admin, diagrams, runtime
   config, the transparent controller) goes in a separate module under
   `/internal/`, `/admin/`, `/config/` or `/transparent/`. Never add a non-standard field to a `/data/` response.

   "Standard" means *present in the v2.6.0 YANG*, not merely
   standard-looking. Check the module before adding a key: a plausible name
   under a `tapi-*` prefix that the spec does not define is worse than an
   obviously proprietary one, because a client cannot tell. The photonic
   layer in particular lives in **augments** on the SIP and the
   connectivity-service end-point, not in top-level resources.

2. **Preserve literature citations in comments.** Where a function implements a
   published formula, the comment naming the author, venue and equation is part
   of the project's scientific audit trail. Carry it through refactors. If a
   change makes the code diverge from its cited source, record that in
   [docs/PHYSICS.md § Known limitations](docs/PHYSICS.md#known-limitations).

3. **Python 3.12 exactly** — GNPy is not compatible with newer releases.

4. **`uv` for environment and dependency management**
   (`uv venv --python 3.12`, `uv pip install -e ".[dev]"`).

5. **GNPy comes from PyPI.** Prefer its public API over reimplementing physics.
   Documentation: <https://gnpy.readthedocs.io/>. The EGN backend has no runtime
   dependency on `optical-networking-gym` — the GN-model kernel is implemented
   directly in `physics/egn_kernel.py`, with attribution.

6. **GNPy reference topologies are not redistributed.** `examples/gnpy-data/*.json`
   is git-ignored; `twinlight-fetch-examples` provisions the files from the
   installed `gnpy` package, and the Docker build does the same. Never commit
   those JSON files.

7. **Generated protobuf code is committed but not edited.**
   `src/twinlight/streaming/proto/` is excluded from ruff, mypy and coverage.
   Regenerate from the `.proto` files — see
   [CONTRIBUTING.md](CONTRIBUTING.md#regenerating-the-grpcprotobuf-code).

8. **Determinism in the physics path.** Per-element randomness comes from
   `cascade.py:hash_phase()` (MD5 of `uid` + metric). Do not introduce an
   unseeded RNG — reproducibility is a feature, and a flaky physics test is
   nearly undiagnosable.

9. **Frontend:** Tailwind CSS **v3** (not v4); **npm** (not pnpm or bun);
   `vite.config.ts` and `vitest.config.ts` stay separate files.

10. **Do not amend existing commits** — always create new ones.

## Quality gates

All four must pass before a change is done; CI runs the same set plus a Docker
image build and smoke test.

```bash
ruff check .
mypy
pytest --cov                            # coverage floor: 60%
npm --prefix twinlight-ui test -- run
```

## GNPy API notes (3.0.x)

Details that are easy to get wrong and cost a debugging cycle:

- Network nodes **are** element objects, not `uid → element` dicts. Access the
  UID via `node.uid`.
- `designed_network(equipment, network)` is the correct post-design entry point,
  not the old `build_network(pref_ch_db=…)`.
- `network_from_json(topo_data: dict, equipment)` takes a parsed dict, not a
  `Path`.
- `Transceiver.__call__(si, role)` requires a `TransceiverRole`
  (`gnpy.core.parameters`): `EMITTER` for the path's first element, `RECEIVER`
  for its last. Without it every propagation raises `TypeError`, and
  `GnpyBackend` quietly falls back to mock OPM.
- `create_input_spectral_information(f_min, f_max, …)` treats both bounds as
  channel centres, inclusive. gnpy 2 put the first channel one slot above
  `f_min`, so the same arguments now launch one extra channel without any
  error. The twin launches the equipment library's SI comb (76 channels,
  191.35–195.10 THz, with the bundled library) with the service channel
  inserted at its own frequency, and reports that channel, not the worst
  (`physics/channel_plan.py:line_comb`). `TestPropagationComb` pins it.
- EDFAs drop carriers outside their band (`Edfa.params.bands`; 191.275–196.125
  THz for GNPy's reference EDFAs), and slot indices do not survive that, so
  the service channel is found by frequency after propagation. The twin keeps
  the comb and every assigned block inside the band
  (`find_elements_common_range`).
- ROADMs equalise each channel to their target plus its `delta_pdb`, so a
  per-channel launch-power offset must ride on `delta_pdb`; a transmitter
  power alone is erased at the first ROADM.
- `si.pmd` and `si.latency` are per-channel arrays, not scalars.
- Deep-copy path elements before propagating; propagation mutates element state.
- An amplifier's NF at a given gain is `Edfa._calc_nf(avg=True)` after setting
  `effective_gain` (and `pin_db`/`nch`/`slot_width` for the OpenROADM types);
  the EGN backend uses it on the designed elements so both backends share the
  equipment model. Designed EDFA objects report `operational.gain_target` /
  `delta_p` as None; `network_to_json` carries the values.
- GNPy has no k-shortest-path function. `compute_constrained_path` runs
  `networkx.shortest_simple_paths(network, weight="weight")`, where an edge's
  weight is the length (m) of the fiber it leaves and 0.01 otherwise
  (`gnpy/tools/json_io.py`, `gnpy/core/network.py`). The twin's k-list is that
  same search; its `TopologyGraph` fallback carries the same weights.
  `tests/test_state/test_candidate_ranking.py` pins candidate 1 to GNPy's route.
- With a `nodes_list`, `compute_constrained_path` filters *every* simple path
  through `ispart`, with no bound: a far-off include runs for minutes. The twin
  uses it only without includes; `algorithms/routing.py:waypoint_paths` (an
  exact branch and bound with a step budget, pinned to brute force and to a
  min-cost-flow optimum in `tests/test_algorithms/test_waypoint_search.py`)
  handles includes.
- With an all-`LOOSE` `loose_list` it cannot satisfy, `compute_constrained_path`
  drops the constraints and returns the unconstrained route. The twin passes
  `STRICT` for every include.

## Caches and invalidation

`TapiContext` holds `_gnpy_uid_map` (rebuilt only by a redesign), `_route_cache`
(`(src, dst) → uid list`), `_baseline_cache` (`service_uuid → OpmBaseline`)
and `_physical_context_cache` (the T-API equipment tree, keyed by the running
revision, so any write that bumps the revision rebuilds it on the next read).
`_propagation_lock` serialises propagation. A change that affects physics must
invalidate the right cache — `invalidate_baseline(uuid)` on service deletion,
and the broader sweep on `/config/` parameter changes and link failure.
Forgetting this produces stale OPM that looks plausible, which is the worst
possible failure mode for a twin. Anything that changes element parameters at
runtime — the `/config/parameters` batches and `/transparent` writes included —
must write through `apply_element_overrides` so it inherits that invalidation.
The same method bumps the running revision (the transparent controller's ETag)
and captures the startup value its reverts restore; twin-config changes go
through `apply_twin_overrides` for the same reasons.

The T-API datastore's entity-tags (`state/revision.py`, served by
`api/conditional.py`) follow the same discipline: every `TapiContext` method
that changes something a T-API body shows calls `_tapi_changed` —
`config=True` when a `config true` node of the YANG changed (services,
path-comp-services, restores) — and `_bump_revision` does it for element,
failure and twin-config changes. A mutation that skips it, or a route that
mutates a model directly, makes a `304` confirm a body that has changed.

Under a backend with `uses_line_load` (EGN), a baseline also depends on the
lightpaths sharing its fibers (`_sharing_services`): an admission, a removal
and a replacement re-evaluate them inside the request (`_reevaluate`), over a
line load built once for all of them. Each lightpath's channel in that load is
memoised in `_lit_channel_memo` and checked against its inputs on every use
(the allocation and service objects, which are replaced rather than mutated,
and the launch power and format); the EGN backend memoises each route's
amplifier bands (never written) and lit fibers (cleared on every cut). Keep
those properties if you add an input to a lightpath's channel. The reverse index
holds the backend's designed UIDs too (`_note_baseline`), since a route's
UIDs are the topology's as written. A service's `operational-state` is
derived from its baseline in `get_or_compute_baseline`; T-API reads call
`refresh_operational_states` so it reflects whatever was invalidated.

A new runtime-writable attribute needs a case in
`tests/test_physics/test_element_params_propagation.py` proving that
propagation reads it: a knob that is accepted but never read is worse than
none. The twin keeps no clock for parameter changes: gradual drift (soft
failures) belongs in the `twinlight-soft-failure` client, which only sends
concrete values.

## The T-API measurement stream

gNMI carries one stream, TR-548 §6 (docs/API.md § gNMI streaming; deviations
in docs/TAPI_COMPLIANCE.md § 2.5). Three rules keep it honest:

- **One OPM computation.** `streaming/measurement_stream.py` samples through
  `api/internal.py:sample_service_opm`, the function behind `GET /internal/opm`,
  reading the instant from `internal.wall_clock()`. Never compute OPM for the
  stream separately; a new OPM key needs a row in `streaming/records.py:MEASURES`
  (a test fails otherwise).
- **OAM jobs follow services.** `TapiContext._add_service` / `delete_service` /
  `restore_from` create, remove and restore each service's `tapi-oam` job
  (name-based UUID); `replace_service` detaches the job with the service and
  puts both back if re-admission fails. A new path that admits or drops
  services must go through them, or records lose their anchor.
- **The log is behind `RecordLog`.** The gNMI servicer only appends, replays
  and trims through that interface; keep it that way so a shared backend can
  replace the in-process log.

## Authentication

Opt-in, switched on by `TWINLIGHT_API_TOKENS` (environment or `.env`, never
YAML; see `twinlight/auth.py`). One `TokenAuthenticator` on `app.state` serves
both interfaces: `TokenAuthMiddleware` for HTTP, `TokenAuthInterceptor` for
gRPC.

- **Installed only when tokens exist.** `create_app` and `build_grpc_server`
  add the middleware and the interceptor only if the token set is non-empty,
  so an open twin runs no authentication code. Do not add per-request
  "is auth on?" checks; do not install either unconditionally.
- **Deny by default.** A new route is protected automatically. `/health` is
  the only public path (`app.py:PUBLIC_PATHS`); adding one needs a reason as
  good as the Compose healthcheck's.
- **No side doors.** Anything that serves the API on another interface or
  port must check the same `app.state.authenticator`. The one deliberate
  exception is the ONOS T-API adapter (`integrations/onos/adapter`): a
  separate client that presents the shared `TWINLIGHT_API_TOKEN` (the one
  the CLIs present) to the twin and serves ONOS without credentials on its
  published port, by the owner's decision and documented
  in `integrations/onos/README.md` § Authentication. Do not copy that pattern
  into `src/twinlight`.
- **Never log, echo or serialise a token.** `TwinConfig.api_tokens` is
  `exclude=True, repr=False` (so `/transparent` never shows it), and parse
  errors name entries by position.
- **Tests stay hermetic.** `conftest.py` clears the token variables and
  points `twinlight.auth.DEFAULT_ENV_FILE` away from the developer's own
  `.env`. A test that wants tokens sets `api_tokens` explicitly.

## SIP ↔ GNPy UID mapping

`TapiBuilder` stores each GNPy element's UID in the SIP's name list:

```python
sip.name = [NameAndValue(value_name="node-name", value=el.uid)]
```

recovered in `TapiContext.__init__` as `_sip_to_gnpy_uid`. This is how a T-API
endpoint reference resolves to a physical element.

## Not yet implemented

Soft-decision post-FEC BER (GMI/NGMI are reported, but not mapped through a
per-code NGMI threshold); multi-band (C-band only); the EGN correction on cross-channel NLI
(both backends are the plain GN model); the actual line load under the GNPy backend (it
assumes a full SI comb; EGN uses the lit lightpaths); an event-driven simulation clock;
T-API notification, fault and virtual-network modules, and OAM beyond the stream's
read-only jobs; T-API entity and alarm streams; gNMI Get/Set; TLS and per-token
authorization scopes (authentication is opt-in and all-or-nothing).
