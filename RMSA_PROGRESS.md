# RMSA decisions — progress tracker (temporary)

Branch `feature/rmsa-decisions`, worktree `../TwinLight-rmsa`, from `origin/main`
(d5e5fb4). One commit per item, each pushed to `origin` and `chalmers`. This file
is deleted in the last commit; it exists so another session can resume.

Environment: `env -u VIRTUAL_ENV uv pip install --python .venv/bin/python -e ".[dev]"`
(an inherited `VIRTUAL_ENV` points at the main checkout's venv — never install
into it). Baseline before any change: 752 tests passed.

## Decisions taken with the user (2026-11-14)

- **Item 1**: comply with T-API v2.6.0. Add the standard
  `path-computation-context/path-comp-service` list (POST/GET/DELETE) and
  `path-computation-context/path` (GET). Remove the non-standard
  `POST …/path-computation-service/compute-path` now; move it, with
  `max-candidates`, to `POST /internal/compute-path` and point the UI at it.
- **Item 5**: no `rmsa.default_launch_power_dbm` key. The default launch power
  comes from the equipment library's `SI` (`power_dbm`); the per-format
  `tx_power_dbm` in `physics/modulation.py` goes. A client's
  `otsi-config/channel-output-power` (decimal64, **0.01 dBm** per the YANG
  description) becomes a `delta_pdb` offset relative to SI `power_dbm` under
  GNPy (kept through ROADM equalisation); EGN launches the channel at P.
- **Item 7**: load comb = the equipment `SI` comb (f_min..f_max, spacing,
  baud, roll-off, power). SI channels whose slot overlaps the service's media
  channel are dropped and the service channel inserted at its frequency. EGN
  gains the GN-model incoherent cross-channel NLI (XCI); with no equipment
  file, EGN reads `SI` from gnpy's bundled `example-data/eqpt_config.json`.
  XCI only affects the EGN backend.

## Status

| # | Item | Status | Commit |
|---|------|--------|--------|
| 1 | Path-computation constraints + T-API path-comp-service | done | see git log |
| 2 | Modulation selection over k candidates | done | see git log |
| 3 | Candidate ranking (GNPy shortest_simple_paths) | done | see git log |
| 4 | Spectrum request / band restriction | done | see git log |
| 5 | Launch power | done | see git log |
| 6 | Startup log of effective RMSA settings | done | see git log |
| 7 | Channel-accurate QoT | done | see git log |
| — | Docs, quality gates, PR | next | |

## Notes for whoever resumes

- YANG v2.6.0 copies were fetched to the session scratchpad; refetch from
  `raw.githubusercontent.com/Open-Network-Models-and-Interfaces-ONMI/TAPI/v2.6.0/YANG/`.
- Constraint support: topology-constraint include/exclude node and link,
  explicit-route; routing-constraint route-objective-function
  MIN_WORK_ROUTE_LATENCY (fiber-length ranking is latency ranking) and
  is-exclusive=true. Everything else is refused by name.
- Mesh fixture `tests/fixtures/mesh_network.json` + `MeshRefs`, `mesh_context`
  (both backends), `mesh_gnpy_context`, `mesh_app` in `tests/conftest.py`.
- Pre-existing finding for the PR (not fixed): topology link
  `latency-characteristic/total-size` is not a v2.6.0 leaf.
- Item 2: `ConnectivityService.requested_modulation` (None = admission
  chooses) vs `modulation_format` (in effect). Without a physics backend an
  unspecified format resolves to DP-QPSK (no QoT to justify a denser one) —
  call this out in the PR. Admission memo is per call (`TapiContext._admit`).
- Item 4: `physics/channel_plan.py` (SI defaults + `common_band`), backend
  `spectral_defaults` / `amplifier_bands()`; EGN keeps designed EDFA bands.
  Without physics (mock) the whole grid is assignable. Requested-spectrum
  errors: `SpectrumRequestError` (422); band/occupancy conflicts 409.
- Item 5: `ModulationParams.tx_power_dbm` removed; `ctx.default_launch_power_dbm`
  = SI power_dbm; `_service_launch_power` (snapshot key `launch_power_dbm`);
  `compute_baseline(path, fmt, *, centre_frequency_hz, launch_power_dbm)`.
  GNPy still reports the worst comb channel until item 7. Transparent tree keeps
  `ModulationList/*/tx_power_dbm` (now the SI default) + new service leaf
  `launch_power_dbm`.
- Item 7: `channel_plan.line_comb` (SI comb, overlap removal, band filter,
  service inserted); GNPy reports the service channel by frequency; EGN has
  `per_span_cross_channel_nli_power` (Poggiolini 2014 Eq. 120/123, checked
  against gnpy `NliSolver._psi`). `compute_baseline` takes
  `media_channel_width_hz` too; admission memo key adds the width.
