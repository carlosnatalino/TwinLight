# Plan: a twin with no gnpy dependency

Status: **proposed, not started** (2026-11-13). Branch: `feat/gnpy-api-backend`.

Goal: the twin itself never imports gnpy. GNPy runs only in the gnpy-api
service, and `GnpyApiBackend` is the only way to reach it. This plan records
where the branch stands after rebasing onto `main`, what is broken, what the
benchmark showed, and the steps to finish the job.

## 1. Where the branch stands

- Rebased onto `main` at `d5e5fb4` (#9). "Cap gnpy below 3.0" was dropped, since
  `main` moved to gnpy 3 in #6. gnpy stays the optional `gnpy` extra, pinned to
  `>=3.0.1,<4` like `main`.
- After the rebase, three things from `main` (#8, #9) clashed with the branch.
  The commit that adds this file fixes them:
  1. `api/parameters.py` imported `ParamValidationError` from
     `element_params`, which imports gnpy at module level. The twin image has no
     gnpy, so the twin crash-looped. It now imports from `physics/backend.py`.
  2. The `PhysicalBackend` Protocol gained `capture_attribute` /
     `restore_attribute`, which `GnpyApiBackend` lacked (a mypy error, and an
     `AttributeError` on parameter rollback, revert and QoT what-if). Both are
     now implemented, with a test. The token is the user-unit value. **Known
     gap:** gnpy-api writes numbers only, so an attribute GNPy loaded as unset
     cannot be put back; that raises `ParamValidationError`.
  3. The transparent tree's `type_variety` leaf read `None` under the API
     backend. `ElementStub` now carries the service's `type-variety`.
- Local gates (gnpy 3.0.1, gnpy-api not installed): ruff, mypy, 752 passed,
  1 skipped, coverage 84.3 %, UI 40/40.

### Open blocker: CI is red on this branch

CI installs `.[dev]` (gnpy 3.0.1) and then gnpy-api, which pins
`gnpy==2.14.1` and downgrades it. The in-process code from #6 needs gnpy 3
(`TransceiverRole`), so `GnpyBackend` falls back to mock OPM. Result in a
CI-equivalent environment: **40 failed, 4 errors**. Changing CI cannot fix this:
the gnpy-api service only runs on gnpy 2.14 (`propagate` calls a `Transceiver`
without `role`), and the parity tests need both in one process.

Also version-coupled: `GnpyApiBackend.compute_baseline` sends comb bounds in
the gnpy 2 convention (first channel one slot above `f-min`). That is correct
for a 2.14 service, but launches 89 channels once the service is on gnpy 3.
There is a comment about it at the call site.

## 2. Benchmark: `main` (GNPy in-process) vs this branch (gnpy-api)

`benchmarks/compose_benchmark.py`, CORONET CONUS, Apple M3 Max, Docker Desktop,
two rounds A-B-A-B, mean of the round medians. Twin image on `main` is
linux/amd64 under Rosetta with gnpy 3.0.1. On the branch the twin is native arm64,
and gnpy-api is linux/amd64 with gnpy 2.14.1. Both admitted the same 31 of 40
services over the same routes.

| | main | gnpy-api | ratio |
|---|---:|---:|---:|
| cold start until physics loaded | 5.03 s | 5.39 s | 1.07 |
| path estimate, first call | 36.2 ms | 47.1 ms | 1.30 |
| path estimate, route cached | 34.7 ms | 42.6 ms | 1.23 |
| service admission | 35.3 ms | 42.8 ms | 1.21 |
| OPM recompute after a write | 28.7 ms | 33.1 ms | 1.15 |
| redesign | 407 ms | 483 ms | 1.19 |
| throughput, 1 client | 23.4 req/s | 21.5 req/s | 0.92 |
| throughput, 8 clients | 25.4 req/s | 20.9 req/s | 0.82 |
| parameter write | 2.4 ms | 3.9 ms | 1.63 |
| fiber fail / restore | 1.7 ms | 3.4 ms | 1.95 |
| OPM read, one / all services | 1.6 / 4.4 ms | 1.0 / 2.8 ms | 0.65 |
| `/config/get`, all devices | 18.1 ms | 3.5 ms | 0.19 |
| Prometheus scrape | 114 ms | 32 ms | 0.28 |
| `/health` while propagating | 109 ms | 2.2 ms | 0.02 |
| peak memory | ~227 MiB | 144 + 152 MiB | ~1.3 |
| images | 491 MB | 475 + 798 MB | 2.6 |

On 2026-10-21 (both sides on gnpy 2.14.1) 8-client throughput was at parity
(0.99). The gap widened because `main` moved to gnpy 3 while the service did
not. One propagation on the same 30 routes, inside the containers, no HTTP:
**29.3 ms with gnpy 3.0.1 vs 33.3–36.1 ms with gnpy 2.14.1.** HTTP and the
service split cost a few milliseconds on top of that.

Separately, on `main` itself the Prometheus scrape (58.6 → 114 ms) and
`/config/get` (11.0 → 18.1 ms) are slower than on 2026-10-21, both with GNPy
in-process. This is probably #7–#9, and is worth a look on its own.

Reproducing: build each stack under its own compose project with an override
file that sets a distinct twin `image`, distinct `container_name`s (the
defaults collide with any other TwinLight stack on the host) and a scratch
snapshot volume. `run` **clears `--snapshot-dir`**, so never point it at the
working tree's `./snapshots`.

## 3. What still touches gnpy in the twin

Only five modules import gnpy (`grep -rn -E '^\s*(from|import) gnpy' src`):

| Module | Use | Change |
|---|---|---|
| `physics/gnpy_backend.py` | in-process backend | delete; `GnpyApiBackend` covers every call |
| `physics/element_params.py` | attribute allow-list over gnpy objects | delete; gnpy-api has its own (`element_attributes.py`) |
| `physics/gnpy_adapter.py` | in-process propagation | delete the propagation; move `OpmBaseline` and the comb constants (no gnpy) to e.g. `physics/baseline.py`. Seven modules import `OpmBaseline` from here today |
| `physics/egn_backend.py` | in-process amplifier design fallback | design through the service only (that path already exists), or run EGN undesigned |
| `example_data.py` | copies example files from the installed gnpy package | `--from-url` only; gnpy-api serves them at `/example-data` |

The twin already parses GNPy topology JSON itself (`loader/gnpy_topology.py`,
`loader/tapi_builder.py`).

## 4. Steps

### Step 1: gnpy-api on gnpy 3 (oopt-gnpy-api, `feat/twinlight-network-api`)

- [ ] Pin `gnpy>=3.0.1,<4`. In `propagate`, call each `Transceiver` with its
      `TransceiverRole` (first emits, last receives), as
      `gnpy.topology.request.propagate` does.
- [ ] Make the spectrum request version-neutral: take first and last channel
      **centres** plus spacing (or a channel count), so a gnpy release that
      changes what `f_min` means cannot silently change the twin's results.
- [ ] Support clearing an attribute or resetting it to its designed value (e.g.
      PATCH with `null`, or a reset endpoint). This closes the
      `restore_attribute` gap above.
- [ ] In the twin: send the new spectrum form, set the Dockerfile `GNPY_VERSION`
      to the service's gnpy, and rerun the benchmark. Propagation-heavy ratios
      should move back toward parity.

This alone makes CI green again (both sides on gnpy 3), even before step 2.

### Step 2: drop in-process GNPy from the twin

- [ ] Move `OpmBaseline` and the comb constants to a gnpy-free module; update the
      seven importers.
- [ ] Delete `gnpy_backend.py`, `element_params.py` and the propagation part of
      `gnpy_adapter.py`. `build_backend`: `physics.backend == "gnpy"` without
      `gnpy_api_url` becomes a clear configuration error.
- [ ] EGN: amplifier design through the service only.
- [ ] `example_data.py`: `--from-url` only. Docker build: stop unpacking the gnpy
      wheel. Fetch the example data from the service at startup, or share it
      through a volume.
- [ ] `pyproject.toml`: remove the `gnpy` extra and gnpy from `dev`. Install
      gnpy-api in the test environment instead; it brings its own gnpy, which
      twin code never imports.
- [ ] Optional: a dev convenience that starts a local gnpy-api, since every run
      now needs the service.

### Step 3: tests

- [ ] Point the `tests/conftest.py` fixtures `gnpy_twin_config`, `gnpy_context`
      and `gnpy_app` at an in-process gnpy-api (`TestClient` + patched
      `new_http_client`, as in `test_gnpy_api_backend.py`). About 300 tests go
      through these fixtures and should run unchanged.
- [ ] Move `test_element_params*.py` and `test_gnpy_adapter.py` (~30 tests) to
      gnpy-api, or delete them.
- [ ] Replace the in-process/remote parity tests with golden values.
- [ ] Fix the ~8 test files that name `GnpyBackend` directly (one or two lines each).
- [ ] Keep `test_the_twin_runs_without_gnpy_installed`. It is the test that caught
      the post-rebase crash.

### Step 4: docs

- [ ] CLAUDE.md hard constraint 5, CONTRIBUTING.md, README, ARCHITECTURE.md,
      CONFIGURATION.md: GNPy only through gnpy-api.
- [ ] PHYSICS.md § Known limitations: any remaining gnpy-api vs in-process
      difference (redesign recomputes span losses).
- [ ] `benchmarks/README.md`: replace the reference table.

## 5. Trade-offs accepted

- No in-process fast path: about +8 ms per propagation today, less once the
  service is on gnpy 3.
- Every deployment, test run and local dev session needs the service.
- Restoring unset attributes depends on step 1.

## 6. Decisions pending

- Do step 2 on this branch, or merge this branch first (after step 1) and remove
  the in-process path in a follow-up? Leaning toward follow-up: step 1 alone gets
  CI green and keeps this branch reviewable.
- Spectrum API shape for gnpy-api (centres + spacing vs centre + count).
