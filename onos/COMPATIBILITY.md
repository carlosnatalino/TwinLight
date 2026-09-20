# Where each incompatibility belongs: twin, or adapter?

Every mismatch found while making ONOS drive TwinLight, classified by where the
fix belongs. **ONOS is treated as immutable** — it is upstream software we do
not control — so the only question for each item is whether TwinLight is wrong
(fix the twin) or ONOS is being non-standard (absorb it in the adapter).

The distinction matters beyond tidiness: anything in column "twin" is a real
T-API compliance improvement that benefits every client, and belongs in
`docs/TAPI_COMPLIANCE.md`. Anything in column "adapter" is ONOS-specific glue
that must **never** migrate into `src/twinlight` — that is exactly what
[CLAUDE.md constraint #1](../CLAUDE.md) protects.

| # | Issue | Verdict |
|---|-------|---------|
| A1 | Port number parsed from the SIP UUID tail | Adapter, permanently |
| A2 | Per-SIP GET must be unwrapped | Adapter — **and** a separate twin bug |
| A3 | ONOS deletes pre-existing connectivity services | Adapter, permanently |
| A4 | ONOS cannot express a modulation format | Adapter policy |
| B1 | SIPs carry no `mc-pool` spectrum block | **Twin gap — recommend fixing** |
| B2 | No `/restconf` RESTCONF root | **Twin gap — recommend fixing** |
| B3 | POST rejects a single-entry JSON array | Twin robustness, low priority |
| D1 | Admission GSNR vs reported OPM diverge by ~9 dB | **Twin — needs a decision** |

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
is both correct and required by [CLAUDE.md constraint #8](../CLAUDE.md)
(determinism). Its last group is hex, so `decode()` throws.

**Verdict: adapter, permanently.** Renumbering SIPs in the twin to please one
client would break RFC 4122 conformance, the UI, the client library and the
gNMI paths. The adapter republishes each SIP as `<real-uuid>-<index>` and strips
the suffix on the way back, so the real UUID still reaches ONOS as a port
annotation.

### A2. Per-SIP GET must be unwrapped

ONOS reads `mc-pool` from the **top level** of the response to
`.../service-interface-point={uuid}`. TwinLight returns the *parent* container:

```json
{"tapi-common:context": {"service-interface-point": [ { ... } ]}}
```

Neither is what RFC 8040 §4.3 specifies. For a GET on a list-entry data
resource the body should be the target resource keyed by its own identifier:

```json
{"tapi-common:service-interface-point": [ { ... } ]}
```

So **both sides are off-spec here, in different directions.** ONOS wants no
wrapper at all; TwinLight uses the wrong wrapper.

**Verdict: adapter is required either way** — fixing the twin would still not
satisfy ONOS. But this is *also* a genuine, independent TwinLight bug worth
fixing on its own merits, because it affects every standards-conformant client,
not just ONOS. It is the one item on this list that is simultaneously both
columns.

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
adapter sets it explicitly only so the demo can switch formats.

**Verdict: adapter policy.** Not a defect on either side.

> **Worth a separate look, though:** TwinLight's `modulation-format` is a bare,
> non-standard key inside a `/data/` connectivity-service payload. CLAUDE.md
> constraint #1 says *"Never add a non-standard field to a `/data/` response."*
> If T-API expresses this at all it would be under a `tapi-photonic-media:`
> augment on the connectivity-service end-point, not as a top-level key. This
> predates the ONOS work and is unrelated to it, but it is the kind of thing a
> T-API-literate reviewer at a conference would spot.

---

## B. Genuine TwinLight gaps — fixing these shrinks the adapter

These are standard T-API / RESTCONF features TwinLight does not implement. The
adapter currently compensates. Implementing them in the twin would be a real
compliance improvement *and* would let the corresponding adapter code be
deleted.

### B1. SIPs carry no `mc-pool` spectrum block — recommend fixing

`tapi-photonic-media` augments `service-interface-point` with
`media-channel-service-interface-point-spec`, containing an `mc-pool` with
`supportable-spectrum`, `available-spectrum` and `occupied-spectrum`. This is
standard T-API, not an ONOS invention — ONOS is entitled to expect it.

TwinLight does not expose it, which
[docs/TAPI_COMPLIANCE.md](../docs/TAPI_COMPLIANCE.md) already concedes
("Photonic Media — ◐ Partial … Media-channel and OTSi resources are not
exposed"). ONOS dereferences it with no null check, so its absence is what
produces a device with zero ports.

**Verdict: twin gap. Recommend implementing.** The twin already has everything
needed: `config.spectrum` gives the grid, and `SpectrumState` /
`/internal/spectrum-grid` gives real per-link occupancy.

**Caveat worth stating plainly:** what the adapter synthesises today is *the
full C-band, available, on every SIP* — a fiction that is harmless for
discovery (ONOS only reads the first OCh signal) but would be wrong if anything
relied on it. A twin-side implementation should publish `supportable-spectrum`
from the spectrum context and `available-spectrum` from actual occupancy, which
would make it truthful for the first time.

### B2. No `/restconf` RESTCONF root — recommend fixing

RFC 8040 §3.1 locates the API under a root resource discovered via
`/.well-known/host-meta`, conventionally `/restconf`, with data under
`{+restconf}/data`. TwinLight serves `/data/...` with no root and no
`host-meta`. ONOS hard-codes `/restconf/data/...`.

**Verdict: twin gap, low cost.** Mounting the existing T-API routers under a
configurable RESTCONF root (defaulting to `/restconf`) and serving
`/.well-known/host-meta` is purely standards work — no proprietary fields, no
constraint #1 tension. It would remove one of the adapter's four reasons to
exist, and any other RESTCONF client would find the twin where it expects to.

### B3. POST rejects a single-entry JSON array — low priority

ONOS sends `{"tapi-connectivity:connectivity-service": [ {...} ]}`; TwinLight's
`ConnectivityService.model_validate()` expects a bare object and fails on a list.

RFC 8040 Appendix B.2.1 shows the **object** form for creating one list entry
and requires an array only when sending several, so TwinLight is *not*
non-conformant here — ONOS's array-of-one is simply the other legal spelling.

**Verdict: twin robustness, not a bug.** Accepting both forms is a two-line
leniency fix that would make the twin interoperate with more RESTCONF clients.
Worth doing eventually; not urgent, and the adapter handles it today.

---

## C. Not incompatibilities at all — errors in this integration's own tooling

Recorded so they are not re-discovered, and because two of them are traps that
would bite anyone repeating this work. All are already fixed.

| Symptom | Cause |
|---|---|
| `netcfg` POST returns `207 {"subjectClassKey '_comment' not found"}` | ONOS validates every top-level netcfg key against a registered `SubjectFactory`. A JSON file cannot carry explanatory comments. **207 is a partial-success code, so a script checking only for failure will not notice.** |
| `/onos/v1/drivers` returns 404 | That REST resource does not exist in ONOS 2.7. Driver registration is only observable via the Karaf CLI, so check the `org.onosproject.drivers.odtn-driver` app state instead. |
| Readiness probe returns instantly, then everything 503s | ONOS answers on :8181 for a minute or more before its core services register, returning HTTP 503 with a JSON body. `curl` without `-f` treats that as success — readiness must be judged on the payload. |
| `fault.sh cut-path` finds no fiber | `/internal/services/{uuid}` summarises the path at ROADM granularity; fibers must be resolved from the element inventory by endpoint pair. |
| OPM formatter crashes on a cut link | The twin correctly nulls every optical metric and pins pre-FEC BER to 1.0 when `status=link-failed`. |

---

## D. Open question for the twin — not something the adapter should hide

### D1. Admission GSNR and reported OPM diverge by ~9 dB

On Abilene → Atlanta (2149 km, 9 hops), the twin:

- **admits** a DP-QPSK lightpath, because `add_service()` gates on the pristine
  GNPy baseline of **11.9 dB** against DP-QPSK's 8.5 + 1.5 dB margin;
- then **reports** GSNR **2.6 dB**, Q −0.6 dB, pre-FEC BER **0.17** through
  `/internal/opm`, because OPM layers the four transient models on top of that
  baseline.

Both numbers are internally consistent and reproducible, and this behaviour
long predates the ONOS work — it is visible through the UI and the twin's own
API. But the two are answering the same question with a 9 dB gap, and on stage
it looks like the twin admitted a lightpath that cannot carry traffic.

Either reading is defensible:

- **The transient models are too aggressive** for a path this long, and the
  penalty should be revisited; or
- **admission should gate on a transient-inclusive figure**, not the pristine
  baseline — arguably the more physically honest choice for a *digital twin*,
  whose value proposition is precisely that it models time-varying impairments.

**Verdict: twin, and a judgement call rather than an obvious bug.** Flagged
rather than fixed, because changing either the transient magnitudes or the
admission criterion is a modelling decision with consequences for
`docs/PHYSICS.md` and for every published result — not something to change
quietly while wiring up a controller.

The adapter deliberately does **not** paper over it: it relays the twin's
numbers unchanged. The demo runbook addresses it head-on instead
([README.md § Act 2](README.md)), because it is the first thing an optical
networking audience will notice.
