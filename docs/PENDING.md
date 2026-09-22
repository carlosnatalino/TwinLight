# Pending work

Deliberate deferrals: things we have decided are wrong, decided how to fix,
and decided not to fix *yet*. Each says why it is not done, so nobody has to
re-derive the analysis before picking it up.

This is not a bug tracker or a wishlist. Approximations we accept and do not
intend to change belong in
[PHYSICS.md § Known limitations](PHYSICS.md#known-limitations); gaps against
the standard belong in [TAPI_COMPLIANCE.md](TAPI_COMPLIANCE.md).

---

## Retire the bare `/data/` mount

**Now:** the six T-API routers are mounted twice — under
`server.restconf_root` (default `/restconf`, per RFC 8040 §3.1) and at the
bare `/data/` they were served from before. The bare mount is hidden from
OpenAPI.

**Want:** `/restconf/data/` only.

**Why not yet:** everything in-tree points at the bare path — the React UI's
API client, `twinlight_client`, the gNMI adapter's ASGI proxy, the ONOS demo
scripts and ~14 test modules. Moving them is mechanical but touches every
layer at once, and any out-of-tree consumer breaks silently with a 404. Doing
it alongside the payload changes in this same branch would have made a
failure hard to attribute.

**How:** point every in-tree consumer at `{restconf_root}/data/`, drop the
unconditional bare `include_router` in `app.py:create_app`, and keep
`restconf_root: ""` as the escape hatch for anyone who needs the old surface.

---

## Move `frequency-slot` onto the connectivity-service end-point

**Now:** `api/connectivity.py:_connectivity_service_payload` injects a
top-level `frequency-slot` key into the connectivity-service.

**Want:** the standard location. `tapi-connectivity.yang` v2.6.0 has no
`frequency-slot` leaf; the photonic module expresses assigned spectrum as an
augment on the end-point's `layer-protocol-constraint`:

```
tapi-photonic-media:mcg-connectivity-service-end-point-spec
  mc-spectrum-config-pac
    spectrum { upper-frequency, lower-frequency }   # uint64 Hz
```

**Why not yet:** this is the same defect class as the `modulation-format` key
that moved to its augment in this branch, and it should have moved with it.
It was left because it is read in more places than modulation was —
`validate.sh` check 14, the adapter's admission log, `lightpath.sh`, the UI's
service detail panel — and bundling both into one change would have made the
ONOS validation ambiguous if it failed. The twin is therefore *inconsistent*
right now: modulation is standard-shaped and spectrum is not. That is a known
state, not an oversight.

**How:** mirror what `models/connectivity.py` does for modulation — a nested
model on `ConnectivityServiceEndPoint`, populated from
`TapiContext.get_service_spectrum`. The Hz arithmetic already exists in
`TapiContext._slot_range_hz`.

---

## Replace `/data/tapi-photonic-media:spectrum-context`

**Now:** that endpoint returns `num-slots`, `slot-width-ghz` and
`nominal-central-frequency-thz`.

**Want:** it gone from `/data/`.

**Why not yet:** the UI's Spectrum page reads it, and the replacement for most
consumers is the per-SIP `spectrum-capability-pac` added in this branch —
which nothing has migrated to yet.

**Why it matters:** none of those three leaves exist in T-API v2.6.0. They are
invented names published under the ONF module prefix, which is worse than an
obviously proprietary key: a client has no way to tell it is not standard.
This is the clearest remaining violation of CLAUDE.md constraint #1.

**How:** move the grid parameters to `/internal/spectrum-context` (they are
twin configuration, not T-API), point `SpectrumPage.tsx` and
`client.ts:getSpectrumContext` at it, and delete `api/photonic_media.py` once
the real photonic surface lives on the SIP.

---

## Return the target resource from a per-list-entry GET

**Now:** `GET .../service-interface-point={uuid}` returns the *parent*
container: `{"tapi-common:context": {"service-interface-point": [...]}}`.

**Want:** the target resource keyed by its own identifier, per RFC 8040 §4.3
and RFC 7951 §4: `{"tapi-common:service-interface-point": [ {...} ]}`.

**Why not yet:** it changes the response shape of every single-entry GET, and
the UI and client library both unwrap the current one. It is recorded as A2 in
[../integrations/onos/COMPATIBILITY.md](../integrations/onos/COMPATIBILITY.md)
— note that fixing it does **not** let the ONOS adapter drop its unwrapping,
because ONOS wants no wrapper at all, so this buys conformance for other
clients rather than a smaller adapter.

---

## Scale the EDFA gain excursion by fractional load

**Now:** the excursion at an add/drop is
`gain_per_channel_db × |Δchannels|`.

**Want:** scaling by the *fraction* of channels added or dropped, per Sun
1997 — one channel added to a full C-band should perturb far less than one
added to an empty span.

**Why not yet:** it does not affect the bundled scenarios, which run at low
occupancy, and the current form is bounded and citable. Recorded in
[PHYSICS.md § Known limitations](PHYSICS.md#known-limitations); listed here
because it is a known modelling shortcut rather than an accepted one.

---

## Remove or implement `gnpy.no_insert_edfas`

**Now:** the field is validated, settable from `--no-insert-edfas`, and
documented in [CONFIGURATION.md](CONFIGURATION.md) — and read by nothing.
`examples/coronet_conus_config.yaml` sets it `true`, and GNPy's
`designed_network()` inserts 1068 EDFAs anyway.

**Want:** either honour it or delete it. A documented knob that silently does
nothing is worse than no knob.

**Why not yet:** honouring it means deciding what a bare-fiber topology should
do without amplifiers, which is a physics question, not a plumbing one.
