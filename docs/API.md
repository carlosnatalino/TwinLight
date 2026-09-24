# API reference

TwinLight exposes 40 REST operations plus a gNMI gRPC service. This document is
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
> `/data/…` mount is kept for clients written against earlier releases;
> retiring it is tracked in [PENDING.md](PENDING.md).

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
    "modulation-format": "DP-QPSK",
    "end-point": [
      {"local-id": "a-end", "service-interface-point": {"service-interface-point-uuid": "<SIP_A_UUID>"}},
      {"local-id": "z-end", "service-interface-point": {"service-interface-point-uuid": "<SIP_Z_UUID>"}}
    ]
  }
}
```

`modulation-format` accepts `DP-QPSK` (default), `DP-16QAM` or `DP-64QAM`.

Creation runs the full RMSA admission path — route, spectrum first-fit, QoT
check against the format's required GSNR plus `rmsa.qot_margin_db`. A request
that cannot be satisfied is refused rather than admitted with a warning.

**Semantics of the mutating verbs:**

| Verb | Behaviour |
|------|-----------|
| PUT | Full replacement at the given UUID; the body must be a complete connectivity-service and its UUID must match the path |
| PATCH | Partial update of `name`, `administrative-state`, `lifecycle-state` only — **not** `modulation-format`, which would invalidate the admission decision |
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
served by the Common and Connectivity routers. The grid parameters that used to
be published as `tapi-photonic-media:spectrum-context` are twin configuration
rather than T-API and now live at
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
