# API reference

TwinLight exposes 75 REST operations plus a gNMI gRPC service carrying the
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

> **T-API is served under the RESTCONF root only.** The paths below use the
> default root, `/restconf`; a twin can be configured with another
> (`server.restconf_root`, see
> [CONFIGURATION.md](CONFIGURATION.md#server--listening-sockets)), and an empty root puts the data
> resource at `/data/…`. A client should not assume `/restconf`: RFC 8040 §3.1
> has it read the root from `GET /.well-known/host-meta`, which every bundled
> client does — see [Finding the RESTCONF root](#finding-the-restconf-root).

> **Every augment of the context is under the context.** `tapi-topology`,
> `tapi-connectivity`, `tapi-path-computation`, `tapi-equipment`, `tapi-oam`
> and `tapi-streaming` augment `/tapi-common:context`, so their RESTCONF paths
> are `/restconf/data/tapi-common:context/<module>:<container>/…` (RFC 8040 §3.5.3),
> and that is the only place each is served. Earlier releases also served the
> connectivity, path-computation and physical contexts at a short
> `/data/<module>:<container>/…`; those paths are gone — see
> [Migrating from earlier releases](#migrating-from-earlier-releases).

## Migrating from earlier releases

Each T-API resource has exactly one path, the one the v2.6.0 YANG gives it.
Paths earlier releases served anywhere else are removed, not redirected, and
answer `404` (`401` without a token when authentication is on):

| Removed path | Standard path |
|--------------|---------------|
| `/data/tapi-connectivity:connectivity-context/…` | `/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/…` |
| `/data/tapi-path-computation:path-computation-context/…` | `/restconf/data/tapi-common:context/tapi-path-computation:path-computation-context/…` |
| `/data/tapi-equipment:physical-context/…` | `/restconf/data/tapi-common:context/tapi-equipment:physical-context/…` |
| `/restconf/data/tapi-connectivity:connectivity-context/…` | `/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/…` |
| `/restconf/data/tapi-path-computation:path-computation-context/…` | `/restconf/data/tapi-common:context/tapi-path-computation:path-computation-context/…` |
| `/restconf/data/tapi-equipment:physical-context/…` | `/restconf/data/tapi-common:context/tapi-equipment:physical-context/…` |
| `/data/tapi-common:context` and every path below it (the bare mount) | `/restconf/data/tapi-common:context` and the same path below it |

The standard paths are given for the default root, `/restconf`; on a twin
configured with another, substitute it. Everything after the container is
unchanged, and so are request and response bodies. A twin configured with
`server.restconf_root: ""` serves the data resource at `/data/…` by
definition, so on such a twin only the short context paths change.

## Finding the RESTCONF root

The twin advertises its root in an RFC 6415 host-meta document, as RFC 8040
§3.1 specifies:

```
GET /.well-known/host-meta

<XRD xmlns='http://docs.oasis-open.org/ns/xri/xrd-1.0'>
    <Link rel='restconf' href='/restconf'/>
</XRD>
```

T-API is then at `{href}/data/…`. host-meta needs a token when authentication
is on, like every path except `/health`. A twin with an empty root serves no
host-meta (`404`); a twin from before the RESTCONF root did not either, and
both serve T-API at `/data/…`, so a `404` means the root is empty. Any other
error, a `401` included, is an error, not a missing root.

The bundled clients follow this rule and discover the root once per twin,
before their first T-API request: the web UI (once per base URL), the
`twinlight_client` library (`TapiClient`, once per client; pass
`restconf_root=` to pin it), the CLIs built on it, the examples, and the ONOS
adapter and its scripts (`TWIN_DATA_ROOT` pins the adapter's instead).

## Authentication

Authentication is off unless the twin was started with API tokens
(`TWINLIGHT_API_TOKENS`, see
[CONFIGURATION.md](CONFIGURATION.md#authentication--api-tokens)). With tokens,
every REST request and every gNMI RPC must present one as an
[RFC 6750](https://www.rfc-editor.org/rfc/rfc6750) bearer token. Both
interfaces accept the same tokens.

```bash
curl -H "Authorization: Bearer $TWINLIGHT_API_TOKEN" \
     http://localhost:8080/restconf/data/tapi-common:context
```

`GET /health` is the only public path, so that liveness probes (the Compose
healthcheck, load balancers) work without credentials. It is an exact match:
`/health/` and `/healthz` are protected. Everything else needs a token — the
T-API modules, the RESTCONF root and `/.well-known/host-meta`, `/internal/`,
`/admin/`, `/config/`, `/transparent/`, `/metrics`, the OpenAPI schema with
`/docs` and `/redoc`, and paths that do not exist (they answer 401, not 404).
A rejected request never reaches a route, so it has no side effect:

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="twinlight", error="invalid_token"
Content-Type: application/yang-data+json

{"ietf-restconf:errors": {"error": [{"error-type": "protocol",
  "error-tag": "access-denied",
  "error-message": "The bearer token is not valid for this twin."}]}}
```

`error="invalid_token"` appears only when a token was presented and not
recognised. A request with no credentials, or with another scheme such as
`Basic`, gets the bare `Bearer realm="twinlight"` challenge (RFC 6750 §3.1).
The scheme name is case-insensitive. CORS preflights never need a token, and
401 responses carry CORS headers so a browser client can read them.

**gNMI** carries the same credential as call metadata,
`authorization: Bearer <token>`. Without a valid one, every RPC, `Capabilities`
included, ends with `UNAUTHENTICATED` before the servicer runs, so a
`Subscribe` opens no stream.

**One check, in one place per interface.** REST requests pass through a single
ASGI middleware and gNMI calls through a single server interceptor, both
consulting the same token set. With no tokens configured, neither is installed:
an open twin runs no authentication code at all.

**Which token am I?** `GET /internal/whoami` returns the principal name a
request authenticated as:

```json
{"principal": "ui", "authentication": "bearer-token"}
```

With authentication off, it returns `{"principal": null, "authentication":
"disabled"}`. With authentication on, a request without a valid token gets 401,
which makes it a cheap credential check. The web UI uses it as its connection
probe: a `401` there is what makes it ask the user for a token (kept in that
browser tab's session storage until it is closed, never read from a file).

When tokens are configured, the OpenAPI schema declares the bearer scheme
(`components.securitySchemes.bearerAuth`) and marks `/health` `security: []`.
The schema and Swagger UI themselves need a token, so open them through a client
that sends one, or read the schema with `curl -H "Authorization: Bearer …"`.

> The twin does not terminate TLS, so a token crosses the network in clear
> text. Beyond a lab network, put a TLS-terminating proxy in front of both
> ports.

## T-API v2.6.0 endpoints

### Common context

| Method | Path |
|--------|------|
| GET | `/restconf/data/tapi-common:context` |
| GET | `/restconf/data/tapi-common:context/service-interface-point` |
| GET | `/restconf/data/tapi-common:context/service-interface-point={uuid}` |

The context is the northbound entry point. `GET /restconf/data/tapi-common:context`
returns the whole tree in one response, which is what a T-API client reads for
its initial sync: the service-interface-point (SIP) list and every augment of
the context the twin serves — `tapi-topology:topology-context`,
`tapi-connectivity:connectivity-context`,
`tapi-path-computation:path-computation-context`,
`tapi-equipment:physical-context`, `tapi-oam:oam-context` and
`tapi-streaming:stream-context`. Each section is exactly what that module's own
GET returns (built by the same function), so a client may read either. Each
SIP's `name` list carries the underlying GNPy element UID under the value-name
`node-name`, which is how SIPs map back to physical elements.

### Topology

| Method | Path |
|--------|------|
| GET | `/restconf/data/tapi-common:context/tapi-topology:topology-context` |
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
| GET | `/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context` |
| GET | `…/connectivity-service` |
| POST | `…/connectivity-service` |
| GET | `…/connectivity-service={uuid}` |
| PUT | `…/connectivity-service={uuid}` |
| PATCH | `…/connectivity-service={uuid}` |
| DELETE | `…/connectivity-service={uuid}` |

The container GET returns `{"tapi-connectivity:connectivity-context":
{"connectivity-service": […]}}`, the same section as the full context. The
list GET `…/connectivity-service` answers with that same container envelope
rather than a `tapi-connectivity:connectivity-service` array, as it always
has; the UI and the ONOS adapter read it that way.

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

A lightpath is never admitted without a QoT verdict while a backend is
loaded: a (route, format) whose propagation fails is skipped, with the error
named in the refusal, and admission moves on to the next. Admissions,
replacements and deletions are serialised, so concurrent requests never get
the same block. A `PUT` that is refused leaves the existing service exactly
as it was, on the same route and block; it is not re-admitted (which could
move it, or lose it when no route is left).

A lightpath is one-way: it runs from the end-point marked `direction: SOURCE`
to the one marked `SINK`, or, when neither says, from the first end-point to
the second. Reads report what was provisioned: the service is
`direction: UNIDIRECTIONAL` and `layer-protocol-name: PHOTONIC_MEDIA`, and the
end-points are listed `SOURCE` first, then `SINK`. Two `SOURCE` (or two `SINK`)
end-points are a `422`. An end-point `name` is kept and echoed.

Standard leaves the twin does not act on fail the create or PUT with `501`
(`operation-not-supported`). The error message names each one by path, so no
service is admitted while ignoring part of the request. A value the leaf's
YANG type does not admit (a `direction` of `"x"`) is a `422` instead. A
`BIDIRECTIONAL` direction is refused with a note on how to get both
directions: one service each way.

| Where | Refused when present | Refused unless |
|-------|----------------------|----------------|
| `connectivity-service` | `layer-protocol-qualifier`, `connectivity-constraint`, `resilience-constraint`, `connectivity-service`, `internal-point`, `connectivity-protection-service` | `layer-protocol-name` = `PHOTONIC_MEDIA`; `direction` = `UNIDIRECTIONAL` |
| `end-point` | `layer-protocol-qualifier`, `role`, `protection-role`, `capacity`, `administrative-state`, the `peer-fwd-`, `server-`, `protecting-` and `assembled-connectivity-service-end-point` references, `profile`, `sink-profile`, `source-profile` | `layer-protocol-name` = `PHOTONIC_MEDIA`; `direction` = `SOURCE` or `SINK` |
| `layer-protocol-constraint` | `layer-protocol-qualifier`, `name`, `tapi-photonic-media:otsi-mcg-connectivity-service-end-point-spec` | `layer-protocol-name` = `PHOTONIC_MEDIA` |
| `otsia-connectivity-service-end-point-spec` | `total-power-warn-threshold-upper`, `total-power-warn-threshold-lower` | `number-of-otsi` = `1` |
| `otsi-config` | `name`, `application-identifier`, `laser-control`, `otsi-threshold-power-config`, `power-management-config-pac` | — |

Config-false leaves (`operational-state`, `connection`, `csep-role`,
`connection-end-point`) belong to the server: they are ignored on input, so a
body read with GET can be sent back as a PUT. `lifecycle-state` is kept as
sent, and can be changed with `PATCH`.

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
agree. A malformed value, or one outside the −20 to +10 dBm the twin models
(−2000 to 1000), is a `422`.

`otsi-config/modulation` is optional too: an `otsi-config` carrying only a
central frequency or a launch power leaves the format to admission. A
`modulation` that is not an object (`null` included) is a `422`.

**Request checks.** A connectivity-service is a point-to-point lightpath:
anything but exactly two end-points is a `422`. A `POST` naming the UUID of
an existing service is a `409` (`resource-denied`, RFC 8040 §4.4.1) and
changes nothing; replace a service with `PUT`. A body of the wrong shape (a
string where an object belongs, an object where a list does) is a `422`,
never a `500`.

**Operational state.** `operational-state` reports whether the lightpath can
carry its format now: `DISABLED` while its route has a failed fiber or its
GSNR is below the format's required GSNR (T-API: "unable to meet the SLA of
the user"), `ENABLED` otherwise. The admission margin `rmsa.qot_margin_db` is
not part of the test, since a lightpath that has used up its margin still
works. The state follows the current QoT: a parameter write, a failure, or,
under the EGN backend, a lightpath lit or removed on a shared fiber changes
it. Under EGN a new lightpath is admitted on its own merits even when it
pushes a co-propagating one below threshold; that one then reads `DISABLED`.

### Path computation

| Method | Path |
|--------|------|
| GET | `/restconf/data/tapi-common:context/tapi-path-computation:path-computation-context` |
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
which are applied by searching a restricted view of the graph. A route through
includes is the exact shortest one (and the next k − 1), or none when none
exists. The search has a budget (3,000 steps, about 1.6 s on CORONET; 3 s at
most). If the budget runs out before the search has proven its answer, the
request is a `409` saying so. In that case a route may exist, and the twin
does not claim either way:

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
| GET | `/restconf/data/tapi-common:context/tapi-equipment:physical-context` |
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

### OAM jobs

| Method | Path |
|--------|------|
| GET | `/restconf/data/tapi-common:context/tapi-oam:oam-context` |
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
| GET | `/restconf/data/tapi-common:context/tapi-streaming:stream-context` |
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
`grpc://<FQDN>:<grpc_port>` when that is empty, resolved once at startup
(the `Measurement stream` log line shows it); see
[When the advertised address cannot be reached](#when-the-advertised-address-cannot-be-reached)
for when that default is wrong. `stream-state` is `ALIGNING`
from startup until the first sampling tick has been logged and `ACTIVE` from
then on (TR-548 ST-1.1). Both UUIDs are name-based and stable across restarts.

### Error format

Errors under `/restconf/data/` use the RESTCONF format of
[RFC 8040](https://www.rfc-editor.org/rfc/rfc8040), served as
`application/yang-data+json`. A rejected token uses the same body with
`error-tag: access-denied` (see [Authentication](#authentication)):

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
| 405 | `operation-not-supported` | A method the resource does not serve |
| 409 | `resource-denied` | Admission refused: no route, spectrum or QoT |
| 422 | `invalid-value` | A body that does not validate, or a constraint or value the twin cannot honour |
| 501 | `operation-not-supported` | A standard leaf the twin does not implement, or a value of one it supports at a single value (see [Connectivity services](#connectivity-services)) |

A 405 also carries an `Allow` header (RFC 9110 §15.5.6) naming every method
the resource serves at that path, on every path of the twin: T-API resources
under any `server.restconf_root` and the proprietary ones (`/internal`,
`/admin`, `/config`, `/transparent`, …) alike. `PATCH …/connectivity-service`,
for example, answers `Allow: GET, POST`. The header is computed from the
router's own matching (`AllowHeaderMiddleware`), so it is complete however
many routes share the path. `HEAD` and `OPTIONS` are listed only where a
route serves them, which today is nowhere: the T-API routes answer neither,
and CORS preflights are answered by the CORS middleware before routing. A
method a route accepts only to refuse it for that resource is left out —
`POST /transparent/data…`, and writes to the read-only `startup` and
`operational` datastores, whose `Allow` is `GET`. With authentication on, an
unauthenticated request gets 401 before routing, so it never sees a 405.

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
root path itself is `server.restconf_root`; setting it to `""` puts the data
resource at `/data/…` and omits these three resources.

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
| GET | `/internal/whoami` | The principal the request authenticated as; see [Authentication](#authentication) |

The OPM endpoints and the [gNMI measurement stream](#gnmi-streaming) share one
computation (`sample_service_opm`): at the same instant both report identical
values, the stream as T-API records and these as the twin's own JSON.

Every OPM figure is the service's own channel — at its assigned central
frequency and launch power — not the worst channel of the line. The rest of
the line is the equipment library's SI comb, fully loaded, under the GNPy
backend, and the lightpaths actually lit under the EGN backend (see
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
| `pre-fec-ber`, `q-factor-db` | Gaussian approximation for Gray-coded M-QAM at the GSNR. Q follows the GSNR until the BER underflows double precision (DP-QPSK: above ~31 dB GSNR, Q ≈ 31.7 dB) | — |
| `post-fec-ber-threshold` | Threshold model: `post_fec_ber_floor` (or the input, if lower) up to the code's pre-FEC BER limit; the uncorrected input beyond it | `fec.threshold` |
| `post-fec-ber-hd` | Bounded-distance decoding of a hard-decision block code — RS(255,239) by default | `fec.hard_decision` |
| `gmi-bits-per-symbol` | Generalized mutual information per polarization, bit per 2D symbol, in [0, bits per symbol] | — |
| `ngmi` | GMI ÷ bits per symbol, in [0, 1] — what a soft-decision FEC threshold is stated in | — |

A service with no signal — its path crosses a failed fiber (`"status":
"link-failed"`) or its channel lies outside the amplifier band (`"status":
"out-of-band"`) — reports every BER as 1.0 and the other fields as `null`. See [PHYSICS.md § 3](PHYSICS.md#3-from-gsnr-to-ber-q-post-fec-ber-and-gmi)
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
| POST | `/admin/restore` | Restore from a snapshot. Body: `{"path": "…"}`. Returns `restored`, `services` and `warnings` |
| GET | `/admin/snapshot/latest` | Path and mtime of the most recent snapshot |
| GET | `/admin/snapshots` | All snapshots, newest first |
| GET | `/admin/snapshot/content?path=<file>` | Contents of one snapshot; the path must resolve inside the snapshot directory |

Snapshots record which physical-layer backend produced them. Restoring under a
different backend is rejected rather than silently reinterpreted, because the
GSNR values would not be comparable.

At startup, `--restore <path>` or `--restore-latest` replays a snapshot before
the servers accept traffic.

The snapshot format is version 2. Version 1, written before spectrum
assignment was bounded by the amplifier band, is deprecated: it is restored
with a warning, and the file is then rewritten in place as version 2 (an
atomic replace, so the old file is never left half-written). A restored
service whose block lies outside the band its amplifiers pass keeps that
block but has no signal: its OPM reads `"status": "out-of-band"`, its
`operational-state` is `DISABLED`, and it is not a neighbour of any other
channel. Replace it with `PUT` to move it into the band. Each such service is
named in the restore's `warnings` (and logged at startup).

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
- `twinlight_service_enabled`: 1 while the service's `operational-state` is
  `ENABLED`.
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
With API tokens configured, every RPC needs `authorization: Bearer <token>`
metadata; see [Authentication](#authentication).

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

A service with no signal — its path crosses a failed fiber (`link-failed` in
`GET /internal/opm`) or its channel lies outside the amplifier band
(`out-of-band`) — reports every measure with `value-qualifier: MISSING` and no
value or units, including the BERs that the REST view shows as 1.0 for
charting. A sample the twin could not compute is
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

### Consuming it

```bash
twinlight-client                                   # REST on localhost:8080, stream address from discovery
twinlight-client --rest-url http://host:8080       # another twin, stream address from discovery
twinlight-client --gnmi-target localhost:50051     # override the advertised stream address
TWINLIGHT_API_TOKEN=<token> twinlight-client       # a twin with tokens
```

`twinlight-client` lists the services, discovers the stream and the chosen
service's oam-job over RESTCONF, and prints that job's records, resuming
automatically after a dropped connection. The one token serves the REST
lookups and the stream; `--token` overrides `TWINLIGHT_API_TOKEN` (environment
or `./.env`).

#### When the advertised address cannot be reached

The client connects to the `connection-address` the twin advertises
([Streaming discovery](#streaming-discovery)) unless `--gnmi-target` overrides
it. The default, `grpc://<FQDN>:<grpc_port>`, is not always reachable from where
the client runs:

- **Compose stack, client on the host**: the address is `grpc://twin:50051`,
  a name that resolves only inside the Compose network. Pass
  `--gnmi-target localhost:50051` (the published port).
- **Twin run natively, on a host whose name does not resolve** (common on a
  laptop, macOS included): pass `--gnmi-target localhost:50051`, or set
  `streaming.advertised_address` on the twin
  ([CONFIGURATION.md](CONFIGURATION.md#streaming--the-t-api-measurement-stream)).
- **NAT or a port mapping**: pass the outside `host:port`, or advertise it.

A stream that cannot be reached at the first connection is an error, not a
dropped connection: the client stops at once, exits `1`, and prints the address
it tried, where that address came from, and the gRPC status:

```text
Cannot reach the measurement stream at twin:50051, the connection-address the twin advertises (grpc://twin:50051): UNAVAILABLE: errors resolving twin:50051: […: Domain name not found]
Pass --gnmi-target HOST:PORT to connect elsewhere, or set streaming.advertised_address on the twin to an address clients can reach.
```

With `--gnmi-target`, the message names that target instead (`… at
localhost:50051 (from --gnmi-target): UNAVAILABLE: …`). Only once the twin has
answered the subscription (it sends a `sync_response` at once, records or not)
is a lost connection retried, every second, resuming from the last record
without loss or duplicates. The walkthrough below fails the same way at its
first gNMI call.

Programmatically:

```python
from twinlight_client import TapiClient, TapiStreamClient
from twinlight_client.streaming import discover_stream, oam_job_for_service

async with TapiClient("http://host:8080", token=token) as rest:
    endpoint = await discover_stream(rest)            # ST-0.2
    job = await oam_job_for_service(rest, service_uuid)
async with TapiStreamClient(endpoint.target, token) as stream:
    async for record in stream.follow(job_uuid=job):  # ST-6.2 + ST-6.4
        print(record.sampled_ms, record.measurements["gsnr-db"])
```

`follow()` raises `StreamUnreachableError` (with `target` and the underlying
`rpc_error`) when the stream cannot be reached at all; `unreachable_message()`
turns it into the text above. Its docstring lists what is and is not retried.

`record.measurements` is keyed by the OPM keys of `GET /internal/opm`, with
`None` for a MISSING measure; `record.details` is the decoded
`tapi.MeasurementDetails`. Any other gNMI client must send the token as plain
call metadata: gRPC's own call credentials refuse to travel over an insecure
channel, and the twin serves no other.

### A walkthrough of every feature

[`examples/tapi_stream_walkthrough.py`](../examples/tapi_stream_walkthrough.py)
does each TR-548 step in the open and prints what it sends and receives:
credentials (`/internal/whoami`), RESTCONF and stream discovery, the oam-jobs and
the services they measure, Capabilities, the calls the twin refuses (no token, a
non-§6.3.4 subscription), a subscription that replays the last seconds of the log
before its `sync_response`, one record decoded field by field (normalized and
native types, units, value qualifiers), and a deliberate disconnect followed by
an ST-6.4 resume that recovers the records logged meanwhile. `--fail-link` cuts a
fiber on the service's path to show every measure turn `MISSING`, then restores
it; `--follow` ends in `TapiStreamClient.follow`, the library's resume loop.

```bash
python examples/demo_services.py                      # a few services to measure
python examples/tapi_stream_walkthrough.py --fail-link --follow
python examples/tapi_stream_walkthrough.py --gnmi-target localhost:50051  # Compose, from the host
python examples/tapi_stream_walkthrough.py --rest-url http://host:8080 \
    --gnmi-target host:50051 --history 30             # behind a port mapping
```

Steps 5–10 use the raw gNMI stub rather than the library, so the protocol is
visible; a test runs the whole script against a live twin with tokens.

### Testing with gnmic

[gnmic](https://gnmic.openconfig.net) (OpenConfig) is a general-purpose gNMI
client, useful for checking the stream without any TwinLight code. Version
0.49.1 has been tried against the twin; it runs from its container image:

```bash
gnmic() { docker run --rm ghcr.io/openconfig/gnmic:0.49.1 \
  -a host.docker.internal:50051 --insecure \
  -H "authorization=Bearer $TWINLIGHT_API_TOKEN" "$@"; }   # drop -H when auth is off

gnmic capabilities
gnmic -e proto subscribe --target tapi --path / --mode stream \
  --stream-mode target-defined --updates-only                # the §6.3.4 subscription
gnmic -e proto subscribe --target tapi --path / --stream-mode target-defined \
  --updates-only --history-start $(( $(date +%s) - 60 ))000000000   # replay the last minute
```

It prints a `sync-response`, then one update per oam-job with the
`MeasurementDetails` as base64 `proto_bytes`. Three things to know:

- Send the token with `-H "authorization=Bearer …"`. gnmic's `--token` uses gRPC
  call credentials, which are never sent over a plaintext channel.
- `--proto-file` does not decode the payload (gnmic expects a Nokia root
  message). Decode one with the vendored `.proto` and the `protoc` bundled in
  `grpcio-tools`:

  ```bash
  echo '<proto_bytes value>' | base64 -d | python -m grpc_tools.protoc \
      -I src/twinlight/streaming/proto --decode=tapi.MeasurementDetails \
      tapi-streaming-performance.proto
  ```

- A rejected subscription (for example without `--updates-only`) appears only in
  gnmic's log; add `-d` to see `InvalidArgument: subscribe.updates_only: must be
  true (TR-548 §6.3.4)`.

`host.docker.internal` reaches a twin on the host from Docker Desktop. Inside
the Compose network, run the container with `--network <project>_default` and
use `twin:50051` instead.

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
| `streaming.auth_token` (gNMI only) | The twin-wide `TWINLIGHT_API_TOKENS`, required on REST and gNMI alike |
| `twinlight-client --gnmi-target HOST:PORT`, default `localhost:50051` | Optional, no default: the address comes from the stream-context. Pass it when that address is not reachable from the client (a Compose stack seen from the host, a host name that does not resolve); the client then stops at the first connection and says so ([details](#when-the-advertised-address-cannot-be-reached)) |
| Link-failed BER reported as 1.0 | Every measure `MISSING` (`None` once decoded), for a link-failed or out-of-band service alike |

## Known gaps

See [TAPI_COMPLIANCE.md](TAPI_COMPLIANCE.md) for the full assessment against
T-API v2.6.0. The main gaps:

- The gNMI interface carries only the TR-548 measurement stream; entity and
  alarm streams, gNMI Get and Set are not implemented.
- Notification, fault and virtual-network T-API modules are not implemented,
  and OAM only as the read-only jobs that anchor the stream.
- No TLS on either interface, and authentication is all-or-nothing: any
  accepted token may do anything, `/admin/` and `/config/` included. TwinLight
  is designed for a lab network or a Compose stack, not the open internet.
