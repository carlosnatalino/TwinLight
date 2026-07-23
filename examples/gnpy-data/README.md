# GNPy reference data (not redistributed)

The example scenarios in `examples/` reference network topologies and an
equipment library that belong to the [oopt-gnpy][gnpy] project. TwinLight does
**not** redistribute those files — this directory is empty in a fresh clone and
its `*.json` contents are git-ignored.

Populate it from the `gnpy` distribution that TwinLight already installs as a
runtime dependency:

```bash
twinlight-fetch-examples          # copies into examples/gnpy-data/
twinlight-fetch-examples --list   # show which files are needed, and why
twinlight-fetch-examples --force  # refresh existing copies after a gnpy upgrade
```

Copying from the installed package (rather than a pinned vendored copy) keeps
the topology schema aligned with the GNPy version actually in use.

| File | Used by | Contents |
|------|---------|----------|
| `CORONET_CONUS_Topology.json` | `examples/coronet_conus_config.yaml` | CORONET CONUS backbone — 75 ROADMs, 198 fiber spans |
| `edfa_example_network.json` | `examples/twin_config.yaml` | Two-site example span with inline EDFAs |
| `eqpt_config.json` | both scenarios | Equipment library — EDFA models, transceivers, fiber types |

The Docker image provisions these files during the build, so `docker compose up`
needs no manual step.

## Manual alternative

If you prefer to fetch them yourself, they live under
[`gnpy/example-data/`][example-data] in the oopt-gnpy repository. Match the tag
to the `gnpy` version resolved by `pyproject.toml`.

[gnpy]: https://github.com/Telecominfraproject/oopt-gnpy
[example-data]: https://github.com/Telecominfraproject/oopt-gnpy/tree/master/gnpy/example-data
