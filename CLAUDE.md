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
| Is this known-wrong already, and why is it not fixed? | [docs/PENDING.md](docs/PENDING.md) |
| How do I set up, test and submit a change? | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Orientation

TwinLight is an optical network digital twin: it loads a GNPy topology,
computes a per-lightpath QoT baseline (GNPy split-step propagation or a
closed-form GN/EGN kernel), layers four analytical models of time-varying
impairments on top, and serves the result over T-API v2.6.0 REST, gNMI streaming
and Prometheus.

```
twinlight-ui (React SPA)          port 5173
      ▼ REST
FastAPI app (src/twinlight)       port 8080  ← owns ALL state
      ▼
PhysicalBackend Protocol → GnpyBackend | EgnBackend
      + NetworkX routing graph + transient layer
gNMI gRPC server                  port 50051 ← stateless adapter
```

Packages: `src/twinlight` (backend), `src/twinlight_client` (client library and
CLI), `twinlight-ui/` (React SPA). Console scripts: `twinlight`,
`twinlight-client`, `twinlight-fetch-examples`.

## Hard constraints

These are the ones that are expensive to discover by breaking them.

1. **T-API files are T-API only.** `api/common.py`, `api/topology.py`,
   `api/connectivity.py`, `api/path_computation.py` and `api/equipment.py`
   must implement only T-API v2.6.0 — standard paths, standard hyphenated JSON
   keys. Anything proprietary (OPM, path metadata, admin, diagrams, runtime
   config) goes in a separate module under `/internal/`, `/admin/` or
   `/config/`. Never add a non-standard field to a `/data/` response.

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

## GNPy API notes (2.14.x)

Details that are easy to get wrong and cost a debugging cycle:

- Network nodes **are** element objects, not `uid → element` dicts. Access the
  UID via `node.uid`.
- `designed_network(equipment, network)` is the correct post-design entry point,
  not the old `build_network(pref_ch_db=…)`.
- `network_from_json(topo_data: dict, equipment)` takes a parsed dict, not a
  `Path`.
- EDFA propagation needs ≥ 2 channels — `interpol_params` derives slot width
  from `channel_freq[1] - channel_freq[0]`. The twin uses an 88-channel C-band
  comb.
- `si.pmd` and `si.latency` are per-channel arrays, not scalars.
- Deep-copy path elements before propagating; propagation mutates element state.

## Caches and invalidation

`TapiContext` holds `_gnpy_uid_map` (built once), `_route_cache`
(`(src, dst) → uid list`) and `_baseline_cache` (`service_uuid → OpmBaseline`).
`_propagation_lock` serialises propagation. A change that affects physics must
invalidate the right cache — `invalidate_baseline(uuid)` on service deletion,
and the broader sweep on `/config/` parameter changes and link failure.
Forgetting this produces stale OPM that looks plausible, which is the worst
possible failure mode for a twin.

## SIP ↔ GNPy UID mapping

`TapiBuilder` stores each GNPy element's UID in the SIP's name list:

```python
sip.name = [NameAndValue(value_name="node-name", value=el.uid)]
```

recovered in `TapiContext.__init__` as `_sip_to_gnpy_uid`. This is how a T-API
endpoint reference resolves to a physical element.

## Not yet implemented

Post-FEC BER; multi-band (C-band only); XPM/FWM in the EGN kernel (self-channel
NLI only); eye and constellation *rendering* in the UI (the backend synthesis
exists); an event-driven simulation clock; T-API notification, OAM, fault and
virtual-network modules; gNMI Get/Set; authentication on any interface.
