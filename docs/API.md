# API reference

TwinLight exposes 65 REST operations plus a gNMI gRPC service. This document is
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
(SIP) list and a reference to the topology context. Each SIP's `name` list
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

Links carry a `latency-characteristic` (`traffic-property-name:
propagation-delay`) whose `fixed-latency-characteristic` is the propagation
delay derived from fiber length and the speed of light in silica. v2.6.0 types
that leaf as a string with no unit; the twin writes **microseconds** — the unit
`tapi-path-computation` uses for `max-allowed-delay` — with three decimals,
e.g. `"392.110"` for 80 km.

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
accepts `MT_DP-QPSK`, `MT_DP-QAM16` or `MT_DP-QAM64`; the module-prefixed identityref spelling is accepted too
(RFC 7951 §6.8). A bare top-level `modulation-format` key is rejected with a
`422` naming the standard location, rather than silently defaulting.

The body may be a single object, as above, or a one-entry JSON array — RFC 7951
§5.4 encodes a list entry as an array, and that is the form some controllers
send. An array of more than one entry is a `400`.

Creation runs the full RMSA admission path. The `rmsa.k_shortest_paths`
candidate routes are tried in rank order; on each, the requested format — or,
when the request names none, every format from the highest spectral efficiency
down (DP-64QAM, DP-16QAM, DP-QPSK) — gets a first-fit spectrum block and a QoT
check against the format's required GSNR plus `rmsa.qot_margin_db`. The first
(route, format, block) that passes is admitted, and the end-points then report
the format in effect. Without a physics backend there is no QoT to check, so a
request that names no format gets DP-QPSK. A request that cannot be satisfied
is a `409` listing every attempt, rather than an admission with a warning.

The standard `connectivity-constraint` and `resilience-constraint` containers
are not implemented: a create or PUT carrying either fails with `501`
(`operation-not-supported`) naming them, rather than admitting a service that
ignores them.

