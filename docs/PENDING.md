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
