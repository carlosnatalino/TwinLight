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
| Soft-decision post-FEC BER | Post-FEC BER comes from a threshold model and a hard-decision block-code model; GMI and NGMI are reported but not mapped to a post-FEC BER, which needs per-code NGMI data |
| Multi-band (C+L and beyond) | C-band only; the spectrum grid is parameterised but the physics is not |
| EGN correction on cross-channel NLI; actual line load under GNPy | Both backends compute the plain incoherent GN model (Poggiolini 2014), and agree within 0.05–0.25 dB at full load; neither applies the EGN correction. The GNPy backend evaluates every channel on the fully loaded SI comb; only the EGN backend follows the lightpaths actually lit |
| Event-driven simulation clock | The transient layer is evaluated at wall-clock time |
| Soft failures in the web UI | Driven by the `twinlight-soft-failure` client only |
| T-API notification, fault and virtual-network modules; OAM beyond read-only jobs | Not implemented; see [TAPI_COMPLIANCE.md](TAPI_COMPLIANCE.md) |
| TR-548 entity and alarm streams | Only the performance-measurement stream is offered; entity streams need a compacted log, and the twin has no alarms yet |
| Persistent, shared measurement log | The log is in-process and starts empty on restart (so the stream re-enters `ALIGNING`); it sits behind the `RecordLog` interface so a Valkey Streams backend can be added without touching the gNMI code |
| gNMI `Get` and `Set` | Return `UNIMPLEMENTED`; `Subscribe` serves the TR-548 measurement stream. The transparent controller's tree (`/transparent`) is the natural model for them to serve |
| Transparent controller: YANG module, locking, confirmed commit | The tree is described by a JSON schema (`/transparent/schema`), not YANG; the candidate is shared without a lock (use `If-Match`); there is no confirmed-commit rollback timer, since the twin keeps no clock for parameter changes |
| Transparent controller in the web UI | Driven through REST, `twinlight-transparent` or `TransparentClient` only |
| TLS and authorization | No TLS anywhere. Authentication is opt-in (`TWINLIGHT_API_TOKENS`) and all-or-nothing: any accepted token may use every REST path and gNMI; there are no per-token scopes. See [SECURITY.md](../SECURITY.md) |