A service may carry the standard `routing-constraint` container and
`topology-constraint` list; they constrain the route exactly as on a
path-comp-service (see [Route constraints](#route-constraints)) and are echoed
on reads. An unsupported constraint is a `422` naming the leaf; a constraint no
route satisfies is a `409`.

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
          "central-frequency": 191306250000000,
          "modulation": { "standard-modulation-technique": "MT_DP-QPSK" } }
      ],
      "number-of-otsi": 1
    },
    "tapi-photonic-media:mcg-connectivity-service-end-point-spec": {
      "number-of-mc": 1,
      "mc-spectrum-config-pac": [
        { "local-id": "1",
          "spectrum": {
            "lower-frequency": 191278125000000,
            "upper-frequency": 191334375000000
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
all, rather than a zeroed one. The `spectrum` is the media channel — the
channel's slots plus `rmsa.default_guardband_slots` — and `central-frequency`
is its centre, where the carrier sits. `channel-output-power` is the launch
power in effect, in the leaf's YANG unit of **0.01 dBm** (`0` = 0 dBm): the
requested value, or the equipment library's SI `power_dbm` when none was
requested.

**Requesting spectrum.** Both leaves are optional on create (and on PUT):
send `mc-spectrum-config-pac/spectrum`, `otsi-config/central-frequency`, or
both, on one or both end-points (they must agree). The request must be on the
grid — edges on slot edges, a central frequency at the centre of a block — and
exactly one media channel wide for the format, the width a GET reports, so a
fetched service can be sent back unchanged; numbers or RFC 7951 decimal
strings are accepted. An `edge-frequency-constraint`, if sent, must be this
twin's grid. These are `422`s naming the problem, as are
`mc-bandwidth-config-pac`, `mc-flexi-grid-config-pac`, more than one media
channel, and end-points that disagree. On each candidate route the requested
block must lie in the band every amplifier on the route passes and be free on
every link; otherwise the next candidate is tried, and if none fits the
request is a `409` naming each conflict — the band, or the service holding
the spectrum and where.

Without a request, the block is first-fit, restricted to slots inside the band
the route's amplifiers pass — GNPy's `find_elements_common_range`, slot by
slot with GNPy's `is_in_band` test, since an EDFA drops any carrier outside
it. The example above is the first such block under GNPy's default EDFA band
(from 191.275 THz). Without a physics backend there is no amplifier model and
the whole grid is assignable.

**Requesting launch power.** `otsi-config/channel-output-power` is optional,
in 0.01 dBm, as a number or decimal string; end-points that carry it must
agree. A malformed value is a `422`.

### Path computation

| Method | Path |
|--------|------|
| GET | `/data/tapi-path-computation:path-computation-context` |
| GET | `…/path-comp-service` |
| POST | `…/path-comp-service` |
| GET | `…/path-comp-service={uuid}` |
| DELETE | `…/path-comp-service={uuid}` |
| GET | `…/path` |
| GET | `…/path={uuid}` |

T-API v2.6.0 has no path-computation RPC: computation is a resource. Creating
a `path-comp-service` computes up to `rmsa.k_shortest_paths` routes, shortest
fiber length first (GNPy's ranking, see
[ARCHITECTURE.md](ARCHITECTURE.md#creating-a-connectivity-service)), stores each as a `path` in the context and lists them in the service's
`path` references. Nothing is allocated. Services and paths are what-if
results: they are not part of snapshots, and a path keeps the route it had when
it was computed.

```json
{
  "tapi-path-computation:path-comp-service": [{
    "end-point": [
      {"local-id": "a", "service-interface-point": {"service-interface-point-uuid": "…"}},
      {"local-id": "z", "service-interface-point": {"service-interface-point-uuid": "…"}}
    ],
    "routing-constraint": {"route-objective-function": "MIN_WORK_ROUTE_LATENCY"},
    "topology-constraint": [
      {"local-id": "1", "exclude-node": [{"topology-uuid": "…", "node-uuid": "…"}]}
    ]
  }]
}
```

Each `path` carries its `link` and `node-edge-point` references in order and
its fiber length as a `routing-constraint/cost-characteristic` named
`fiber-length-km`. Paths are computed `UNIDIRECTIONAL`, from the first
end-point to the second; `direction` defaults to that and other values are
refused.

#### Route constraints

The same subset applies to `path-comp-service`, `connectivity-service` and
`/internal/compute-path`. It is what GNPy's
`gnpy.topology.request.compute_constrained_path` can express, plus exclusions,
which are applied by searching a restricted view of the graph:

| Leaf | Supported |
|------|-----------|
| `topology-constraint/include-node`, `include-link` | Yes — visited in the order given, all strict (GNPy `nodes_list`/`loose_list`). One of the two lists, in one entry |
| `topology-constraint/exclude-node`, `exclude-link` | Yes — merged across entries; excluding an end-point's own node is refused |
| `topology-constraint/explicit-route` | Yes — the includes must name every hop between the end-points' ROADMs; the route is not completed for you |
| `routing-constraint/route-objective-function` | `MIN_WORK_ROUTE_LATENCY` only — routes are ranked by fiber length, and link latency is fiber length over a constant group velocity |
| `routing-constraint/is-exclusive` | `true` only |
| everything else (diversity, max-allowed-cost/hops/delay, cost/latency/risk characteristics, include/exclude topology, path and NEP, partitions, path sets) | Refused with a `422` naming the leaf |

The ranked view with a candidate count, which T-API has no leaf for, is
[`POST /internal/compute-path`](#internal--observation-and-metadata).

### Physical inventory and spectrum

| Method | Path |
|--------|------|
| GET | `/data/tapi-equipment:physical-context` |
| GET | `…/device` |
| GET | `…/device={uuid}` |
| GET | `…/device={uuid}/equipment={uuid}` |
| GET | `…/physical-span` |
| GET | `…/physical-span={uuid}` |

T-API v2.6.0 has no `equipment-context`: `tapi-equipment` augments the context
with `physical-context`, a list of `device` (each holding `equipment`) and a
list of `physical-span`. Every ROADM, transceiver and amplifier — including
the boosters and preamplifiers GNPy's design inserts — is a device holding one
equipment, whose `actual-equipment/common-equipment-properties` carries the
GNPy kind as `equipment-type-name` and its `type_variety` as
`equipment-type-identifier`. Every fiber is a physical span. UUIDs are derived
from the element UID, which each object keeps in its `name` list as
`element-uid`.

```json
{
  "uuid": "…",
  "name": [{"value-name": "element-uid", "value": "east edfa in Brest to Morlaix"}],
  "equipment": [{
    "uuid": "…",
    "name": [{"value-name": "element-uid", "value": "east edfa in Brest to Morlaix"}],
    "actual-equipment": {"common-equipment-properties": {
      "equipment-type-name": "Edfa", "equipment-type-identifier": "std_medium_gain"
    }}
  }]
}
```

Element attributes T-API has no leaf for — a fiber's length among them — are
not in this tree; read them from the transparent controller,
`GET /transparent/data/ElementList`.

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

| HTTP status | `error-tag` | Used for |
|-------------|-------------|----------|
| 400 | `bad-element` | A malformed envelope (e.g. a list of more than one entry) |
| 404 | `data-missing` | An unknown resource |
| 409 | `resource-denied` | Admission refused: no route, spectrum or QoT |
| 422 | `invalid-value` | A body that does not validate, or a constraint or value the twin cannot honour |
| 501 | `operation-not-supported` | A standard container the twin does not implement (`connectivity-constraint`, `resilience-constraint`) |

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
| GET | `/internal/services/{service_uuid}` | Name, modulation format, central frequency, launch power, path hops, total fiber km |
| GET | `/internal/links` | Link inventory with per-link state, used by the UI |
| GET | `/internal/spectrum-grid` | Per-link slot occupancy, used by the UI heat map |
| GET | `/internal/spectrum-context` | Grid parameters: `num-slots`, `slot-width-ghz`, `nominal-central-frequency-thz` |
| GET | `/internal/path-info?sip_a=&sip_z=&modulation=` | Path hops and a QoT estimate without creating a service, evaluated on the channel admission would assign (reported as `central-frequency-thz` and `launch-power-dbm`) |
| POST | `/internal/compute-path` | Up to `max-candidates` (1–10) routes ranked by fiber length between two SIPs, each with its T-API links, nodes and `fiber-length-km`; same constraints as a path-comp-service |
| GET | `/internal/services/{service_uuid}/eye-diagram` | Synthesised eye-diagram traces |
| GET | `/internal/services/{service_uuid}/constellation` | Synthesised constellation points |

Every OPM figure is the service's own channel — at its assigned central
frequency and launch power, on a line fully loaded with the equipment
library's SI comb — not the worst channel of the line (see
[PHYSICS.md § 1](PHYSICS.md#1-qot-baseline)).

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

## gNMI / gRPC interface

The gRPC server listens on port 50051 (`server.grpc_port`).

| RPC | Status |
|-----|--------|
| `Capabilities` | Returns the tapi-common and tapi-topology model data with JSON_IETF encoding |
| `Subscribe` ONCE | Fetches every subscribed path once, then `sync_response=True` |
| `Subscribe` STREAM | Initial sync, then re-polls at `sample_interval` (default 10 s) |
| `Subscribe` POLL | Like ONCE; each poll message re-fetches |
| `Get` / `Set` | Not implemented — returns gRPC `UNIMPLEMENTED` |

### Path mapping

gNMI path elements are joined with `/`, looked up in a static map first, and
otherwise fall back to `/data/<joined>`:

```
tapi-common:context                       → /data/tapi-common:context
tapi-common:context/tapi-topology:topology-context
                                          → /data/tapi-common:context/tapi-topology:topology-context
topology[uuid=X]                          → topology=X       (key → =value)
…/connectivity-service[uuid=X]/opm        → /internal/opm/X   (resolved dynamically)
opm                                       → /internal/opm
```

OPM paths are resolved dynamically rather than through the static map, because
they carry a per-service UUID. Any path ending in an `opm` element maps to the
transient-aware OPM endpoint, so each gNMI sample reflects the live signal
quality rather than a cached baseline.

### Consuming it

```bash
twinlight-client                                              # localhost defaults
twinlight-client --rest-url http://host:8080 --gnmi-target host:50051
```

Programmatically, `twinlight_client.streaming.GnmiConsumer` wraps the subscribe
loop, and `twinlight_client.client.TapiClient` wraps the REST surface.

## Known gaps

See [TAPI_COMPLIANCE.md](TAPI_COMPLIANCE.md) for the full assessment against
T-API v2.6.0. The main gaps:

- gNMI subscriptions for connectivity-service objects fall back to
  `/data/<joined>`; only the OPM and topology paths have first-class handling.
- Notification, OAM and virtual-network T-API modules are not implemented.
- No authentication or TLS on either interface — TwinLight is designed to run
  inside a lab network or a Compose stack, not exposed to the internet.
