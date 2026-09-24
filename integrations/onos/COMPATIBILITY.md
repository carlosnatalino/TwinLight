# ONOS compatibility: what the twin handles and what the adapter handles

Every mismatch between ONOS's ODTN driver and TwinLight, classified by where it
is handled. ONOS is treated as upstream software that this project does not
change, so for each item the question is whether TwinLight deviated from the
standard (fixed in the twin) or ONOS does (absorbed in the adapter).

The distinction matters beyond tidiness. Anything fixed in the twin is a real
T-API compliance improvement that benefits every client, and is reflected in
[docs/TAPI_COMPLIANCE.md](../../docs/TAPI_COMPLIANCE.md). Anything handled in
the adapter is ONOS-specific and must stay out of `src/twinlight`, whose T-API
modules implement only the standard (see
[CONTRIBUTING.md](../../CONTRIBUTING.md#t-api-surfaces-stay-standard)).

**Each classification names the clause it rests on:** the
[T-API v2.6.0 YANG][yang], [RFC 8040][rfc8040] (RESTCONF), or
[RFC 7951][rfc7951] (JSON encoding of YANG).

| # | Issue | Handled in |
|---|-------|------------|
| A1 | ONOS parses a port number from the SIP UUID tail | Adapter |
| A2 | ONOS reads the per-SIP GET response unwrapped | Adapter — and a separate twin issue |
| A3 | ONOS deletes pre-existing connectivity services | Adapter |
| A4 | ONOS cannot express a modulation format | Adapter (policy) |
| A5 | ONOS reads the T-API **2.1** photonic SIP shape | Adapter |
| A6 | ONOS mis-parses `G_6_25GHZ` and divides by zero | Adapter |
| B1 | SIP spectrum capability | Twin |
| B2 | RESTCONF root resource | Twin |
| B3 | Single-entry JSON array in POST bodies | Twin |
| B4 | Modulation format location | Twin |
| B5 | Assigned-spectrum location | Twin |
| B6 | No invented `tapi-photonic-media` resources | Twin |

---

## A. ONOS deviations — handled in the adapter

These are places where ONOS does something the standard does not ask for.
Changing TwinLight to match would make the twin *less* correct.

### A1. Port number parsed from the SIP UUID tail

```java
String[] uuidSeg = uuid.split("-");
PortNumber portNumber = PortNumber.portNumber(uuidSeg[uuidSeg.length - 1]);
```

`PortNumber.portNumber(String)` is `UnsignedLongs.decode()`. ONOS assumes the
last group of a UUID is a decimal port index — an assumption that holds for the
ADVA OLS the driver was written against and for nothing else.

T-API's `uuid` is an RFC 4122 UUID. TwinLight's `uuid5(NAMESPACE_DNS, ...)`
values are conformant and deterministic across runs, which reproducibility
depends on. Their last group is hex, so `decode()` throws.

**Handled in the adapter.** Renumbering SIPs in the twin to suit one client
would break RFC 4122 conformance, the UI, the client library and the gNMI
paths. The adapter republishes each SIP as `<real-uuid>-<index>` and strips the
suffix on the way back, so the real UUID still reaches ONOS as a port
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

So **both sides deviate here, in different directions**: ONOS wants no wrapper
at all, and TwinLight uses the wrong one.

**Handled in the adapter**, which is needed either way — fixing the twin would
still not satisfy ONOS. The twin's side is a known issue that affects every
standards-conformant client, and a fix is planned; see
[docs/ROADMAP.md](../../docs/ROADMAP.md).

### A3. ONOS deletes pre-existing connectivity services

`TapiDeviceHelper.removeInitalConnectivityServices()` deletes every connectivity
service it can see whenever ONOS's flow cache for the device is empty — on first
connect, and again after any ONOS restart. TwinLight is behaving correctly by
listing its own lightpaths; ONOS is claiming ownership of a domain it does not
exclusively own.

**Handled in the adapter.** The adapter shows ONOS only the services ONOS
created (`ADAPTER_EXPOSE_TWIN_SERVICES=false`). Suppressing this in the twin
would mean the twin misreporting its own state to all clients.

### A4. ONOS cannot express a modulation format

T-API 2.1's connectivity-service has no modulation field, so ONOS cannot send
one. TwinLight defaults to DP-QPSK when it is absent, so nothing breaks; the
adapter sets it explicitly so the format can be switched at runtime.

**Adapter policy.** Not a defect on either side. Even T-API 2.6 would not help
ONOS here: its modulation lives on the connectivity-service end-point (B4),
which the 2.1 driver has no notion of.

### A5. ONOS reads the T-API 2.1 photonic SIP shape

The ODTN driver dereferences
`tapi-photonic-media:media-channel-service-interface-point-spec` → `mc-pool` →
`available-spectrum` unconditionally. **Neither `mc-pool` nor
`media-channel-service-interface-point-spec` exists in T-API v2.6.0** — they
are 2.1 constructs, absent from `tapi-photonic-media.yang`. Four differences in
one block, all with the same cause:

| ONOS (T-API 2.1) | TwinLight (T-API 2.6.0) |
|---|---|
| `media-channel-service-interface-point-spec` → `mc-pool` | `photonic-media-service-interface-point-spec` → `spectrum-capability-pac` |
| frequencies in **MHz** | **uint64 Hz** (`grouping spectrum-band`) |
| bare `DWDM`, `G_50GHZ` | identityrefs `GRID_TYPE_DWDM`, `ADJUSTMENT_GRANULARITY_G_6_25GHZ` |
| `supported-layer-protocol-qualifier` | `supported-cep-layer-protocol-qualifier-instances` |

**Handled in the adapter.** Translating between two published versions of a
specification is precisely the adapter's job, and no change to the twin removes
it. `_mc_pool()` does the reshaping and the unit and token conversions.

### A6. ONOS mis-parses `G_6_25GHZ` and divides by zero

`TapiDeviceHelper.getChannelSpacing()` has trailing spaces in its `"G_100GHZ "`,
`"G_12_5GHZ "` and `"G_6_25GHZ "` case labels (an upstream typo), so those fall
through to `CHL_0GHZ` and then divide by zero in `getOchSignal()`. Only
`G_50GHZ` and `G_25GHZ` are safe.

TwinLight's grid is a 6.25 GHz flexi-grid and advertises itself that way:
`GRID_TYPE_FLEX` at `ADJUSTMENT_GRANULARITY_G_6_25GHZ`.

**Handled in the adapter.** The adapter forces the granularity shown to ONOS to
`ADAPTER_GRID_GRANULARITY` (default `G_50GHZ`). No standard asks a 6.25 GHz port
to claim 50 GHz, and a twin that did would mislead every other client into
computing channel centres that do not line up with its slots.

---

## B. Standard behaviour implemented in the twin

Standard T-API / RESTCONF behaviour that the integration relies on and that
TwinLight implements for every client.

### B1. SIP spectrum capability

`mc-pool` is standard T-API **2.1**; it does not appear anywhere in the 2.6.0
YANG, so a 2.6 server must not publish it. The 2.6 equivalent is an augment on
the SIP:

```
service-interface-point
  tapi-photonic-media:photonic-media-service-interface-point-spec
    spectrum-capability-pac
      supportable-spectrum / available-spectrum / occupied-spectrum
```

each a list of `spectrum-band` keyed on `upper-frequency lower-frequency`, in
uint64 Hz. The twin publishes it on all three SIP resources, built per request
from the live spectrum state, so it cannot go stale or leak into snapshots.

**What "available spectrum at a SIP" means.** A SIP sits on a transceiver, so
the answer is **SIP-local**: `occupied-spectrum` is the blocks used by services
terminating on *this* SIP, and `available-spectrum` is the rest of the band.
That is the only reading that makes the answer a property of the SIP, which is
what the YANG models — `spectrum-capability-pac` is a *port* attribute, and
folding in the occupancy of downstream links would attribute link state to a
port, with the choice of link depending on where the lightpath is going.

The adapter translates these real bands into the 2.1 `mc-pool` (A5). Because
the block carries live occupancy, the adapter's SIP catalogue caches only port
indices, which must stay stable, and re-reads payloads from the twin —
`TapiDeviceLambdaQuery` picks a lambda out of this block, and a cached one
could pick a wavelength already assigned.

### B2. RESTCONF root resource

RFC 8040 §3.1 locates the API under a root resource discovered via
`/.well-known/host-meta`, conventionally `/restconf`, with data under
`{+restconf}/data`. ONOS hard-codes `/restconf/data/...`.

The six T-API routers are mounted under `server.restconf_root` (default
`/restconf`). `host-meta` returns XRD per RFC 6415, and
`{root}/yang-library-version` answers. The bare `/data/` mount remains for
clients written against earlier releases and is scheduled for removal; see
[docs/ROADMAP.md](../../docs/ROADMAP.md).

This does **not** remove the need for the adapter: A1 and A2 still require it.

### B3. Single-entry JSON array in POST bodies

RFC 8040 Appendix B.2.1's example of creating one list entry is:

```json
{ "example-jukebox:artist" : [ { "name" : "Foo Fighters" } ] }
```

and RFC 7951 §5.4 is categorical: *"A list instance is encoded as a name/array
pair"* — no exception for a single entry. `connectivity-service` is a YANG
`list`, so the array-of-one ONOS sends is the correct encoding.

POST and PUT accept both the array and the bare-object spelling. A multi-entry
array is a `400`, since the handler admits one service, and body validation
errors are reported as `422`.

### B4. Modulation format location

`tapi-connectivity.yang` v2.6.0 contains **no** occurrences of "modulation".
The photonic module puts it on the end-point:

```
end-point → layer-protocol-constraint
  → tapi-photonic-media:otsia-connectivity-service-end-point-spec
      → otsi-config → modulation → standard-modulation-technique
```

with `MT` identities — note that ONF spells 16QAM as `MT_DP-QAM16`. The twin
emits and accepts only that location; the module-prefixed identityref spelling
is accepted too, per RFC 7951 §6.8.

A payload carrying a bare top-level `modulation-format` key gets a **422 naming
the standard location**, rather than silently defaulting to DP-QPSK — which
would provision a working lightpath of the wrong format.

### B5. Assigned-spectrum location

`tapi-connectivity.yang` has no `frequency-slot` leaf either. T-API 2.6
expresses assigned spectrum as an augment on the end-point, beside the
modulation one:

```
end-point → layer-protocol-constraint
  → tapi-photonic-media:mcg-connectivity-service-end-point-spec
      → mc-spectrum-config-pac → spectrum { lower-frequency, upper-frequency }
```

in uint64 Hz, with an `edge-frequency-constraint` naming the grid. A service
holding no allocation carries no MCG spec at all, rather than zeroes.

With B4 and B5 the connectivity-service's top level is standard T-API
throughout — `uuid`, `name`, `end-point` and the three states, nothing else.

### B6. No invented `tapi-photonic-media` resources

T-API v2.6.0 defines no `tapi-photonic-media:spectrum-context` container, and
no `num-slots`, `slot-width-ghz` or `nominal-central-frequency-thz` leaves.
Publishing leaves like those under the ONF module prefix would give a client no
way to tell they are not standard.

The grid parameters are twin *configuration*, so they are served at
`GET /internal/spectrum-context`, unwrapped. The twin's photonic surface is the
augments on the SIP (B1) and on the connectivity-service end-point (B4, B5),
served by the common and connectivity routers. The standard view of the grid is
the per-SIP `spectrum-capability-pac`, which reports real bands in Hz rather
than a slot count.

---

## C. Integration pitfalls

Traps that are easy to hit when working on or repeating this integration, and
that nothing in the ONOS or twin APIs points at directly.

| Symptom | Cause |
|---|---|
| `netcfg` POST returns `207 {"subjectClassKey '_comment' not found"}` | ONOS validates every top-level netcfg key against a registered `SubjectFactory`, so a JSON file cannot carry explanatory comments. **207 is a partial-success code, so a script checking only for failure will not notice.** |
| `/onos/v1/drivers` returns 404 | That REST resource does not exist in ONOS 2.7. Driver registration is only observable via the Karaf CLI, so check the `org.onosproject.drivers.odtn-driver` app state instead. |
| Readiness probe returns instantly, then everything 503s | ONOS answers on :8181 for a minute or more before its core services register, returning HTTP 503 with a JSON body. `curl` without `-f` treats that as success — readiness must be judged on the payload. |
| Device never appears; ONOS logs `Driver not found` once and gives up | An ACTIVE `drivers.odtn-driver` bundle does **not** mean the `ols` driver is bound yet. On a cold Karaf boot the netcfg can land in that window, `RestDeviceProvider` fails permanently, and nothing in the REST API says why. `demo-up.sh` re-pushes the netcfg once if the device has not registered. |
| `fault.sh cut-path` finds no fiber | `/internal/services/{uuid}` summarises the path at ROADM granularity; fibers must be resolved from the element inventory by endpoint pair. |
| OPM for a lightpath shows every optical metric as null and pre-FEC BER as 1.0 | Expected on a cut link: the twin reports `status=link-failed`, nulls the optical metrics and pins BER to 1.0. |
| Twin refuses to start after an upgrade, while restoring its checkpoint | A checkpoint written by a release with a different payload shape cannot be restored. Start once with `--reset`. Note that a running container writes a *fresh* checkpoint on SIGTERM, so delete a checkpoint only **after** the old container has stopped. |
| A change detector on `/adapter/status` never fires | `recent-rejections` holds only the last ten refusals, so its length saturates at 10. Compare the newest entry's identity instead, as `lightpath.sh` and `validate.sh` do. |

[yang]: https://github.com/OpenNetworkingFoundation/TAPI/tree/v2.6.0/YANG
[rfc8040]: https://www.rfc-editor.org/rfc/rfc8040
[rfc7951]: https://www.rfc-editor.org/rfc/rfc7951
