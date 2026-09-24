# Roadmap and known issues

Known issues in the current release and the changes planned to address them,
followed by features that are not implemented yet. Each issue says what you
will observe, how it affects you, and what to do about it in the meantime.

Two related documents cover what this one does not:

- [PHYSICS.md § Known limitations](PHYSICS.md#known-limitations) — modelling
  approximations that are accepted by design and not planned to change.
- [TAPI_COMPLIANCE.md](TAPI_COMPLIANCE.md) — the full assessment of where the
  twin does and does not conform to T-API v2.6.0.

Bug reports and feature requests are welcome as
[GitHub issues](https://github.com/carlosnatalino/TwinLight/issues).

---

## Known issues

### The bare `/data/` mount is deprecated

**Current behaviour:** every T-API resource is served twice — under the RESTCONF
root (`/restconf/data/…` by default, set by `server.restconf_root`, as RFC 8040
§3.1 specifies) and at the bare `/data/…` used by earlier releases. The bare
mount is hidden from the OpenAPI documentation.

**Planned:** serve T-API only under the RESTCONF root. Setting
`server.restconf_root: ""` will remain available for anyone who needs the old
paths.

**What to do now:** point new clients at `/restconf/data/…`, or discover the
root from `GET /.well-known/host-meta`. The bundled web UI, client library and
gNMI adapter will be migrated in the same release that removes the bare mount.

### A GET on a single list entry returns its parent container

**Current behaviour:** `GET …/service-interface-point={uuid}` (and the other
per-entry GETs) returns the parent container:

```json
{"tapi-common:context": {"service-interface-point": [ { … } ]}}
```

**Planned:** return the target resource keyed by its own module-qualified name,
as RFC 8040 §4.3 and RFC 7951 §4 specify:

```json
{"tapi-common:service-interface-point": [ { … } ]}
```

**What to do now:** unwrap `["tapi-common:context"]["service-interface-point"][0]`.
The fix changes the response shape of every single-entry GET, so clients that
unwrap the current form will need a one-line change when it lands.

### `gnpy.no_insert_edfas` has no effect

**Current behaviour:** the option is accepted in the YAML file and by
`--no-insert-edfas`, but nothing reads it. GNPy's `designed_network()` inserts
amplifiers regardless — 1068 of them on the CORONET CONUS scenario, which sets
the option to `true`.

**Planned:** either honour the option or remove it. Honouring it requires
deciding how a topology of bare, unamplified fiber spans should be propagated,
which is a modelling question rather than a configuration one.

**What to do now:** nothing — the option is safe to leave in place. Results are
those of a GNPy-designed, fully amplified network whatever its value.

### EDFA gain excursion does not scale with channel load

**Current behaviour:** the transient gain excursion at an add/drop event is
`gain_per_channel_db × |Δchannels|`, independent of how many channels the
amplifier already carries.

**Planned:** scale the excursion by the *fraction* of channels added or dropped,
following Sun *et al.* 1997 [[2]](../README.md#ref-2): one channel added to a
full C-band should perturb far less than one added to an empty span.

**What to do now:** the bundled scenarios run at low occupancy, where the two
forms agree closely. Do not rely on excursion magnitudes on heavily loaded
spans. See [PHYSICS.md § 2.1](PHYSICS.md#21-edfa-gain-reservoir).

---

## Not yet implemented

| Area | Status |
|------|--------|
| Post-FEC BER | Only pre-FEC BER and Q-factor are reported |
| Multi-band (C+L and beyond) | C-band only; the spectrum grid is parameterised but the physics is not |
| Cross-channel NLI in the EGN kernel | Self-channel NLI only; use the GNPy backend when spectral loading matters |
| Eye and constellation rendering in the web UI | The backend synthesises the data (`/internal/services/{uuid}/eye-diagram`, `/constellation`); the UI components are placeholders |
| Event-driven simulation clock | The transient layer is evaluated at wall-clock time |
| T-API notification, OAM, fault and virtual-network modules | Not implemented; see [TAPI_COMPLIANCE.md](TAPI_COMPLIANCE.md) |
| gNMI `Get` and `Set` | Return `UNIMPLEMENTED`; `Subscribe` is supported |
| Authentication and TLS | None on any interface; see [SECURITY.md](../SECURITY.md) |
