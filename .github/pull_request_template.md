<!--
Thanks for contributing to TwinLight. Keep a PR to one logical change where you
can — it is easier to review and much easier to revert.
-->

## What this changes

<!-- A short description, and *why* — the physics and protocol constraints in
this codebase are rarely self-evident from a diff. -->

Closes #

## Checklist

- [ ] `ruff check .` passes
- [ ] `mypy` passes
- [ ] `pytest --cov` passes (coverage floor: 60%)
- [ ] Web UI checks pass, if the UI changed
      (`npm --prefix twinlight-ui test -- run && npm --prefix twinlight-ui run build`)
- [ ] Tests added or updated for the behaviour this changes

## If this touches a T-API endpoint

- [ ] The change stays within T-API v2.6.0 — standard paths and JSON keys
- [ ] Nothing proprietary was added to a `/data/` response
      (non-standard surfaces belong under `/internal/`, `/admin/` or `/config/`)
- [ ] [docs/TAPI_COMPLIANCE.md](../docs/TAPI_COMPLIANCE.md) updated if the
      compliance picture changed

## If this touches a physical model

- [ ] The source is cited in the code, with the equation number where one exists
- [ ] Existing citation comments were preserved
- [ ] [docs/PHYSICS.md](../docs/PHYSICS.md) updated — including
      **Known limitations** if the implementation diverges from its source
- [ ] The reference is listed in [README.md § References](../README.md#references)
- [ ] No unseeded randomness was introduced into the physics path
