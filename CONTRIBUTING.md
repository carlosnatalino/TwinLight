# Contributing to TwinLight

Thanks for your interest in TwinLight. Bug reports, physics corrections,
documentation fixes and new features are all welcome.

## Getting set up

TwinLight targets **Python 3.12** — GNPy is not yet compatible with newer
releases — and uses [`uv`](https://docs.astral.sh/uv/) for environment and
dependency management.

```bash
git clone https://github.com/carlosnatalino/TwinLight.git
cd TwinLight

uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"

twinlight-fetch-examples          # GNPy reference topologies for the examples
npm --prefix twinlight-ui ci      # web UI dependencies
```

`uv` is the supported path, but the project is a standard PEP 621 package, so
`pip install -e ".[dev]"` in a 3.12 virtualenv works too.

## Before opening a pull request

Run the same checks CI runs — all four must pass:

```bash
ruff check .                            # lint
mypy                                    # static type checking
pytest --cov                            # tests + coverage (floor: 60%)
npm --prefix twinlight-ui test -- run   # web UI tests
npm --prefix twinlight-ui run build     # web UI build
```

CI additionally builds both Docker images and smoke-tests the twin image, so a
change that breaks `docker compose up --build` will be caught there.

## Project conventions

These are not style preferences; each one prevents a specific class of bug.

### T-API surfaces stay standard

Files under `api/common.py`, `api/topology.py`, `api/connectivity.py`,
`api/path_computation.py`, `api/equipment.py` and `api/photonic_media.py` must
implement **only** T-API v2.6.0 — standard paths, standard hyphenated JSON keys,
RESTCONF error bodies.

Anything the standard does not cover (OPM readings, path metadata, admin
utilities, diagram synthesis) belongs in a separate module on a separate URL
prefix: `/internal/`, `/admin/`, `/config/`. Never mix a proprietary field into
a standard T-API response — a client that trusts the schema will break on it.

### Cite the literature in the code

Where a function implements a published formula, name the source next to it —
author, venue, year, and the equation number where one exists:

```python
# Hinge power transfer — Zarkosvky & Shtaif, Opt. Lett. 45(5):1224 (2020), Eq. 5.
a_j = (1.0 + gamma_j * cos_theta_j) / math.sqrt(1.0 - gamma_j**2)
```

Existing citation comments must be preserved through refactors. They are the
project's scientific audit trail, and the [References](README.md#references)
section of the README is generated from the same set of works. If you introduce
a new model, add its reference there as well and to
[docs/PHYSICS.md](docs/PHYSICS.md).

If your change makes the implementation diverge from its cited source (a
simplification, a scope limit), say so in
[docs/PHYSICS.md § Known limitations](docs/PHYSICS.md#known-limitations) rather
than leaving it implicit.

### Typing and models

- The codebase is fully type-hinted and `mypy` runs in CI with the Pydantic
  plugin. New code must type-check without `# type: ignore` unless there is a
  comment explaining why.
- T-API JSON keys are hyphenated (`"modulation-format"`, `"end-point"`).
  Pydantic models use `alias=` and serialise with `by_alias=True`.
- Configuration models use `extra="forbid"`, so a misspelled YAML key is a
  startup error rather than a silently ignored setting. Keep it that way.

### Determinism

Transient models derive per-element randomness from an MD5 hash of
`(uid, metric)` (`cascade.py:hash_phase`). Do not introduce an unseeded RNG into
the physics path — reproducibility is what makes a recorded experiment
re-runnable, and a flaky physics test is nearly impossible to diagnose.

### Frontend

- **Tailwind CSS v3** — do not upgrade to v4.
- **npm** — not pnpm, not bun. Commit `package-lock.json` changes.
- `vite.config.ts` and `vitest.config.ts` are separate files and must stay
  separate (the `vitest/config` and `vite` type exports conflict).
- Use `globalThis`, not `global`, in tests — the browser tsconfig has no Node
  types.

### Generated code

`src/twinlight/streaming/proto/` is generated from the `.proto` files. It is
committed (the package imports it directly; there is no codegen build step) but
excluded from ruff, mypy and coverage. Do not hand-edit it.

## Regenerating the gRPC/protobuf code

Only needed after editing `gnmi.proto` or `gnmi_ext.proto`. The compiler ships
with `grpcio-tools`, which is already a project dependency.

1. From the repository root, with the environment active:

   ```bash
   PROTO_DIR=src/twinlight/streaming/proto
   python -m grpc_tools.protoc \
     -I "$PROTO_DIR" \
     --python_out="$PROTO_DIR" \
     --grpc_python_out="$PROTO_DIR" \
     "$PROTO_DIR/gnmi.proto" "$PROTO_DIR/gnmi_ext.proto"
   ```

   The include path is the proto directory itself, so the files' bare
   `import "gnmi_ext.proto"` resolves.

2. Rewrite the cross-module imports. `protoc` emits bare imports
   (`import gnmi_pb2`) that fail once the modules are imported as part of the
   `twinlight` package:

   ```bash
   sed -i.bak -E \
     's/^import (gnmi[a-z_]*_pb2) as/from twinlight.streaming.proto import \1 as/' \
     "$PROTO_DIR/gnmi_pb2.py" "$PROTO_DIR/gnmi_pb2_grpc.py"
   rm -f "$PROTO_DIR"/*.bak
   ```

3. Verify:

   ```bash
   python -c "from twinlight.streaming.proto import gnmi_pb2, gnmi_pb2_grpc"
   pytest
   ```

Commit the regenerated `*_pb2.py` / `*_pb2_grpc.py` files together with the
`.proto` changes.

## Commits and pull requests

- Write commit messages that explain *why*, not just what. The physics and
  protocol constraints in this codebase are rarely self-evident from a diff.
- Keep a pull request to one logical change; a rename plus a behaviour change in
  one commit is hard to review and harder to revert.
- Reference the issue a change closes.
- Do not amend or force-push commits that already exist on `main`.

## Reporting bugs

Please include:

- What you ran — the config file (or the relevant excerpt) and the command.
- The topology: which scenario, or the shape of your own.
- Which physical-layer backend (`gnpy` or `egn`).
- Expected versus observed behaviour. For physics issues, the OPM values you saw
  and what you expected, ideally with the reasoning or the reference.

A snapshot from `POST /admin/snapshot` makes a QoT or RMSA report reproducible
and is the single most useful attachment.

## Questions and discussion

Open a GitHub issue. For questions about the underlying physical models, please
say which reference you are comparing against — it makes the discussion much
faster.

## Licence

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE) that covers the project.
