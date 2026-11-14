# API reference

TwinLight exposes 73 REST operations plus a gNMI gRPC service carrying the
T-API performance-measurement stream. This document is
the catalogue; a live, interactive version is served at
<http://localhost:8080/docs> (OpenAPI/Swagger) and <http://localhost:8080/redoc>
whenever the twin is running.

Endpoints fall into two groups, kept strictly apart (see
[ARCHITECTURE.md](ARCHITECTURE.md#2-t-api-surfaces-stay-standard)):

- **`/restconf/data/…`** — T-API v2.6.0 only. Standard paths, hyphenated JSON
  keys, RESTCONF errors, `application/yang-data+json` responses.
- **`/internal/`, `/admin/`, `/config/`, `/transparent/`, `/metrics`** —
  everything the standard does not cover. `/transparent/` is the twin's *fully
  transparent* controller view: every element and parameter, addressed
  independently of T-API objects.

> **The T-API paths below are written as `/data/…` throughout.** Every one is
> also served under the RESTCONF root — `/restconf/data/…` by default, set by
> `server.restconf_root` — which is the canonical location per RFC 8040 §3.1
> and the one a client discovers from `/.well-known/host-meta`. The bare
> `/data/…` mount is kept for clients written against earlier releases and is
> scheduled for removal — see [ROADMAP.md](ROADMAP.md). New clients should use
> the RESTCONF root.

## T-API v2.6.0 endpoints

### Common context

| Method | Path |
|--------|------|
| GET | `/data/tapi-common:context` |
| GET | `/data/tapi-common:context/service-interface-point` |
| GET | `/data/tapi-common:context/service-interface-point={uuid}` |

The context is the northbound entry point: it carries the service-interface-point
(SIP) list, the topology context, the OAM context and the stream context. Each SIP's `name` list
carries the underlying GNPy element UID under the value-name `node-name`, which
is how SIPs map back to physical elements.

### Topology

| Method | Path |
|--------|------|
| GET | `/data/tapi-common:context/tapi-topology:topology-context` |
| GET | `…/topology={uuid}` |
| GET | `…/topology={uuid}/node={node_uuid}` |
| GET | `…/topology={uuid}/node={node_uuid}/owned-node-edge-point={nep_uuid}` |
| GET | `…/topology={uuid}/link={link_uuid}` |

Links carry a `latency-characteristic` whose `propagation-delay` (ns) is derived
from fiber length and the speed of light in silica.

### Connectivity services

| Method | Path |
|--------|------|
| GET | `/data/tapi-connectivity:connectivity-context/connectivity-service` |
| POST | `/data/tapi-connectivity:connectivity-context/connectivity-service` |
| GET | `…/connectivity-service={uuid}` |
| PUT | `…/connectivity-service={uuid}` |
| PATCH | `…/connectivity-service={uuid}` |
| DELETE | `…/connectivity-service={uuid}` |

**Create (POST) body:**

```json
{
  "tapi-connectivity:connectivity-service": {
    "name": [{"value-name": "service-name", "value": "my-link"}],
    "end-point": [
      {
        "local-id": "a-end",
        "service-interface-point": {"service-interface-point-uuid": "<SIP_A_UUID>"},
        "layer-protocol-constraint": [
          {
            "local-id": "otsi",
            "tapi-photonic-media:otsia-connectivity-service-end-point-spec": {
              "otsi-config": [
                { "local-id": "1",
                  "modulation": { "standard-modulation-technique": "MT_DP-QPSK" } }
              ]
            }
          }
        ]
      },
      {"local-id": "z-end", "service-interface-point": {"service-interface-point-uuid": "<SIP_Z_UUID>"}}
    ]
  }
}
```

The modulation format is the end-point's
`otsia-connectivity-service-end-point-spec` augment, as T-API v2.6.0 specifies
— `tapi-connectivity` has no modulation leaf of its own. `standard-modulation-technique`
accepts `MT_DP-QPSK` (the default when the augment is absent), `MT_DP-QAM16` or
`MT_DP-QAM64`; the module-prefixed identityref spelling is accepted too
(RFC 7951 §6.8). A bare top-level `modulation-format` key is rejected with a
`422` naming the standard location, rather than silently defaulting.

The body may be a single object, as above, or a one-entry JSON array — RFC 7951
§5.4 encodes a list entry as an array, and that is the form some controllers
send. An array of more than one entry is a `400`.

Creation runs the full RMSA admission path — route, spectrum first-fit, QoT
check against the format's required GSNR plus `rmsa.qot_margin_db`. A request
that cannot be satisfied is refused rather than admitted with a warning.

**Semantics of the mutating verbs:**

| Verb | Behaviour |
|------|-----------|
| PUT | Full replacement at the given UUID; the body must be a complete connectivity-service and its UUID must match the path |
| PATCH | Partial update of `name`, `administrative-state`, `lifecycle-state` only — **not** the modulation format, which would invalidate the admission decision |
| DELETE | Releases spectrum, invalidates the cached baseline, records the channel drop with the EDFA tracker; returns 204 |

A service with a spectrum allocation carries it on **each end-point**, beside
the modulation augment, as T-API v2.6.0 specifies — `tapi-connectivity` has no
`frequency-slot` leaf:

```json
"layer-protocol-constraint": [
  {
    "local-id": "otsi",
    "layer-protocol-name": "PHOTONIC_MEDIA",
    "tapi-photonic-media:otsia-connectivity-service-end-point-spec": {
      "otsi-config": [
        { "local-id": "1",
          "modulation": { "standard-modulation-technique": "MT_DP-QPSK" } }
      ],
      "number-of-otsi": 1
    },
    "tapi-photonic-media:mcg-connectivity-service-end-point-spec": {
      "number-of-mc": 1,
      "mc-spectrum-config-pac": [
        { "local-id": "1",
          "spectrum": {
            "lower-frequency": 190696875000000,
            "upper-frequency": 190753125000000
          },
          "edge-frequency-constraint": {
            "grid-type": "GRID_TYPE_FLEX",
            "adjustment-granularity": "ADJUSTMENT_GRANULARITY_G_6_25GHZ"
          } }
      ]
    }
  }
]
```

Frequencies are uint64 Hz. A service with no allocation carries no MCG spec at
all, rather than a zeroed one.

### Path computation

| Method | Path |
|--------|------|
| GET | `/data/tapi-path-computation:path-computation-context` |
| GET | `…/path-computation-service` |
| POST | `…/path-computation-service/compute-path` |

Computes candidate paths without committing any state — useful for what-if
analysis before creating a service.

### Equipment and spectrum

| Method | Path |
|--------|------|
| GET | `/data/tapi-equipment:equipment-context` |
| GET | `/data/tapi-equipment:equipment-context/equipment` |
| GET | `/data/tapi-equipment:equipment-context/equipment={uuid}` |

There is no `tapi-photonic-media` resource of its own. The photonic surface is
the augments on the SIP and on the connectivity-service end-point, so it is
served by the Common and Connectivity routers. The grid parameters (slot count,
slot width, centre frequency) are twin configuration rather than T-API, so they
are served at
[`/internal/spectrum-context`](#internal--observation-and-metadata).

Per-SIP spectrum is the standard surface, carried on every
`service-interface-point` as a `tapi-photonic-media` augment:

```json
{
  "tapi-photonic-media:photonic-media-service-interface-point-spec": {
    "spectrum-capability-pac": {
      "supportable-spectrum": [
        {
          "lower-frequency": 190696875000000,
          "upper-frequency": 195496875000000,
          "frequency-constraint": {
            "grid-type": "GRID_TYPE_FLEX",
            "adjustment-granularity": "ADJUSTMENT_GRANULARITY_G_6_25GHZ"
          }
        }
      ],
      "available-spectrum": [],
      "occupied-spectrum": []
    }
  }
}
```

Frequencies are **uint64 Hz**. `occupied-spectrum` lists the blocks held by
services terminating on *that* SIP — it is a property of the port, not of the
links the lightpath crosses — and `available-spectrum` is the remainder of the
supportable band, merged into maximal contiguous runs.

### OAM jobs

| Method | Path |
|--------|------|
| GET | `/data/tapi-common:context/tapi-oam:oam-context` |
| GET | `…/tapi-oam:oam-context/oam-job` |
| GET | `…/tapi-oam:oam-context/oam-job={uuid}` |

The twin creates one `oam-job` per admitted connectivity service and removes
it when the service is deleted; jobs are carried through snapshots. They are
read-only — `POST`, `PUT` and `DELETE` answer 405. Each job is the anchor of
its service's performance records on the [measurement stream](#gnmi-streaming)
(TR-548 §6.2.3) and references the end-point the OPM is measured at:

```json
{
  "tapi-oam:oam-job": [{
    "uuid": "6f0c…",
    "oam-job-state": "tapi-oam:OAM_JOB_STATE_ACTIVE",
    "creation-time": "20261114093012.4Z",
    "connectivity-service-end-point": {
      "connectivity-service-uuid": "c2a1…",
      "connectivity-service-end-point-local-id": "z-end"
    },
    "administrative-state": "UNLOCKED",
    "operational-state": "ENABLED",
    "lifecycle-state": "INSTALLED"
  }]
}
```

The job UUID is name-based on the service UUID, so it survives a `PUT`
replacement and a restore. The referenced end-point is always `end-point[1]`:
the twin propagates from the first end-point to the second, so that is where
the signal it reports is received. `oam-job-type` is omitted — the only
photonic job type, `OAM_JOB_TYPE_OPTICAL_POWER`, would mislabel records that
carry GSNR, OSNR, BER and the other QoT measures.

### Streaming discovery

| Method | Path |
|--------|------|
| GET | `/data/tapi-common:context/tapi-streaming:stream-context` |
| GET | `…/tapi-streaming:stream-context/supported-stream-type` |
| GET | `…/tapi-streaming:stream-context/supported-stream-type={uuid}` |
| GET | `…/tapi-streaming:stream-context/available-stream` |
| GET | `…/tapi-streaming:stream-context/available-stream={uuid}` |

TR-548 ST-0.2 discovery. There is one `supported-stream-type` — exactly the one
TR-548 §6.2.2 makes mandatory for performance data — and one
`available-stream` of it on the gRPC port:

```json
{
  "tapi-streaming:stream-context": {
    "supported-stream-type": [{
      "uuid": "…",
      "stream-type-name": "measurement-details",
      "record-retention": "5",
      "stream-type-content": ["tapi-gnmi-streaming:GNMI_STREAMING_OBJECT_TYPE_MEASUREMENT_DETAILS"],
      "log-storage-strategy": "tapi-streaming:LOG_STORAGE_STRATEGY_TRUNCATED",
      "log-record-strategy": "tapi-streaming:LOG_RECORD_STRATEGY_WHOLE_ENTITY",
      "record-trigger": "tapi-streaming:RECORD_TRIGGER_PERIODIC",
      "connection-protocol-details": {
        "allowed-connection-protocols": ["tapi-streaming:CONNECTION_PROTOCOL_GNMI"],
        "encoding-format": "tapi-streaming:ENCODING_FORMAT_PROTOBUF"
      }
    }],
    "available-stream": [{
      "uuid": "…",
      "connection-address": ["grpc://twin.example.org:50051"],
      "stream-state": "tapi-streaming:STREAM_STATE_ACTIVE",
      "supported-stream-type": {"supported-stream-type-uuid": "…"},
      "connection-protocol": "tapi-streaming:CONNECTION_PROTOCOL_GNMI"
    }]
  }
}
```

`record-retention` is `streaming.record_retention_min` (minutes);
`connection-address` is `streaming.advertised_address`, or
`grpc://<FQDN>:<grpc_port>` when that is empty. `stream-state` is `ALIGNING`
from startup until the first sampling tick has been logged and `ACTIVE` from
then on (TR-548 ST-1.1). Both UUIDs are name-based and stable across restarts.

### Error format

Errors under `/data/` use the RESTCONF format of
[RFC 8040](https://www.rfc-editor.org/rfc/rfc8040), served as
`application/yang-data+json`:

```json
{
  "ietf-restconf:errors": {
    "error": [
      {
        "error-type": "application",
        "error-tag": "invalid-value",
        "error-message": "No path with sufficient GSNR: best 12.1 dB, required 16.0 dB"
      }
    ]
  }
}
```

### A note on percent-encoded colons

T-API paths contain literal colons (`tapi-common:context`). Browsers
percent-encode those to `%3A` per the WHATWG URL specification, which would
otherwise miss the route. `PathDecodeMiddleware` decodes them before routing, so
both spellings work.

### RESTCONF root

| Method | Path | Returns |
|--------|------|---------|
| GET | `/.well-known/host-meta` | XRD (RFC 6415) advertising the RESTCONF root |
| GET | `/restconf` | `ietf-restconf:restconf` with `data`, `operations`, `yang-library-version` |
| GET | `/restconf/yang-library-version` | `{"ietf-restconf:yang-library-version": "2019-01-04"}` |

RFC 8040 §3.1 has a client discover the root rather than assume it, so
`host-meta` is XML by specification — a JSON variant would not be found. The
root path itself is `server.restconf_root`; setting it to `""` serves only the
bare `/data/…` paths and omits these three resources.

## Non-standard endpoints

### `/internal/` — observation and metadata

| Method | Path | Returns |
|--------|------|---------|
| GET | `/internal/opm` | Live OPM for every service |
| GET | `/internal/opm/{service_uuid}` | Live OPM for one service |
| GET | `/internal/services/{service_uuid}` | Name, modulation format, path hops, total fiber km |
| GET | `/internal/links` | Link inventory with per-link state, used by the UI |
| GET | `/internal/spectrum-grid` | Per-link slot occupancy, used by the UI heat map |
| GET | `/internal/spectrum-context` | Grid parameters: `num-slots`, `slot-width-ghz`, `nominal-central-frequency-thz` |
| GET | `/internal/path-info?sip_a=&sip_z=&modulation=` | Path hops and a QoT estimate without creating a service |
| GET | `/internal/services/{service_uuid}/eye-diagram` | Synthesised eye-diagram traces |
| GET | `/internal/services/{service_uuid}/constellation` | Synthesised constellation points |

The OPM endpoints and the [gNMI measurement stream](#gnmi-streaming) share one
computation (`sample_service_opm`): at the same instant both report identical
values, the stream as T-API records and these as the twin's own JSON.

**OPM response — all services** (`GET /internal/opm`) wraps a list:

```json
{
  "services": [
    {
      "service-uuid": "…",
      "timestamp": 1234567890.123,
      "measurements": {
        "osnr-db": 19.1,
        "osnr-01nm-db": 23.2,
        "gsnr-db": 16.4,
        "pre-fec-ber": 1.17e-3,
        "q-factor-db": 9.66,
        "post-fec-ber-threshold": 1.0e-15,
        "post-fec-ber-hd": 3.48e-6,
        "gmi-bits-per-symbol": 3.981,
        "ngmi": 0.995,
        "chromatic-dispersion-ps-per-nm": 1.3,
        "pmd-ps": 0.87
      }
    }
  ],
  "timestamp": 1234567890.123
}
```

**OPM response — single service** (`GET /internal/opm/{service_uuid}`) returns
one such entry directly, without the `services` wrapper:

```json
{
  "service-uuid": "…",
  "timestamp": 1234567890.123,
  "measurements": {
    "osnr-db": 19.1,
    "osnr-01nm-db": 23.2,
    "gsnr-db": 16.4,
    "pre-fec-ber": 1.17e-3,
    "q-factor-db": 9.66,
    "post-fec-ber-threshold": 1.0e-15,
    "post-fec-ber-hd": 3.48e-6,
    "gmi-bits-per-symbol": 3.981,
    "ngmi": 0.995,
    "chromatic-dispersion-ps-per-nm": 1.3,
    "pmd-ps": 0.87
  }
}
```

`osnr-db` and `gsnr-db` are referenced to the **signal bandwidth** — the SNR
the receiver sees, and the reference the admission thresholds are quoted
against. `osnr-01nm-db` is the same OSNR at the conventional **0.1 nm**, which
reads 4.08 dB higher at 32 GBd. There is deliberately no 0.1 nm GSNR; see
[PHYSICS.md](PHYSICS.md#which-reference-bandwidth-an-snr-is-quoted-against).

The example is a DP-16QAM lightpath. The coding-layer fields come from
separate models and are meant to be read side by side, not as one number
measured three ways:

| Field | Model | Configured by |
|-------|-------|---------------|
| `pre-fec-ber`, `q-factor-db` | Gaussian approximation for Gray-coded M-QAM at the GSNR | — |
| `post-fec-ber-threshold` | Threshold model: `post_fec_ber_floor` (or the input, if lower) up to the code's pre-FEC BER limit; the uncorrected input beyond it | `fec.threshold` |
| `post-fec-ber-hd` | Bounded-distance decoding of a hard-decision block code — RS(255,239) by default | `fec.hard_decision` |
| `gmi-bits-per-symbol` | Generalized mutual information per polarization, bit per 2D symbol, in [0, bits per symbol] | — |
| `ngmi` | GMI ÷ bits per symbol, in [0, 1] — what a soft-decision FEC threshold is stated in | — |

A service on a failed link reports every BER as 1.0 and the other fields as
`null`. See [PHYSICS.md § 3](PHYSICS.md#3-from-gsnr-to-ber-q-post-fec-ber-and-gmi)
for the formulas and their limits.

Every read is evaluated at the current wall-clock time: poll it twice a couple of
seconds apart and the numbers move, because the transient models are
re-evaluated rather than cached.

**Service info response:**

```json
{
  "service-uuid": "…",
  "name": "MyLink",
  "modulation-format": "DP-16QAM",
  "hops": [
    {"uid": "Site_A", "type": "Transceiver", "distance_km_to_next": 80.0},
    {"uid": "Site_B", "type": "Transceiver", "distance_km_to_next": null}
  ],
  "total-fiber-km": 80.0
}
```

### `/config/` — runtime parameter overrides

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/config/get` | Current values of the twin's runtime-mutable knobs |
| POST | `/config/set` | Change element parameters (any attribute in `allowed_attributes`), fail or restore a fiber, or change the runtime-mutable twin config (every field of `transients`, `rmsa` and `fec`; listed as `allowed_twin_keys`) |
| GET | `/config/devices/{uid}` | Per-element parameter values for one network element |

This is the fault-injection and sensitivity-analysis plane: raise a span's loss
coefficient or fail a fiber, and every affected service's cached baseline is
invalidated so the next OPM read reflects the change. It is what makes
before/after experiments possible without restarting the twin.

### `/config/` — parameters by path

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/config/parameters?path=…` | Expand a [parameter path](#parameter-paths) into the parameters it addresses, with current values and bounds. Default `/*/*/*` lists everything mutable |
| POST | `/config/parameters` | Set absolute values: `{"set": {"<path>": <number>, …}}` |
| POST | `/config/parameters/changes` | Apply a list of changes: `[{"path": …, "to" \| "by" \| "by_percent": …}, …]` |

Both POSTs take `?dry_run=true`, which validates the batch and returns the
result (each parameter's `previous` and new `value`) without writing anything.

Both are **immediate and all-or-nothing**: the whole batch is validated before
anything is written, an error lists every offending parameter, and a write that
fails despite that rolls back the ones before it. A parameter that two entries of
one batch would write is an error rather than an order-dependent result. Writes
go through the same path as `POST /config/set`: the allow-list bounds apply,
`/config/get` reports the values as overrides, and the affected services'
baselines are invalidated, so the next OPM read, gNMI sample or Prometheus scrape
reflects them.

`/config/set` remains the way to fail or restore a fiber and to change the
twin's own runtime settings; these endpoints add path addressing for element
parameters.

```json
POST /config/parameters
{"set": {"/Fiber/*/loss_coef": 0.22, "/Edfa/Edfa1/tilt_target": 0.5}}
```

```json
POST /config/parameters/changes
[
  {"path": "/Link/Site_A/Site_B/Fiber/*/loss_coef", "by": 0.05},
  {"path": "/Edfa/*/gain_target", "by": 3, "direction": "decrease"},
  {"path": "/Edfa/Edfa1/out_voa", "by_percent": 10, "out_of_bounds": "clamp"}
]
```

| Change field | Default | Meaning |
|--------------|---------|---------|
| `path` | — | Parameter path naming a single attribute |
| `to` | — | Absolute value |
| `by` | — | Additive change from each parameter's own current value (signed) |
| `by_percent` | — | Relative change from each parameter's current value, in % (signed) |
| `direction` | — | `increase` / `decrease`: treat `by`/`by_percent` as a magnitude |
| `out_of_bounds` | `reject` | `reject` a result outside the attribute's range, or `clamp` it to the bound |

Exactly one of `to`, `by` and `by_percent` is required. Every entry is computed
from the values before the batch, so the order of the list does not matter. A
relative change skips parameters whose value is unset (reported under
`skipped`); an entry left with nothing to change is an error.

#### Parameter paths

Parameters are addressed by a path modelled on the
[ns-3 attribute paths](https://www.nsnam.org/docs/manual/html/attributes.html)
(`/NodeList/*/DeviceList/0/…`), walking the optical hierarchy instead of an
object tree:

```
[/Service/<sel>] [/Link/<from-sel>/<to-sel>] /<Kind>/<uid-sel>/<attribute>
```

| Segment | Meaning |
|---------|---------|
| `<Kind>` | Element kind: `Fiber`, `Edfa`, `Roadm`, or `*` |
| `<uid-sel>` | Element UID selector |
| `<attribute>` | One attribute from `allowed_attributes` in `GET /config/get`, or `*` (listing only) |
| `/Service/<sel>` | Optional scope: only elements on the *allocated* path of a matching service, matched by UUID or `service-name` |
| `/Link/<from>/<to>` | Optional scope: only inline fibers and amplifiers between a matching pair of ROADMs/transceivers, **in that direction** |

Scopes intersect and may come in either order. A selector is one or more terms
joined by `|`. Each term is a glob (`*`, `east*`, `roadm ?orlaix`), a quoted
literal (`"fiber (A → B)-_(1/4)"`, required when a UID contains `/`, `|` or glob
characters), or a regex (`~"^fiber \(Brest"`; unanchored, like awk's `~`).
Inside quotes, `\"` and `\\` escape a quote and a backslash; any other
backslash is literal, so regex escapes are written as usual.

| Path | Addresses |
|------|-----------|
| `/Fiber/*/loss_coef` | Every span's loss coefficient |
| `/*/*/loss_coef` | The same: with a wildcard kind, only kinds that have the attribute match |
| `/Edfa/east*\|west*/gain_target` | Gain of the amplifiers whose UIDs start with `east` or `west` |
| `/Link/roadm Brest/roadm Morlaix/Edfa/*/nf_offset` | Noise-figure offset of every amplifier on the Brest → Morlaix link, boosters included |
| `/Service/my-service/Fiber/*/loss_coef` | Every span carrying the service named `my-service` |

Naming a kind explicitly with an attribute it does not have (`/Edfa/*/loss_coef`)
is rejected rather than treated as an empty match. Every parameter in a response
carries a fully qualified `path` naming just that parameter, which can be fed
straight back into `POST /config/parameters`.

#### Soft failures

A soft failure is a slow degradation that leaves the lightpath up while its
margin erodes — a span whose loss creeps up as a connector contaminates, an
amplifier whose gain sags as its pump ages. The twin has no clock of its own and
does not ramp anything: a gradual change is a client issuing these calls over
time. `twinlight-soft-failure` is that client. For a scenario it

1. plans every entry in one `POST /config/parameters/changes?dry_run=true`,
   which checks every end value and returns each parameter's start and end;
2. on each tick, sends the current value of every due ramp in one
   `POST /config/parameters`;
3. sends instant steps through `POST /config/parameters/changes` when they
   fall due, so a relative step is taken from the value at that moment.

Ramp shapes (`linear`, `accelerating`, `saturating`, `sigmoid`, `step`), speeds,
start delays, write intervals and what happens at the end (`hold`, `revert`,
`reverse`) are all options of the script — see its `--help` and
[examples/soft_failure_scenario.yaml](../examples/soft_failure_scenario.yaml).

### `/transparent/` — transparent optical domain controller

The whole physical model as one data tree — every element of the backend's
network and every parameter the twin models, plus its own configuration —
addressed with ns-3-style paths keyed by element UIDs, over a RESTCONF-shaped
protocol. The full description, with the path grammar, datastore semantics and a
worked scenario, is [TRANSPARENT_CONTROLLER.md](TRANSPARENT_CONTROLLER.md).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/transparent` | Root resource: datastores, operations, backend |
| GET | `/transparent/schema` | Every node and leaf: type, unit, bounds, `config` flag, why a field is read-only |
| GET | `/transparent/data[/<path>]` | Operational view (config + state). Query: `content`, `depth`, `with-defaults`, `format=tree\|leaves` |
| PUT | `/transparent/data[/<path>]` | Replace in running: set what the body names, revert every other config leaf under the target |
| PATCH | `/transparent/data[/<path>]` | Merge into running; with `Content-Type: application/yang-patch+json`, a YANG Patch (RFC 8072) transaction |
| DELETE | `/transparent/data[/<path>]` | Revert every config leaf under the target to its startup value |
| GET, PUT, PATCH, DELETE | `/transparent/ds/<datastore>[/<path>]` | The same per NMDA datastore: `running`, `candidate` (writable), `startup`, `operational` |
| GET | `/transparent/operations` | The operations below |
| POST | `/transparent/operations/{commit,discard-changes,validate,compare,redesign}` | Commit the candidate; drop it; validate it (or an inline YANG Patch) with an optional QoT what-if; diff two datastores as a YANG Patch; re-run the GNPy design |

```
/ElementList/<uid>[/$Kind]/<leaf>          /ElementList/*/$Fiber/loss_coef
/NodeList/<uid>/…                          /NodeList/*/$Roadm/target_pch_out_db
/LinkList/<from>/<to>[/ElementList/…]      /LinkList/roadm A/roadm B/ElementList/[0]
/ServiceList/<uuid|name>[/qot|/ElementList] /ServiceList/my-service/qot/gsnr_db
/ModulationList/<format>/<leaf>            /ModulationList/DP-QPSK/req_gsnr_db
/Config/<section>/…/<field>                /Config/transients/*/enabled
```

Writes answer `204`, or `200` with the applied changes and the invalidated
services under `Prefer: return=representation`. Every data response carries an
`ETag` — the running revision, which any configuration change through any
interface moves — and `If-Match` makes a write or a commit conditional on it
(`412` otherwise). Errors use the RESTCONF errors body, one entry per offending
leaf. Writes go through the same `TapiContext` methods as `/config`, so OPM,
gNMI, Prometheus, `/config/get` and snapshots all reflect them.

### `/admin/` — checkpoints

| Method | Path | Notes |
|--------|------|-------|
| POST | `/admin/snapshot` | Serialise connectivity + spectrum state to JSON. Optional body `{"path": "…"}`; default `snapshots/twin-<ISO8601>.json` |
| POST | `/admin/restore` | Restore from a snapshot. Body: `{"path": "…"}` |
| GET | `/admin/snapshot/latest` | Path and mtime of the most recent snapshot |
| GET | `/admin/snapshots` | All snapshots, newest first |
| GET | `/admin/snapshot/content?path=<file>` | Contents of one snapshot; the path must resolve inside the snapshot directory |

Snapshots record which physical-layer backend produced them. Restoring under a
different backend is rejected rather than silently reinterpreted, because the
GSNR values would not be comparable.

At startup, `--restore <path>` or `--restore-latest` replays a snapshot before
the servers accept traffic.

### `/metrics` — Prometheus

| Method | Path |
|--------|------|
| GET | `/metrics` |

One snapshot per scrape, in Prometheus text exposition format:

- Per-service OPM: `twinlight_opm_gsnr_db`, `twinlight_opm_osnr_db`,
  `twinlight_opm_osnr_01nm_db`, `twinlight_opm_q_factor_db`,
  `twinlight_opm_pre_fec_ber`, `twinlight_opm_post_fec_ber_threshold`,
  `twinlight_opm_post_fec_ber_hd`, `twinlight_opm_gmi_bits_per_symbol`,
  `twinlight_opm_ngmi`,
  `twinlight_opm_chromatic_dispersion_ps_per_nm`, `twinlight_opm_pmd_ps`,
  each labelled with `service_uuid`, `service_name` and `modulation`.
  The two OSNR gauges are one measurement on two reference bandwidths.
- Per-element `/config` values: `twinlight_fiber_loss_coef_db_per_km`,
  `twinlight_edfa_nf_db` (EGN), `twinlight_edfa_nf_offset_db` (GNPy),
  `twinlight_edfa_gain_target_db`,
  `twinlight_fiber_failed`, `twinlight_service_link_failed`.
- Twin state: `twinlight_services_total`, `twinlight_failed_links_total`,
  `twinlight_rmsa_qot_margin_db`, `twinlight_transient_enabled`.
- `twinlight_info{backend="gnpy"|"egn"}` so a dashboard can show which
  physical-layer backend produced a series.

A ready-made scrape config is in
[`examples/prometheus-scrape.yaml`](../examples/prometheus-scrape.yaml) and a
Grafana dashboard in
[`examples/grafana-dashboard.json`](../examples/grafana-dashboard.json) (it uses
a `${DS_PROMETHEUS}` placeholder so it imports against any Prometheus data
source). The Compose stack provisions both automatically.

## gNMI streaming

The gRPC server on `server.grpc_port` (50051) carries one stream: the T-API
v2.6.0 performance-measurement stream of TR-548 §6, discovered as described in
[Streaming discovery](#streaming-discovery). Configuration is the
[`streaming`](CONFIGURATION.md#streaming--the-t-api-measurement-stream) section.

| RPC | Behaviour |
|-----|-----------|
| `Capabilities` | `tapi-streaming`, `tapi-gnmi-streaming` and `tapi-oam` (Linux Foundation ONMI, 2.6.0); encoding `PROTO`; gNMI 0.10.0 |
| `Subscribe` | The TR-548 §6.3.4 subscription only; anything else is `INVALID_ARGUMENT` |
| `Get` / `Set` | `UNIMPLEMENTED` — read and configure the twin over RESTCONF |

### Records

Every `streaming.sample_interval_s` the twin takes one OPM sample of every
connectivity service — the computation `GET /internal/opm` serves, at a single
instant — and logs one record per OAM job, whether or not anyone is
subscribed. Every subscriber receives the same records:

```text
SubscribeResponse.update (Notification)
  timestamp   ns since the epoch: when the record was logged (the resume key)
  prefix      { target: "tapi" }
  update[0]
    path      oam-job[uuid=<oam-job uuid>]
    val       proto_bytes = serialized tapi.MeasurementDetails
```

`tapi.MeasurementDetails` (from the vendored
[`tapi-streaming-performance.proto`](../src/twinlight/streaming/proto/tapi-streaming-performance.proto))
states the shared properties once and lists the measures in
`qualified-measurement`:

| Field | Value |
|-------|-------|
| `time-measurement-was-sampled` | Sampling instant, **milliseconds** since the epoch (TR-548 §6.3.5 example) |
| `sample-interval` | `sample_interval_s` in **nanoseconds** |
| `native-resource-id` | GNPy UID of the receiving transceiver |
| `direction-of-measured-signal` | `RECEIVE` |

| list-index | Measure | Type | Units |
|-----------:|---------|------|-------|
| 0 | chromatic dispersion | normalized `CHROM_DISP` | (ps/nm) |
| 1 | PMD (mean DGD) | normalized `POL_MODE_DISP` | (ps) |
| 2 | pre-FEC BER | normalized `FEC_PRE_FEC_BER` | — |
| 3 | post-FEC BER, hard-decision code model | normalized `FEC_POST_FEC_BER` | — |
| 4 | GSNR | native `gsnr-db` | `dB` |
| 5 | OSNR (signal bandwidth) | native `osnr-db` | `dB` |
| 6 | OSNR (0.1 nm reference) | native `osnr-01nm-db` | `dB` |
| 7 | Q-factor | native `q-factor-db` | `dB` |
| 8 | GMI | native `gmi-bits-per-symbol` | `bit/symbol` |
| 9 | NGMI | native `ngmi` | `1` |
| 10 | post-FEC BER, threshold model | native `post-fec-ber-threshold` | `1` |

Normalized measures carry no `units` field, as the YANG asks where the type
defines them; the parenthesised units are those of the twin's OPM. Native
types are the OPM keys of `GET /internal/opm`, so a record maps back to the
REST view one-to-one.

A service whose path crosses a failed fiber reports every measure with
`value-qualifier: MISSING` and no value or units — including the BERs that the
REST view shows as 1.0 for charting. A sample the twin could not compute is
reported the same way; a non-finite value is `INVALID`.

### Subscribing

The one accepted subscription (TR-548 §6.3.4):

```text
SubscribeRequest
  subscribe
    prefix        { target: "tapi" }          # or omitted
    subscription  [ { path: {}, mode: TARGET_DEFINED } ]
    mode          STREAM
    updates_only  true
  extension       [ history { range { start: <ns> } } ]   # optional: resume
```

`mode: SAMPLE` with `sample_interval` 0 — the form of the RIA's own example —
is accepted as well. `encoding` may be omitted or `PROTO`; `qos` and
`allow_aggregation` are ignored. Any other field, path content, mode, encoding
or extension is rejected with `INVALID_ARGUMENT` and a message that starts with
the field name, for example `subscribe.updates_only: must be true (TR-548
§6.3.4)`. So is a second message on an open subscription; a client
half-closing its send side is fine.

Without the history extension the client receives `sync_response` at once and
then every record logged from then on. While the stream is still `ALIGNING`
nothing has been logged yet, so a client that connects early simply waits.

### Resuming after a lost connection

TR-548 ST-6.4: resubscribe with `range.start` set to the notification
timestamp of the last record processed. The twin replays every retained record
logged at or after `start` (so the records at `start` arrive again), sends
`sync_response`, and continues live.

- `start` older than the oldest retained record: replay starts at the oldest
  record. Records older than `record_retention_min` are gone.
- `start` exactly at the oldest retained record: that record is not sent, as
  ST-6.4 requires.
- `start` in the future: live records only.
- `range.end` must be omitted; `snapshot_time` is rejected.

### Authentication

With `streaming.auth_token` set, every call — `Capabilities` included — must
carry `authorization: Bearer <token>` metadata (the scheme is
case-insensitive); otherwise the call fails with `UNAUTHENTICATED`. An empty
token disables the check. The transport is plaintext gRPC either way.

### Consuming it

```bash
twinlight-client                                   # localhost defaults
twinlight-client --rest-url http://host:8080       # address from discovery
twinlight-client --gnmi-target host:50051 --token "$TOKEN"   # override; auth
```

`twinlight-client` lists the services, discovers the stream and the chosen
service's oam-job over RESTCONF, and prints that job's records, resuming
automatically after a dropped connection. `--token` defaults to
`$TWINLIGHT_STREAM_TOKEN`.

Programmatically:

```python
from twinlight_client import TapiClient, TapiStreamClient
from twinlight_client.streaming import discover_stream, oam_job_for_service

async with TapiClient("http://host:8080") as rest:
    endpoint = await discover_stream(rest)            # ST-0.2
    job = await oam_job_for_service(rest, service_uuid)
async with TapiStreamClient(endpoint.target, token) as stream:
    async for record in stream.follow(job_uuid=job):  # ST-6.2 + ST-6.4
        print(record.sampled_ms, record.measurements["gsnr-db"])
```

`record.measurements` is keyed by the OPM keys of `GET /internal/opm`, with
`None` for a MISSING measure; `record.details` is the decoded
`tapi.MeasurementDetails`.

### Migrating from the earlier gNMI interface

Earlier releases served REST resources as JSON_IETF over invented gNMI paths
(`tapi-common:context`, `…/connectivity-service[uuid=X]/opm`, `opm`) in ONCE,
POLL and STREAM/SAMPLE modes. Those paths, modes and the `GnmiConsumer` class
are gone:

| Before | Now |
|--------|-----|
| Subscribe to `…/connectivity-service[uuid=X]/opm` | Look up the service's oam-job (`GET …/tapi-oam:oam-context/oam-job`), subscribe to the whole stream, keep the records whose path is that job — `TapiStreamClient.follow(job_uuid=…)` |
| `json_ietf_val` with OPM keys | `proto_bytes` with `tapi.MeasurementDetails`; `decode_measurements()` maps it back to the same OPM keys |
| `sample_interval` chosen per subscription | One twin-wide cadence, `streaming.sample_interval_s` |
| ONCE / POLL, or subscribing to context or topology paths | Read them over RESTCONF |
| `GnmiConsumer(target)` | `TapiStreamClient(target, token)`, with the target from `discover_stream()` |
| `twinlight-client --gnmi-target` required | Optional; the address comes from the stream-context |
| Link-failed BER reported as 1.0 | Every measure `MISSING` (`None` once decoded) |

## Known gaps

See [TAPI_COMPLIANCE.md](TAPI_COMPLIANCE.md) for the full assessment against
T-API v2.6.0. The main gaps:

- The gNMI interface carries only the TR-548 measurement stream; entity and
  alarm streams, gNMI Get and Set are not implemented.
- Notification, fault and virtual-network T-API modules are not implemented,
  and OAM only as the read-only jobs that anchor the stream.
- No TLS on either interface, and the only authentication is the optional
  bearer token on gNMI — TwinLight is designed to run inside a lab network or
  a Compose stack, not exposed to the internet.
