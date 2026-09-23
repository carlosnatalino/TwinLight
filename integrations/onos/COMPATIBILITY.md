# Where each incompatibility belongs: twin, or adapter?

Every mismatch found while making ONOS drive TwinLight, classified by where the
fix belongs. **ONOS is treated as immutable** — it is upstream software we do
not control — so the only question for each item is whether TwinLight is wrong
(fix the twin) or ONOS is being non-standard (absorb it in the adapter).

The distinction matters beyond tidiness: anything in column "twin" is a real
T-API compliance improvement that benefits every client, and belongs in
`docs/TAPI_COMPLIANCE.md`. Anything in column "adapter" is ONOS-specific glue
that must **never** migrate into `src/twinlight` — that is exactly what
[CLAUDE.md constraint #1](../../CLAUDE.md) protects.

**What decides it is a document, not a preference.** Every verdict below names
the clause it rests on: the [T-API v2.6.0 YANG][yang], [RFC 8040][rfc8040]
(RESTCONF), or [RFC 7951][rfc7951] (JSON encoding of YANG). Two of the original
verdicts were overturned by reading those sources rather than trusting the
summary, and they are marked.

| # | Issue | Verdict |
|---|-------|---------|
| A1 | Port number parsed from the SIP UUID tail | Adapter, permanently |
| A2 | Per-SIP GET must be unwrapped | Adapter — **and** a separate twin bug |
| A3 | ONOS deletes pre-existing connectivity services | Adapter, permanently |
| A4 | ONOS cannot express a modulation format | Adapter policy |
| A5 | ONOS reads the T-API **2.1** photonic SIP shape | Adapter, permanently |
| A6 | ONOS mis-parses `G_6_25GHZ` and divides by zero | Adapter, permanently |
| B1 | SIPs carried no spectrum capability | **Twin gap — fixed**; verdict revised |
| B2 | No `/restconf` RESTCONF root | **Twin gap — fixed** |
| B3 | POST rejected a single-entry JSON array | **Twin defect — fixed**; verdict reversed |
| B4 | `modulation-format` was a bare non-standard key | **Twin defect — fixed** |
| B5 | `frequency-slot` is a bare non-standard key | **Twin defect — recorded, deferred** |
| B6 | `tapi-photonic-media:spectrum-context` is invented | **Twin defect — recorded, deferred** |
| C5 | netcfg pushed before the `ols` driver is bound | Our tooling — fixed |
| C6 | `gnpy.no_insert_edfas` is read by nothing | Our tooling — recorded, deferred |
| C8 | `validate.sh` watched a counter that saturates at 10 | Our tooling — fixed |
| D1 | Admission GSNR vs reported OPM diverged by ~9 dB | **Twin defect — fixed**; verdict reversed |
| D2 | Accumulated CD stored in the wrong unit | **Twin defect — fixed** |

---

## A. ONOS deviations — absorb in the adapter

These are places where ONOS does something the standard does not ask for.
Changing TwinLight to match would make the twin *less* correct.

### A1. Port number parsed from the SIP UUID tail

```java
String[] uuidSeg = uuid.split("-");
PortNumber portNumber = PortNumber.portNumber(uuidSeg[uuidSeg.length - 1]);
```

`PortNumber.portNumber(String)` is `UnsignedLongs.decode()`. ONOS is assuming
the last group of a UUID is a decimal port index — an assumption that holds for
the ADVA OLS the driver was written against and for nothing else.

T-API's `uuid` is an RFC 4122 UUID, and TwinLight's `uuid5(NAMESPACE_DNS, ...)`
is both correct and required by [CLAUDE.md constraint #8](../../CLAUDE.md)
(determinism). Its last group is hex, so `decode()` throws.

**Verdict: adapter, permanently.** Renumbering SIPs in the twin to please one
client would break RFC 4122 conformance, the UI, the client library and the
gNMI paths. The adapter republishes each SIP as `<real-uuid>-<index>` and strips
the suffix on the way back, so the real UUID still reaches ONOS as a port
annotation.

### A2. Per-SIP GET must be unwrapped

ONOS reads the photonic spec from the **top level** of the response to
`.../service-interface-point={uuid}`. TwinLight returns the *parent* container:

```json
{"tapi-common:context": {"service-interface-point": [ { ... } ]}}
```

Neither is what RFC 8040 §4.3 specifies. For a GET on a list-entry data
resource the body should be the target resource keyed by its own identifier,
module-qualified per RFC 7951 §4:

```json
{"tapi-common:service-interface-point": [ { ... } ]}
```

So **both sides are off-spec here, in different directions.** ONOS wants no
wrapper at all; TwinLight uses the wrong wrapper.

**Verdict: adapter is required either way** — fixing the twin would still not
satisfy ONOS. But this is *also* a genuine, independent TwinLight bug worth
fixing on its own merits, because it affects every standards-conformant client,
not just ONOS. It is the one item on this list that is simultaneously both
columns. Deferred, and recorded in [docs/PENDING.md](../../docs/PENDING.md):
unlike B1 and B3, fixing it buys conformance for other clients rather than a
smaller adapter, so it was not worth bundling into the same change.

### A3. ONOS deletes pre-existing connectivity services

`TapiDeviceHelper.removeInitalConnectivityServices()` deletes every connectivity
service it can see whenever ONOS's flow cache for the device is empty — on first
connect, and again after any ONOS restart. TwinLight is behaving correctly by
listing its own lightpaths; ONOS is claiming ownership of a domain it does not
exclusively own.

**Verdict: adapter, permanently.** The adapter shows ONOS only the services ONOS
created (`ADAPTER_EXPOSE_TWIN_SERVICES=false`). Suppressing this in the twin
would mean the twin lying about its own state to all clients.

### A4. ONOS cannot express a modulation format

T-API 2.1's connectivity-service has no modulation field, so ONOS cannot send
one. TwinLight defaults to DP-QPSK when it is absent, so nothing breaks; the
adapter sets it explicitly so the demo can switch formats.

**Verdict: adapter policy.** Not a defect on either side. Note that even T-API
2.6 would not help ONOS here: its modulation lives on the connectivity-service
end-point (B4), which the 2.1 driver has no notion of.

The related *twin* defect — TwinLight's own non-standard `modulation-format`
key — was split out as **B4** and fixed.

### A5. ONOS reads the T-API 2.1 photonic SIP shape

The ODTN driver dereferences
`tapi-photonic-media:media-channel-service-interface-point-spec` → `mc-pool` →
`available-spectrum` unconditionally. **Neither `mc-pool` nor
`media-channel-service-interface-point-spec` exists in T-API v2.6.0** — grep
`tapi-photonic-media.yang`. They are 2.1 constructs. Four differences in one
block, all the same cause:

| ONOS (T-API 2.1) | TwinLight (T-API 2.6.0) |
|---|---|
| `media-channel-service-interface-point-spec` → `mc-pool` | `photonic-media-service-interface-point-spec` → `spectrum-capability-pac` |
| frequencies in **MHz** | **uint64 Hz** (`grouping spectrum-band`) |
| bare `DWDM`, `G_50GHZ` | identityrefs `GRID_TYPE_DWDM`, `ADJUSTMENT_GRANULARITY_G_6_25GHZ` |
| `supported-layer-protocol-qualifier` | `supported-cep-layer-protocol-qualifier-instances` |

**Verdict: adapter, permanently.** This process is the 2.6 → 2.1 version
adapter; translating between two published versions of a spec is precisely its
job, and no amount of twin-side work removes it. `_mc_pool()` does the
reshaping and the unit and token conversions.

### A6. ONOS mis-parses `G_6_25GHZ` and divides by zero

`TapiDeviceHelper.getChannelSpacing()` has trailing spaces in its `"G_100GHZ "`,
`"G_12_5GHZ "` and `"G_6_25GHZ "` case labels (an upstream typo), so those fall
through to `CHL_0GHZ` and then divide by zero in `getOchSignal()`. Only
`G_50GHZ` and `G_25GHZ` are safe.

TwinLight's grid is genuinely 6.25 GHz flexi-grid, and now advertises itself
that way: `GRID_TYPE_FLEX` at `ADJUSTMENT_GRANULARITY_G_6_25GHZ`.

**Verdict: adapter, permanently.** The adapter forces what ONOS is shown to
`ADAPTER_GRID_GRANULARITY` (default `G_50GHZ`). No standard asks a 6.25 GHz port
to claim 50 GHz, and a twin that did would mislead every other client into
computing channel centres that do not line up with its slots.

---

## B. Genuine TwinLight gaps

Standard T-API / RESTCONF behaviour TwinLight did not implement. Fixing these
is a real compliance improvement for every client, and in two cases it let
adapter code be deleted.

### B1. SIPs carried no spectrum capability — fixed, verdict revised

> **Revised.** This row previously read *"`mc-pool` … is standard T-API, not an
> ONOS invention — ONOS is entitled to expect it."* The second half is wrong.
> `mc-pool` is standard T-API **2.1**; it does not appear anywhere in the 2.6.0
> YANG. ONOS is entitled to expect it *from a 2.1 server*, and TwinLight is a
> 2.6 server. Publishing an `mc-pool` from the twin would itself have violated
> constraint #1.

The gap was real but differently shaped: the **2.6** augment was missing.

```
service-interface-point
  tapi-photonic-media:photonic-media-service-interface-point-spec
    spectrum-capability-pac
      supportable-spectrum / available-spectrum / occupied-spectrum
```

each a list of `spectrum-band` keyed on `upper-frequency lower-frequency`, in
uint64 Hz. The twin now publishes it on all three SIP resources, built per
request from `SpectrumState` so it cannot go stale or leak into snapshots.

**What "available spectrum at a SIP" means** was the open modelling question.
A SIP sits on a transceiver, so the answer is **SIP-local**: `occupied-spectrum`
is the blocks used by services terminating on *this* SIP, and
`available-spectrum` is the rest of the band. That is the only reading that
makes the answer a property of the SIP, which is what the YANG models —
`spectrum-capability-pac` is a *port* attribute, and folding in the occupancy of
links downstream would attribute link state to a port, with the choice of link
depending on where the lightpath is going.

**The fiction is gone.** What the adapter synthesised before was *the full
C-band, available, on every SIP* — harmless for discovery, since ONOS only reads
the first OCh signal, but untrue. It now translates the twin's real bands.

One consequence: `mc-pool` carries live occupancy, so the adapter's SIP
catalogue must no longer serve it. The catalogue still assigns port indices,
which have to stay stable, but payloads are re-read from the twin —
`TapiDeviceLambdaQuery` picks a lambda out of this block, and a cached one would
pick a wavelength already assigned.

### B2. No `/restconf` RESTCONF root — fixed

RFC 8040 §3.1 locates the API under a root resource discovered via
`/.well-known/host-meta`, conventionally `/restconf`, with data under
`{+restconf}/data`. TwinLight served `/data/...` with no root and no
`host-meta`. ONOS hard-codes `/restconf/data/...`.

**Verdict: twin gap, low cost.** The six T-API routers are now mounted under
`server.restconf_root` (default `/restconf`) as well as the bare `/data/`,
`host-meta` returns XRD per RFC 6415, and `{root}/yang-library-version` answers
— closing the capability-discovery gap `docs/TAPI_COMPLIANCE.md` named. The bare
mount stays for clients written against earlier releases; retiring it is in
[docs/PENDING.md](../../docs/PENDING.md).

This does **not** remove the adapter: A1 and A2 still require it.

### B3. POST rejected a single-entry JSON array — fixed, verdict reversed

> **Reversed.** This row previously read *"RFC 8040 Appendix B.2.1 shows the
> **object** form for creating one list entry and requires an array only when
> sending several, so TwinLight is not non-conformant here."* That is the
> opposite of what the RFC says.

RFC 8040 Appendix B.2.1's example of creating one list entry is:

```json
{ "example-jukebox:artist" : [ { "name" : "Foo Fighters" } ] }
```

and RFC 7951 §5.4 is categorical: *"A list instance is encoded as a name/array
pair"* — no exception for a single entry. `connectivity-service` is a YANG
`list`. So the array-of-one ONOS sends is the **correct** encoding and the bare
object TwinLight demanded was the non-conformant one.

**Verdict: twin defect, not "robustness".** POST and PUT now accept both
spellings; a multi-entry array is a clear 400, since the handler admits one
service. Hand-rolled body validation also had its pydantic error mapped to 422
— it was surfacing as a 500.

### B4. `modulation-format` was a bare non-standard key — fixed

`tapi-connectivity.yang` v2.6.0 contains **zero** occurrences of "modulation".
A bare `modulation-format` inside a `/data/` connectivity-service was therefore
exactly what constraint #1 forbids. The photonic module puts it on the
end-point:

```
end-point → layer-protocol-constraint
  → tapi-photonic-media:otsia-connectivity-service-end-point-spec
      → otsi-config → modulation → standard-modulation-technique
```

with `MT` identities — note ONF spells 16QAM as `MT_DP-QAM16`. The twin emits
and accepts only that; the prefixed identityref spelling is accepted too, per
RFC 7951 §6.8.

A payload carrying the old key gets a **422 naming the standard location**
rather than silently defaulting to DP-QPSK, which would provision a working
lightpath of the wrong format — worse than an error. Existing snapshots are in
the old shape and were deleted; see [§ C.7](#c-not-incompatibilities-at-all--errors-in-this-integrations-own-tooling).

### B5. `frequency-slot` is a bare non-standard key — deferred

The identical defect to B4, in the same payload: `tapi-connectivity.yang` has no
`frequency-slot` leaf either. 2.6 expresses assigned spectrum as
`mcg-connectivity-service-end-point-spec` → `mc-spectrum-config-pac` →
`spectrum` on the end-point.

**Verdict: twin defect.** Deferred deliberately, not overlooked — it is read by
`validate.sh` check 14, the adapter's admission log, `lightpath.sh` and the UI,
and bundling it with B4 would have made an ONOS validation failure ambiguous
between the two. The twin is therefore *inconsistent right now*: modulation is
standard-shaped and spectrum is not. Recorded in
[docs/PENDING.md](../../docs/PENDING.md).

### B6. `tapi-photonic-media:spectrum-context` is invented — deferred

`GET /data/tapi-photonic-media:spectrum-context` returns `num-slots`,
`slot-width-ghz` and `nominal-central-frequency-thz`. **None of those leaves
exists in T-API v2.6.0**, and neither does a `spectrum-context` container.

This is arguably worse than B4 and B5: a bare key is visibly proprietary,
whereas invented leaves published under the ONF module prefix give a client no
way to tell they are not standard.

**Verdict: twin defect (constraint #1).** Deferred: the UI's Spectrum page reads
it, and the replacement for most consumers is B1's per-SIP
`spectrum-capability-pac`, which nothing has migrated to yet. The grid
parameters are twin configuration, not T-API, and belong under `/internal/`.
Recorded in [docs/PENDING.md](../../docs/PENDING.md).

---

## C. Not incompatibilities at all — errors in this integration's own tooling

Recorded so they are not re-discovered, and because several are traps that
would bite anyone repeating this work.

| Symptom | Cause |
|---|---|
| `netcfg` POST returns `207 {"subjectClassKey '_comment' not found"}` | ONOS validates every top-level netcfg key against a registered `SubjectFactory`. A JSON file cannot carry explanatory comments. **207 is a partial-success code, so a script checking only for failure will not notice.** |
| `/onos/v1/drivers` returns 404 | That REST resource does not exist in ONOS 2.7. Driver registration is only observable via the Karaf CLI, so check the `org.onosproject.drivers.odtn-driver` app state instead. |
| Readiness probe returns instantly, then everything 503s | ONOS answers on :8181 for a minute or more before its core services register, returning HTTP 503 with a JSON body. `curl` without `-f` treats that as success — readiness must be judged on the payload. |
| `fault.sh cut-path` finds no fiber | `/internal/services/{uuid}` summarises the path at ROADM granularity; fibers must be resolved from the element inventory by endpoint pair. |
| OPM formatter crashes on a cut link | The twin correctly nulls every optical metric and pins pre-FEC BER to 1.0 when `status=link-failed`. |
| **C5.** Device never appears; ONOS logs `Driver not found` once and gives up | An ACTIVE `drivers.odtn-driver` bundle does **not** mean the `ols` driver is bound yet. On a cold Karaf boot the netcfg can land in that window, `RestDeviceProvider` fails permanently, and nothing in the REST API says why. `demo-up.sh` now re-pushes the netcfg once if the device has not registered. |
| **C7.** Twin refuses to start after a payload change, restoring its checkpoint | Expected — the loud failure from B4 working. But note the ordering trap: a running container writes a *fresh* checkpoint on SIGTERM, so deleting it before `demo-up.sh` just recreates it. Delete **after** the old container stops. |
| **C8.** `validate.sh` check 15 reports "twin admitted a DP-16QAM path that should have failed QoT" while the adapter log plainly shows it refusing | The check watched `len(recent-rejections)` for an increase, but `/adapter/status` returns `rejections[-10:]` — the count **saturates at 10** and can never rise again. A stack that had already seen ten refusals failed the check no matter what the twin did, and the message blamed the topology. Now compares the newest rejection's identity instead, as `lightpath.sh` already did. A saturating value is not a change detector. |

### C6. `gnpy.no_insert_edfas` is read by nothing

The field is validated, settable via `--no-insert-edfas`, and documented in
`docs/CONFIGURATION.md` — and no code reads it.
`examples/coronet_conus_config.yaml` sets it `true`, and GNPy's
`designed_network()` inserts 1068 EDFAs regardless.

**Verdict: our own inert knob.** A documented option that silently does nothing
is worse than no option. Deferred because honouring it means deciding what a
bare-fiber topology should do without amplifiers, which is a physics question.
Recorded in [docs/PENDING.md](../../docs/PENDING.md).

---

## D. Physics defects

### D1. Admission GSNR and reported OPM diverged by ~9 dB — fixed, verdict reversed

> **Reversed.** This was recorded as *"twin, and a judgement call rather than an
> obvious bug"*, offering two options: the transient models are too aggressive,
> or admission should gate on a transient-inclusive figure. Neither was right.
> It is one term, and it is a bug.

On Abilene → Atlanta (2148.7 km, 30 EDFAs, 7 ROADMs) the transient layer
decomposed as:

| Term | Contribution |
|---|---|
| EDFA reservoir | **−9.000 dB, constant** |
| PDL (hinge) | +0.34 … −0.30 dB, fluctuating about 0 ✓ |
| EEPN | −0.000 dB — silently inert (**D2**) |

−9.000 dB is exactly `n_edfa × gain_per_channel_db × channel_count`
= 30 × 0.3 × 1. `EdfaStateTracker` set the post-event steady state to
`-new_count × gain_per_channel_db`, reached within `tau_eff` = 10 µs and
**never decaying**. A second service on the same amplifiers doubled it to
−18 dB; a fully loaded 88-channel span would have read −792 dB.

That is not a transient. An AGC-controlled, gain-flattened EDFA in steady state
delivers its designed per-channel gain whatever its loading — which is precisely
what `designed_network()` baked into the GNPy baseline. Bononi–Rusch Eq. 19
describes the *excursion between* two steady states; the model had turned the
steady state itself into a standing penalty, double-counting loading the
baseline had already priced in.

The steady state is now zero deviation and the excursion, scaled by the load
step, relaxes back to it with τ_e. Reported GSNR moved from 2.6–3.2 dB to
11.5–12.0 dB against an **unchanged** admission figure of 11.891 dB. The
remaining ~0.4 dB is PDL and EEPN, comfortably inside `rmsa.qot_margin_db`.

Admission still gates on the pristine baseline, and that is deliberate: a
sample is a point in time, so gating on one would make admission depend on the
phase of the PDL drift when the request arrived, and two identical requests
seconds apart could decide differently. `rmsa.qot_margin_db` is the documented
allowance for the transient layer, as a system margin is in network design.

**Consequences for published results:**

* Any GSNR / Q / BER from `/internal/opm` or `/internal/path-info` on a path
  with EDFAs was low by `0.3 × n_edfa × n_services_sharing_those_amplifiers` dB
  — −9 dB for one 30-EDFA CORONET lightpath. "The twin reports BER 0.17" was an
  artefact.
* **Admission and blocking-probability results are unaffected**: admission used
  the baseline, which was always correct.
* Transient *time-series shape* was real (PDL); the *level* was not.

### D2. Accumulated CD stored in the wrong unit — fixed

`compute_path_baseline` stored gnpy's `si.chromatic_dispersion` straight into
`cd_ps_nm`, but `SpectralInformation` carries accumulated CD in **s/m** —
gnpy's own `Transceiver._calc_cd` applies the `× 1e3` that was missing. CD was
reported 1000× low (35.9 instead of 35 883 ps/nm on Abilene → Atlanta).

Two independent confirmations: `si.pmd` and `si.latency` alongside it *are*
scaled correctly, and the EGN backend emits genuine ps/nm via
`chromatic_dispersion_ps_per_nm(total_km)` — so **the two physics backends
disagreed by 1000× on the same field**.

Because the EEPN parameter α is proportional to accumulated dispersion, α was
1000× too small and the Shieh–Ho term never moved GSNR. That is exactly the
symptom `docs/PHYSICS.md` recorded as **"Unresolved — the term is implemented
per the reference but does not move GSNR"**; it now contributes ≈ −0.1 dB at
2150 km, and the limitation is resolved.

**Any published CD figure was 1000× low.**

[yang]: https://github.com/OpenNetworkingFoundation/TAPI/tree/v2.6.0/YANG
[rfc8040]: https://www.rfc-editor.org/rfc/rfc8040
[rfc7951]: https://www.rfc-editor.org/rfc/rfc7951
