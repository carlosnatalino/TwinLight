# API reference

TwinLight exposes 48 REST operations plus a gNMI gRPC service. This document is
the catalogue; a live, interactive version is served at
<http://localhost:8080/docs> (OpenAPI/Swagger) and <http://localhost:8080/redoc>
whenever the twin is running.

Endpoints fall into two groups, kept strictly apart (see
[ARCHITECTURE.md](ARCHITECTURE.md#2-t-api-surfaces-stay-standard)):

- **`/restconf/data/…`** — T-API v2.6.0 only. Standard paths, hyphenated JSON
  keys, RESTCONF errors, `application/yang-data+json` responses.
- **`/internal/`, `/admin/`, `/config/`, `/metrics`** — everything the standard
  does not cover.

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

**OPM response — all services** (`GET /internal/opm`) wraps a list:

```json
{
  "services": [
    {
      "service-uuid": "…",
      "timestamp": 1234567890.123,
      "measurements": {
        "osnr-db": 30.2,
        "osnr-01nm-db": 34.3,
        "gsnr-db": 27.4,
        "pre-fec-ber": 2.6e-26,
        "q-factor-db": 19.3,
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
    "osnr-db": 30.2,
    "osnr-01nm-db": 34.3,
    "gsnr-db": 27.4,
    "pre-fec-ber": 2.6e-26,
    "q-factor-db": 19.3,
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
| POST | `/config/set` | Change a parameter (fiber loss, EDFA noise figure, link failure, RMSA margin, transient enables) |
| GET | `/config/devices/{uid}` | Per-element parameter values for one network element |

This is the fault-injection and sensitivity-analysis plane: raise a span's loss
coefficient or fail a fiber, and every affected service's cached baseline is
invalidated so the next OPM read reflects the change. It is what makes
before/after experiments possible without restarting the twin.

### `/config/` — soft failures

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/config/parameters?path=…` | Expand a [parameter path](#parameter-paths) into the parameters it addresses, with current values and bounds. Default `/*/*/*` lists everything mutable |
| GET | `/config/soft-failures` | All soft-failure jobs and their progress |
| POST | `/config/soft-failures` | Start one; `?dry_run=true` validates and returns the plan without starting it |
| GET | `/config/soft-failures/{id}` | One job: state, progress, per-target start/end/current values, affected services |
| POST | `/config/soft-failures/{id}/pause` | Freeze the ramp; the job's clock stops |
| POST | `/config/soft-failures/{id}/resume` | Continue from where it paused |
| POST | `/config/soft-failures/{id}/stop` | End the job; parameters keep the values they reached |
| DELETE | `/config/soft-failures/{id}?revert=…` | Remove the job; with `revert=true`, write every target's start value back first |

A *hard* failure is a step: `failed: true` on a fiber. A *soft* failure is a
slow degradation that leaves the lightpath up while its margin erodes — a span
whose loss creeps up as a connector contaminates, an amplifier whose gain sags as
its pump ages. A soft-failure job ramps one attribute on one or more elements
from its current value towards a target, on a schedule, so that degradation
detection and localisation can be rehearsed against the twin.

Every write goes through the same validated path as `POST /config/set`, so the
allow-list bounds apply, `/config/get` reports the values as overrides, and the
affected services' baselines are invalidated after each write. The next OPM
read, gNMI sample or Prometheus scrape therefore reflects the ramp.

The twin runs the jobs in a background task; a client only starts and inspects
them. The `twinlight-soft-failure` command wraps these endpoints, with
`h`/`m`/`s` durations, per-minute and per-hour rates, and YAML scenario files —
see [examples/soft_failure_scenario.yaml](../examples/soft_failure_scenario.yaml).

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
| `/Link/roadm Brest/roadm Morlaix/Edfa/*/nf0` | Noise figure of every amplifier on the Brest → Morlaix link, boosters included |
| `/Service/my-service/Fiber/*/loss_coef` | Every span carrying the service named `my-service` |

Naming a kind explicitly with an attribute it does not have (`/Edfa/*/loss_coef`)
is rejected rather than treated as an empty match. Every parameter in a
`GET /config/parameters` response carries a fully qualified `path` naming just
that parameter.

#### Request body

Exactly one of `to` / `by` / `by_percent`, and exactly one of `duration_s` /
`rate`, are required.

| Field | Default | Meaning |
|-------|---------|---------|
| `path` | — | Parameter path; must name a single attribute |
| `to` | — | Absolute end value for every target |
| `by` | — | Additive change from each target's own start value (signed) |
| `by_percent` | — | Relative change from each target's start value, in % (signed) |
| `direction` | — | `increase` / `decrease`: treat `by`/`by_percent` as a magnitude in that direction |
| `duration_s` | — | Ramp duration |
| `rate` | — | Average speed in attribute units per second; each target's duration is \|end − start\| / rate |
| `profile` | `linear` | `linear`, `accelerating` (exponential, slow then fast), `saturating` (fast then levelling off), `sigmoid`, or `step` |
| `shape` | `5.0` | Steepness of `accelerating`, `saturating` and `sigmoid` |
| `steps` | `5` | Number of equal increments of `step` |
| `interval_s` | `1.0` | Seconds between writes (minimum 0.05). Each write re-propagates the affected services on their next read |
| `start_delay_s` | `0` | Wait before the ramp starts; the job reports `scheduled` meanwhile |
| `on_complete` | `hold` | `hold` the end value, `revert` to the start value, or `reverse` back along the same trajectory over the same duration |
| `out_of_bounds` | `reject` | `reject` a job whose end value leaves the attribute's allowed range, or `clamp` it to the bound |
| `name` | — | Free-form label |

```json
POST /config/soft-failures
{
  "name": "pump ageing",
  "path": "/Edfa/Edfa1/gain_target",
  "by": 3, "direction": "decrease",
  "rate": 0.0083,
  "profile": "accelerating",
  "on_complete": "reverse"
}
```

The profiles are scenario shapes, not physical models of a particular
degradation mechanism; each is monotone and reaches the end value exactly, so the
bounds check on the end value also bounds every intermediate write.

Jobs are `planned` (dry run), `scheduled`, `running`, `paused`, `completed`,
`stopped` or `failed`, and stay listed after they finish until deleted. Two active jobs cannot drive the same attribute of the
same element (`409`). A job whose write fails — for example, a concurrent
`/config/set` pushes the parameter where the next step would be out of range —
moves to `failed` with an `error`, leaving other jobs running. When a job returns
a parameter to its start value (`revert`, `reverse`, or `DELETE ?revert=true`),
the override record it created is removed too, so `/config/get` reads as before.

Jobs live in memory: snapshots record the parameter values a job has reached, but
not the job itself, and a restore cancels every job.

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
  `twinlight_opm_pre_fec_ber`,
  `twinlight_opm_chromatic_dispersion_ps_per_nm`, `twinlight_opm_pmd_ps`,
  each labelled with `service_uuid`, `service_name` and `modulation`.
  The two OSNR gauges are one measurement on two reference bandwidths.
- Per-element `/config` values: `twinlight_fiber_loss_coef_db_per_km`,
  `twinlight_edfa_nf_db`, `twinlight_edfa_gain_target_db`,
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
