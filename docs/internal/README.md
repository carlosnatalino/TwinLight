# Internal design documents (historical)

These are the original planning documents written while TwinLight was being
built. They are kept for provenance — they record *why* the architecture looks
the way it does, and which alternatives were considered and rejected — but they
are **not maintained** and will drift from the code.

For current, maintained documentation see:

| Instead of | Read |
|------------|------|
| `IMPLEMENTATION_PLAN.md` | [../ARCHITECTURE.md](../ARCHITECTURE.md), [../API.md](../API.md), [../PHYSICS.md](../PHYSICS.md) |
| `WEBUI_IMPLEMENTATION_PLAN.md` | [../../twinlight-ui/README.md](../../twinlight-ui/README.md) |

Two conventions in these documents no longer match the repository:

- The package was named `tapi_twin` (and the UI directory `tapi-twin-ui`) before
  the project was published as **TwinLight**; the packages are now `twinlight`
  and `twinlight_client`, and the UI lives in `twinlight-ui/`.
- They reference a `related-projects/` directory of read-only upstream checkouts
  (oopt-gnpy, the T-API YANG/protobuf models, optical-networking-gym) that was
  never part of the repository. Those are external projects; see the
  [References](../../README.md#references) section of the main README.
