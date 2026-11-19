# T-API v2.6 compliance: current implementation and gaps

This document evaluates TwinLight's RESTCONF and gNMI interfaces against a **fully compliant T-API v2.6.0** implementation. It summarizes what is implemented, then lists gaps by category.

Target audience: integrators, test suites, and contributors who need to know where the twin is spec-compliant and where it is not.

For the endpoint catalogue itself see [API.md](API.md).

---

## 1. What Is Implemented (T-API–aligned)

### 1.1 TAPI Common

| Aspect | Status | Notes |
|--------|--------|------|
| **Context** | ✓ | `GET /restconf/data/tapi-common:context` returns the whole context: the SIP list and the topology, connectivity, path-computation, physical, OAM and stream contexts, each identical to its own module's GET |
| **Service interface points** | ✓ | `GET .../service-interface-point` (`{"tapi-common:service-interface-point": […]}`), `GET .../service-interface-point={uuid}` (the entry, an array of one); each SIP carries the `tapi-photonic-media:photonic-media-service-interface-point-spec` augment |
| **Path & JSON keys** | ✓ | Hyphenated keys, `tapi-common:context` wrapper |
| **Content-Type** | ✓ | Responses under `{restconf-root}/data/` use `application/yang-data+json` (RESTCONF) |

### 1.2 TAPI Topology

| Aspect | Status | Notes |
|--------|--------|------|
| **Topology context** | ✓ | `GET .../tapi-topology:topology-context` |
| **Topology list / single** | ✓ | `GET .../topology={uuid}` |
| **Node** | ✓ | `GET .../topology={uuid}/node={node_uuid}` |
| **Link** | ✓ | `GET .../topology={uuid}/link={link_uuid}` |
| **Link latency-characteristic** | ✓ | Propagation delay from fiber length / speed of light in fiber, in `fixed-latency-characteristic` (a unitless string in v2.6.0; the twin writes microseconds) |
| **Owned node-edge-point (NEP)** | ✓ | `GET .../topology={uuid}/node={node_uuid}/owned-node-edge-point={nep_uuid}` |
| **Data model** | ✓ | Topology, Node, Link, NodeEdgePoint with layer-protocol-name, states, NEP refs |

### 1.3 TAPI Connectivity

| Aspect | Status | Notes |
|--------|--------|------|
| **Connectivity context** | ✓ | `GET /restconf/data/tapi-common:context/tapi-connectivity:connectivity-context`, the augment's RESTCONF path; the same section as in the full-context GET |
| **Create service** | ✓ | `POST .../tapi-connectivity:connectivity-context`, the list's parent (RFC 8040 §4.4.1), with the new `connectivity-service` (end-points with SIP refs and the photonic modulation augment) as the body; `201` with a `Location` naming the entry. The list path takes no `POST` (`405`) |
| **List / get service** | ✓ | `GET .../connectivity-service`, `GET .../connectivity-service={uuid}`. The list GET answers with the list as its one top-level member, `{"tapi-connectivity:connectivity-service": [...]}` (RFC 8040 §4.3, RFC 7951 §4); earlier releases answered with the container's envelope, which the connectivity-context GET still returns |
| **Replace service** | ✓ | `PUT .../connectivity-service={uuid}` (full replacement, `204`; creation, `201`; body UUID must match path) |
| **Replace every service** | ✓ | `PUT .../tapi-connectivity:connectivity-context` (RFC 8040 §4.5): the body is the whole new list; services it omits are deleted, new or changed ones admitted, unchanged ones kept; all or nothing; `204` |
| **Update service** | ✓ | `PATCH .../connectivity-service={uuid}` (a plain patch of name, administrative-state, lifecycle-state; `200` with the entry) |
| **Delete service** | ✓ | `DELETE .../connectivity-service={uuid}` (204) |
| **Conflict handling** | ✓ | 409 for no path, insufficient spectrum, or insufficient QoT |
| **End-point model** | ✓ | local-id, service-interface-point (SIP ref), direction |

### 1.4 Protocol and conventions

- **RESTCONF root**: The T-API modules are served under `server.restconf_root` (default `/restconf`), discoverable via `GET /.well-known/host-meta` (XRD, RFC 6415) as RFC 8040 §3.1 requires, and only there: one path per resource. The bare `/data/...` earlier releases also served is gone (with an empty root, `/data/...` is the data resource itself).
- **Capability discovery**: `GET {restconf-root}`, `{restconf-root}/yang-library-version` and `{restconf-root}/operations` answer per RFC 8040 §3.3; `GET {restconf-root}/data` is the datastore, and `ietf-yang-library:yang-library`, `ietf-yang-library:modules-state` and `ietf-restconf-monitoring:restconf-state` under it list the modules and capabilities (§9, §10). See §2.7 for the full matrix.
- **URL path encoding**: Colons in paths handled (PathDecodeMiddleware for `%3A` → `:`).
- **RESTCONF-style paths**: Resource paths follow the `.../connectivity-service={uuid}` pattern.
- **Augment paths**: Every context augment is served under the context, at its RFC 8040 §3.5.3 path, with a module prefix only where the module changes: `/restconf/data/tapi-common:context/tapi-topology:topology-context`, `…/tapi-connectivity:connectivity-context`, `…/tapi-path-computation:path-computation-context`, `…/tapi-equipment:physical-context`, `…/tapi-oam:oam-context`, `…/tapi-streaming:stream-context`. Each is served there only; the short `/data/<module>:<container>/…` earlier releases also served for the connectivity, path-computation and physical contexts is gone.
- **Create by POST on the parent**: RFC 8040 §4.4.1 — a `connectivity-service` is created on `…/tapi-connectivity:connectivity-context` and a `path-comp-service` on `…/tapi-path-computation:path-computation-context`, never on the list path, which answers `405` to a `POST`. The `201` carries a `Location` with the entry's data-resource path under the configured root, key percent-encoded (§3.5.3), as a path-absolute reference. It also carries a body; see §2.2.
- **List encoding**: POST accepts a list entry as a single-element JSON array (RFC 7951 §5.4, RFC 8040 App. B.2.1) as well as a bare object.
- **gNMI**: the T-API v2.6.0 performance-measurement stream of TR-548 §6 — see §1.6 and the deviations in §2.5.

### 1.5 Path computation and equipment

| Aspect | Status | Notes |
|--------|--------|------|
| **Path computation context** | ✓ | `GET /restconf/data/tapi-common:context/tapi-path-computation:path-computation-context` lists `path-comp-service` and `path`, the v2.6.0 names |
| **path-comp-service** | ✓ | Created by `POST .../tapi-path-computation:path-computation-context`, the list's parent (RFC 8040 §4.4.1), answering `201` with a `Location`, or by `PUT .../path-comp-service={uuid}` (`201`; a replacement, its paths computed anew, `204`); `GET`/`DELETE .../path-comp-service[={uuid}]`; the whole list replaced by `PUT` on the container, all or nothing. Creating one computes up to `rmsa.k_shortest_paths` ranked routes. v2.6.0 has no RPC, so the earlier `compute-path` RPC (and its non-standard `max-candidates`) is gone; the ranked view with a count is `POST /internal/compute-path` |
| **path** | ✓ | `GET .../path[={uuid}]` with `link` and `node-edge-point` references and the fiber length as a `cost-characteristic` |
| **Route constraints** | ◐ | `topology-constraint` include/exclude node and link and `explicit-route`; `routing-constraint` `route-objective-function: MIN_WORK_ROUTE_LATENCY` and `is-exclusive: true`. Every other constraint leaf is refused with a 400 (`invalid-value`) naming it, on path-comp-service and connectivity-service alike |
| **Physical context** | ✓ | `GET /restconf/data/tapi-common:context/tapi-equipment:physical-context`, `.../device[={uuid}]`, `.../device={uuid}/equipment={uuid}`, `.../physical-span[={uuid}]`. v2.6.0 has no `equipment-context`; the earlier non-standard `equipment-context` endpoints and their `equipment-type`/`length-km` fields are gone |
| **Spectrum capability** | ✓ | Per-SIP `spectrum-capability-pac` — supportable / available / occupied spectrum-bands in uint64 Hz, from live occupancy |
| **Assigned spectrum** | ✓ | Per-service `mcg-connectivity-service-end-point-spec/mc-spectrum-config-pac` on the end-point |

### 1.6 TAPI Streaming (TR-548 v3.2, performance measurement)

| Aspect | Status | Notes |
|--------|--------|------|
| **Supported stream type** | ✓ | The one TR-548 §6.2.2 makes mandatory: `GNMI_STREAMING_OBJECT_TYPE_MEASUREMENT_DETAILS`, `LOG_RECORD_STRATEGY_WHOLE_ENTITY`, `RECORD_TRIGGER_PERIODIC`, `LOG_STORAGE_STRATEGY_TRUNCATED` with `record-retention` in minutes, `CONNECTION_PROTOCOL_GNMI`, `ENCODING_FORMAT_PROTOBUF` |
| **Available stream** | ✓ | One, with `connection-address` `grpc://<host>:<port>` (§6.3.1), `connection-protocol` and the supported-stream-type reference |
| **Discovery (ST-0.2)** | ✓ | `GET .../tapi-streaming:stream-context` and its two lists and their entries; also in the full-context GET. Name-based UUIDs |
| **Stream state (ST-1.1)** | ✓ | `ALIGNING` until the first sampling tick is logged, then `ACTIVE`. No records exist while `ALIGNING` |
| **Truncated log (§3.8.1.2)** | ✓ | Every tick is logged, subscribers or not; records older than `record-retention` are dropped |
| **Subscription (§6.3.4)** | ✓ | Exactly the listed subset; any other field is `INVALID_ARGUMENT` naming it |
| **Records (§6.3.5)** | ✓ | `SubscribeResponse` update with path `oam-job[uuid=…]` and `proto_bytes` = `tapi.MeasurementDetails` |
| **Normalized measurement types (§6.3.8)** | ✓ | `CHROM_DISP`, `POL_MODE_DISP`, `FEC_PRE_FEC_BER`, `FEC_POST_FEC_BER`; the rest native with units |
| **Value qualifiers (ST-6.3)** | ✓ | `MISSING` for a service with no signal (link-failed or out-of-band) or one that could not be sampled, `INVALID` for a non-finite value |
| **Resume (§6.3.4 history, ST-6.4)** | ✓ | `range.start` replays from the log; see §2.5 for the edge cases |
| **Authentication (ST-0.1)** | ◐ | Optional pre-shared bearer tokens, required on every gNMI call and REST request alike — see §2.5 |

### 1.7 TAPI OAM

| Aspect | Status | Notes |
|--------|--------|------|
| **OAM job** | ◐ | `GET .../tapi-oam:oam-context`, `.../oam-job`, `.../oam-job={uuid}`. One provider-managed, read-only job per connectivity service, referencing its receiving end-point through `connectivity-service-end-point`; created on admission, removed on deletion, carried through snapshot/restore |
| **Rest of tapi-oam** | ✗ | No oam-service, MEG/MEP/MIP, profiles, job services, pm-data, current-data or history-data |

---

## 2. Gaps to Full T-API v2.6 Compliance

### 2.1 T-API module coverage

T-API 2.6 defines several modules beyond Common, Topology, and Connectivity. Coverage is as follows:

| Module | Purpose | Status |
|--------|---------|--------|
| **Path Computation Service** | Request candidate paths (A–Z, constraints, diversity) | ✓ Implemented — the `path-comp-service` and `path` lists of the path-computation context. Constraints: include/exclude node and link, explicit route, latency objective; no diversity, maximum cost/hops/delay, path sets or NEP/topology inclusion, which are refused by name. |
| **Equipment** | Physical / logical equipment inventory | ✓ Implemented — `physical-context` with `device`, `equipment` and `physical-span`, derived from the GNPy elements (devices for ROADMs, transceivers and amplifiers; spans for fibers). Equipment carries `equipment-type-name` and `equipment-type-identifier`; holders, access ports, strands and expected-equipment are not modelled. |
| **Photonic Media** | SIP spectrum capability, OTSi config, media channels | ◐ Partial — the SIP augment `photonic-media-service-interface-point-spec/spectrum-capability-pac` and the connectivity-service end-point augments `otsia-connectivity-service-end-point-spec/otsi-config/modulation` and `mcg-connectivity-service-end-point-spec/mc-spectrum-config-pac` are implemented. Standalone media-channel resources and the rest of `otsi-config` are not. |
| **Virtual Network Service** | Virtual network (slicing / abstraction) | ✗ No `tapi-virtual-network:*` endpoints. |
| **OAM (Operations, Admin, Maintenance)** | Maintenance entities, MEP/MIP, tests | ◐ Partial — read-only `oam-job` resources, one per service, that anchor the measurement stream (§1.7). Nothing else in `tapi-oam`. |
| **Fault** | Alarms, fault records, severity | ✗ No `tapi-fault:*` endpoints. Fiber failure is injected through the non-standard `/config/` plane instead. |
| **Notification** | Subscribe to notifications (e.g. fault, state change) | ✗ No T-API notification subscription, and no entity or alarm streams. |
| **Streaming** | TR-548 streams over WebSocket, SSE or gNMI | ◐ Partial — the performance-measurement stream over gNMI (§1.6); entity and alarm streams (compacted log), WebSocket/SSE, the stream monitor and `information-record-strategy` are out of scope. |

So: **Common, Topology, Connectivity, Path Computation and Equipment** are implemented to varying depth, Photonic Media, OAM and Streaming partially; the remaining modules are **missing**.

### 2.2 RESTCONF protocol gaps

| Gap | RFC / practice | Current behavior |
|-----|----------------|------------------|
| **Query parameters** | RFC 8040 §4.8: an unexpected or repeated parameter MUST be a 400; §4.8.1 `content` MUST be supported; `depth`, `fields`, `filter`, `with-defaults`, `start-time`, `stop-time` are optional | ◐ `content=all` is accepted; `content=config`/`nonconfig` is a `501` (a MUST not met, see §2.7); every other parameter, and any given twice, is a `400` `invalid-value`. No optional parameter is supported or advertised. |
| **Accept / media types** | RFC 8040 §5.2: a server MUST support one of XML and JSON, MUST answer an unsupported `Accept` with 406 and an unsupported request media type with 415 | ✓ JSON only (`application/yang-data+json`); an `Accept` that admits no JSON is a `406`, a request body that is not `application/yang-data+json` or `application/json` a `415`. Accepting `application/json` is a deliberate leniency (§2.7). |
| **Error format** | RESTCONF errors (e.g. `application/yang-data+json` with `ietf-restconf:errors`) | ✓ Implemented: HTTP and validation errors return `ietf-restconf:errors` with `error-type`, `error-tag`, `error-message`, and `error-app-tag`/`error-path` where RFC 7950 §15 defines them. Each status carries the tag RFC 8040 §7 pairs it with: a missing resource is `404` `invalid-value` (§4.3), a request refused as sent is `400` with `malformed-message`, `unknown-element`, `unknown-namespace`, `missing-element`, `bad-element` or `invalid-value` (no `422`; `unknown-element` and `unknown-namespace` carry the RFC 6241 `error-info`), a reference to a SIP, node or link that does not exist is `409` `data-missing`/`instance-required` (RFC 7950 §15.5). A 405 has `error-tag: operation-not-supported` (RFC 8040 §7) and an `Allow` header naming every method the resource serves (RFC 9110 §15.5.6); see [API.md § Error format](API.md#error-format). |
| **PATCH semantics** | RFC 8040 §4.6.1 plain patch: the target's representation merged into it; §4.6: `200` with a body or `204` | ◐ The body is the entry, `{"tapi-connectivity:connectivity-service": [{…}]}` (an object, or the bare entry, are accepted too), its key checked against the URI; `name`, `administrative-state` and `lifecycle-state` are merged and any other config leaf is refused by name (`400`), since the rest decide the admission and are changed with `PUT`. `200` with the entry's `GET` body. Not a general merge, and no YANG Patch (`application/yang-patch+json`). |
| **PUT** | RFC 8040 §4.5: create or replace the target; `201` when created, `204` when replaced | ✓ Implemented on every configuration data resource the twin lets a client write and on the datastore: a `connectivity-service={uuid}` or `path-comp-service={uuid}` entry is replaced (`204`) or created (`201` with `Location`; the body is the deviation below); the `connectivity-context` and `path-computation-context` containers, `tapi-common:context` and `{+restconf}/data` replace whole lists, all or nothing (`204`). Configuration the twin derives (the context's `uuid`, the topologies, the OAM jobs) may be repeated unchanged and is `403` `access-denied` otherwise; config-false members are ignored. `config false` resources (SIPs, nodes, paths, streams) and the list paths, which are not data resources (§3.5), answer `405`. See [API.md](API.md#replacing-lists-the-context-or-the-datastore). |
| **Body of a creating POST or PUT** | RFC 8040 §4.4.1: a `201 Created` with a `Location` header and no message body | ◐ Deliberate deviation: the `201` on the connectivity-context and the path-computation-context, and of a `PUT` that creates a connectivity-service, carries the `Location` **and** the created entry, exactly as its `GET` returns it: `{"tapi-connectivity:connectivity-service": [{…}]}` or `{"tapi-path-computation:path-comp-service": [{…}]}`, an array of one (RFC 7951 §5.4). The entry holds what the server decided (the UUID when the client sent none; the modulation format and spectrum admission chose; a path-comp-service's computed `path` references), and every client in the repository reads it from there. A strictly conforming client ignores the body and dereferences `Location`, which returns the same body. **Recommendation:** keep it. HTTP allows a 201 to describe what it created (RFC 9110 §15.3.2), a conforming client loses nothing, and dropping it would cost every client a second request to learn what admission chose; if strict conformance is ever required, drop it and have the clients `GET` the `Location`. |
| **Response bodies** | RFC 8040 §4.3: the target resource; RFC 7951 §4, §5.4: a top-level member is module-qualified, and a list entry is an array of one | ✓ Every GET returns its target as the body's one member, named with its module, and every list entry as an array of one: the SIP list `{"tapi-common:service-interface-point": […]}`, a SIP `{"tapi-common:service-interface-point": [{…}]}`, and likewise every keyed resource, the stream entries included. Earlier releases answered the SIP resources with the context's envelope and several entries as bare objects; see [API.md § Migrating](API.md#migrating-from-earlier-releases). |
| **OPTIONS** | RFC 8040 §4.1: the server MUST implement OPTIONS, to discover a resource's methods, and MUST return `Accept-Patch` | ✓ Implemented on every route: `200` with `Allow` (the set a `405` names) and, on a RESTCONF resource that takes `PATCH`, `Accept-Patch: application/yang-data+json`. CORS preflights are still answered by the CORS middleware. |
| **HEAD** | RFC 8040 §4.2: MUST, the same response as `GET` without the body | ✓ Implemented on every route that serves `GET`, with the `GET`'s header fields and access control. |

### 2.3 Data model gaps (within implemented modules)

**Connectivity**

- **ConnectivityService**: Minimal set of attributes. Standard T-API may include, for example: `connection`, `connectivity-service-end-point` refinements, `resilience-constraint`, `connectivity-constraint`, other QoS constraints. Supported: name, end-point, states, the end-point's `layer-protocol-constraint` augments, and `routing-constraint` / `topology-constraint` with the subset described under path computation. The service is reported as `UNIDIRECTIONAL` `PHOTONIC_MEDIA`, with `SOURCE` and `SINK` end-points, as it is provisioned. Every other configuration member v2.6.0 defines for the service, its end-points and their photonic augments is refused with `501` / `operation-not-supported`, never admitted and ignored: what the twin acts on is listed, and everything else in the YANG tree is refused, so no member can slip through a hand-kept list. This covers `connectivity-constraint`, `resilience-constraint`, the layer-protocol qualifiers, the end-point `role`, `capacity` and `protection-role`, the OTSi power and laser controls, the MCG bandwidth and flexi-grid pacs and the `tapi-oam` service augments; the full list is in [API.md](API.md#connectivity-services). A member the YANG does not define at all is a `400` (§2.7, Request bodies). `lifecycle-state` is config false in v2.6.0 but kept as a client sends it and changed by `PATCH`, as in earlier releases.
- **Connection**: T-API often models a “Connection” (actual path/route) separate from “ConnectivityService”. This twin does not expose a separate Connection resource; path is internal (and exposed only via `/internal/services/{uuid}`).
- **Modulation**: ✓ Carried where T-API 2.6 puts it — `end-point/layer-protocol-constraint/tapi-photonic-media:otsia-connectivity-service-end-point-spec/otsi-config/modulation/standard-modulation-technique`, with `MT_*` identities. `tapi-connectivity.yang` has no modulation leaf of its own.
- **Spectrum / L0**: ✓ Assigned spectrum is carried where T-API 2.6 puts it — `end-point/layer-protocol-constraint/tapi-photonic-media:mcg-connectivity-service-end-point-spec/mc-spectrum-config-pac/spectrum`, in uint64 Hz. Available and occupied spectrum per port is the SIP's `spectrum-capability-pac`. The grid parameters themselves are twin configuration rather than T-API and are served from `/internal/spectrum-context`. A client may request the spectrum (`mc-spectrum-config-pac/spectrum`) or the carrier (`otsi-config/central-frequency`); either is validated against the grid, the format's width, the route's amplifier band and occupancy, and used or refused with the conflict named. Without one, assignment is first-fit inside the amplifier band. The assigned `central-frequency` is reported in `otsi-config`.

**Topology**

- **Link / NEP**: Links carry a `latency-characteristic`; no other performance or quality attributes are exposed (e.g. `risk-characteristic`, `transfer-integrity`, loss). GNPy-derived data such as span loss is used internally but not exposed as standard T-API topology attributes.
- **Supporting entities**: No `tapi-topology:link` augmentations for “supporting link” or detailed photonic media extensions in the topology resource tree.

**Common / SIP**

- **SIP**: Model is minimal (name, layer, direction, states) plus the photonic `spectrum-capability-pac` augment. No other technology-specific SIP extensions are exposed.

### 2.4 Behavioral / semantic gaps

| Area | Gap |
|------|-----|
| **Administrative / operational state** | States are stored and returned, but there is no defined behavior (e.g. locking a SIP or link does not block connectivity creation or path computation). |
| **Lifecycle state** | Lifecycle is not driven by a state machine (e.g. PLANNED → INSTALLED transitions); it is just a stored field. |
| **Modulation format** | Supported at create time only; PATCH does not allow changing it (documented as intentional). |
| **QoT in T-API** | No standard T-API “path computation result” or “connectivity with QoT” response. Live QoT reaches T-API clients as performance records on the TR-548 measurement stream (§1.6); the twin's own JSON view is `/internal/opm`. |
| **Versioning / capability** | ✓ Implemented: data served under a configurable RESTCONF root (default `/restconf`), advertised via `/.well-known/host-meta`, with `yang-library-version`. No explicit T-API version in responses. |

### 2.5 TR-548 streaming: deviations and interpretations

What the twin does where TR-548 v3.2 leaves room, is internally inconsistent,
or is not followed, with the reason for each.

**Scope**

| Topic | TR-548 | TwinLight | Reason |
|-------|--------|-----------|--------|
| Streams | Entity, alarm and measurement streams; WebSocket, SSE, gNMI | Only the §6.2.2 measurement stream over gNMI | §6.2.2 is the only MUST for performance data; the twin has no alarms, and T-API entities are read over RESTCONF |
| oam-jobs on a stream | ST-6.1: "make available via a stream … the oam-jobs that correspond to the active measures" | Jobs are served by RESTCONF GET only | Entity streams (compacted log) are out of scope; a client resolves the job it needs before subscribing |
| Stream states | `ALIGNING`, `ACTIVE`, `PAUSED`, `TERMINATED` | `ALIGNING` → `ACTIVE` only | The twin never pauses or terminates the stream while running |
| Recovery after provider restart (ST-1.2) | Provider recovers the stream | The log is in-process and starts empty; the stream re-enters `ALIGNING` | The log sits behind the `RecordLog` interface so a persistent backend (Valkey Streams) can add this later |
| Stream monitor, `information-record-strategy`, `segment-size` | Optional | Not offered | Not required (§6.2.2: "not required to be used") |
| gNMI Get / Set | Not part of TR-548 | `UNIMPLEMENTED` | Configuration and reads are RESTCONF |

**Discovery and authentication**

| Topic | TR-548 | TwinLight | Reason |
|-------|--------|-----------|--------|
| Auth token (ST-0.1) | Client acquires a token, e.g. from an authorisation service (§7.11.2) | Optional pre-shared tokens from `TWINLIGHT_API_TOKENS`, sent as `authorization: Bearer <token>` gRPC metadata; `UNAUTHENTICATED` otherwise. The same tokens gate the RESTCONF discovery (ST-0.2). No token service, no TLS | Lab tool; the tokens keep unintended clients off the twin without an identity provider |
| `log-record-strategy` | May be omitted when it is the default (`WHOLE_ENTITY`) | Stated explicitly | A client can match all six §6.2.2 properties without knowing YANG defaults |
| `connection-address` | `grpc://<host-fqdn>:<port>` | `streaming.advertised_address`, else `grpc://<FQDN>:<grpc_port>` | The FQDN is wrong behind NAT or a Compose port mapping, or when it does not resolve, so it can be overridden. `twinlight-client` connects to it unless `--gnmi-target` says otherwise, and stops with an error if it cannot reach it ([API.md](API.md#when-the-advertised-address-cannot-be-reached)) |

**Subscription (§6.3.4)**

| Topic | TR-548 | TwinLight | Reason |
|-------|--------|-----------|--------|
| Subscription mode | The list says `TARGET_DEFINED`; the example uses `SAMPLE` with `sample_interval: 0` | Both accepted | The RIA contradicts itself; both mean provider-chosen cadence, and rejecting the RIA's own example would break clients written from it |
| Fields listed as "omitted" | `sample_interval`, `suppress_redundant`, `heartbeat_interval`, `use_models`, prefix `origin`/`elem`, path content | Rejected with `INVALID_ARGUMENT` naming the field | A client that sets them expects behaviour the twin does not provide |
| `qos`, `allow_aggregation` | "If present it should be ignored" | Ignored | As specified |
| `encoding` | Not mentioned | Omitted (proto3 default) or `PROTO` accepted; others rejected | Records are protobuf whatever is asked for |
| Further messages on the stream | Not mentioned | A second `SubscribeRequest` is `INVALID_ARGUMENT`; a half-close is accepted | gNMI STREAM subscriptions take one request; many gRPC clients half-close after sending it |
| `sync_response` | Not mentioned (no initial state, §6.3.4) | Sent at once without history; after the replayed records with history | gNMI requires one; placing it after the replay tells the client where catch-up ends |

**Resume (ST-6.4)**

| Topic | TR-548 | TwinLight | Reason |
|-------|--------|-----------|--------|
| Replay boundary | "from the first record with time identified as start"; duplicates expected | Records logged at or after `start`, inclusive | As specified; `twinlight-client` drops the duplicates |
| Truncated `start` | Send from the oldest record | As specified | — |
| `start` equal to the oldest record | "that record must not be sent as a record at that time may have been lost" | Implemented literally: records at that time are skipped | Followed as written. The twin trims whole ticks, so no tick is ever partly lost; a client that had processed only part of that tick loses the rest |
| `start` not yet elapsed | `UNIMPLEMENTED` or start from the next report | Next report | The friendlier of the two options |
| `range.end`, `snapshot_time` | Omitted / not used | `end` must be 0 or int64 max; `snapshot_time` is rejected | As specified |

**Records (§6.3.5)**

| Topic | TR-548 | TwinLight | Reason |
|-------|--------|-----------|--------|
| Record granularity | Many measures of one job may share one `measurement-details` | One record per oam-job per tick: all eleven QoT measures in `qualified-measurement`, with sampled time, resource, direction and interval stated once | §6.1.3: shared properties "only stated once" |
| Time units | The YANG gives none. The §6.3.5 example decodes `time-measurement-was-sampled` as milliseconds; `sample-interval` and the notification `timestamp` as nanoseconds | Same | Followed the RIA example where the YANG is silent |
| Notification `timestamp` | Not specified beyond gNMI ("ns since epoch") | The time the record was logged | §6.3.4 compares `range.start` with the logged time; using that as the timestamp gives the client its resume key |
| Measure types | Normalized type where one exists, native otherwise (§6.3.8) | `CHROM_DISP`, `POL_MODE_DISP` (GNPy's accumulated PMD, i.e. mean DGD), `FEC_PRE_FEC_BER`, `FEC_POST_FEC_BER` (the hard-decision code model); GSNR, OSNR (two references), Q, GMI, NGMI and the threshold-model post-FEC BER native, named by their OPM keys, with units | `NormalizedMeasurementType` has no member for the native ones. The threshold model is a planning convention, so the code-based model is the one presented as the normalized post-FEC BER |
| Units of normalized types | "Should not be stated" where the type defines them | Omitted; the values are in ps/nm (CD) and ps (PMD), the twin's OPM units | The proto defines no units for these types; the documented choice is the common one |
| Resource reference | `native-resource-id` mandatory "under most circumstances"; `abstract-resource-ref` optional | `native-resource-id` = GNPy UID of the receiving transceiver; no `abstract-resource-ref` | The oam-job path already identifies the measured service end-point |
| No signal (link failure, out-of-band channel) | ST-6.3: `MISSING` when a value should have been provided but was not | Every measure `MISSING`, value and units omitted — BERs included, although `/internal/opm` shows them as 1.0 — whenever the OPM sample carries a `status`: `link-failed` (a failed fiber on the path) or `out-of-band` (a channel the amplifiers drop, e.g. restored from a version-1 snapshot) | A receiver with no signal measures nothing; 1.0 is a charting convention of the REST view |
| No physics baseline | — | When the backend cannot compute a baseline, the OPM falls back to a deterministic sinusoidal mock; the stream reports those values as `OK`, exactly as `/internal/opm` does | Values must equal `/internal/opm`; `SUSPECT` is reserved for "solutions aware of the valid range" |
| `sample-qualifier` (FIRST/FINAL/DELAYED) | Optional | Never set | Optional; no record is delayed |

### 2.6 Security

Neither interface implements authorization scopes or TLS. Authentication is opt-in: with `TWINLIGHT_API_TOKENS` set, RESTCONF (every path but `/health`) and gNMI both require one of those pre-shared bearer tokens (RFC 6750; a rejected RESTCONF request gets 401 with an RFC 8040 `access-denied` error). T-API deployments normally sit behind an authenticating gateway; TwinLight is designed for lab and Compose use. See [SECURITY.md](../SECURITY.md).

### 2.7 RFC 8040 and RFC 7951 conformance matrix

What every T-API resource and the RESTCONF protocol resources do, against
RFC 8040 (with its verified errata) and RFC 7951, as of this release. "Conforms"
means the twin does what the clause requires; "Gap" means it does not, and
the row says why it is left and what is recommended. Where an RFC sentence
decides the question it is quoted.

**Resources and discovery**

| Requirement | Level | Status |
|-------------|-------|--------|
| §3.1 root discovered from `/.well-known/host-meta`, exactly one `restconf` link | MUST | Conforms with a root; **gap with `server.restconf_root: ""`**: no host-meta (the bundled clients read its `404` as "the root is empty"). Recommendation: serve host-meta with `href=""` and let the clients take an empty href as the empty root, or drop the empty-root mode — a client-visible decision for the owner |
| §3.3 API resource `{+restconf}` as `ietf-restconf:restconf`, media type `application/yang-data+json` | MUST | Conforms with a root (the media type was `application/json` before this release); **gap with an empty root**, as above |
| §3.3.1 `{+restconf}/data`, the datastore | MUST | Conforms: `GET` returns `ietf-restconf:data` with every top-level node |
| §3.3.2 `{+restconf}/operations` | MAY | Served, empty: T-API v2.6.0 defines no RPC |
| §3.3.3 `yang-library-version` | MUST | Conforms (`2019-01-04`, the revision served) |
| §9 `ietf-restconf-monitoring`: `restconf-state/capabilities` with the `defaults` capability | MUST | Conforms: `basic-mode=explicit`; no optional query parameter, so no other capability; `streams` omitted (§9.2 allows it) |
| §10 `ietf-yang-library`: "All YANG modules and submodules used by the server MUST be identified" | MUST | Conforms: `yang-library` (RFC 8525) and `modules-state` (RFC 7895). The `datastore` list names `running` and `operational` with one schema; the twin does not implement the NMDA `{+restconf}/ds/…` resources (RFC 8527), which is optional. `tapi-notification` is listed as import-only although most T-API modules import it; the twin offers no notification service (§2.1) |

**Methods** (every T-API resource; the sweeps in `tests/test_api/` are driven by the OpenAPI schema)

| Requirement | Level | Status |
|-------------|-------|--------|
| §4.1 OPTIONS, with `Accept-Patch` | MUST | Conforms on every route; `Accept-Patch` where `PATCH` is allowed (RFC 5789 §3.1 makes the field mean that) |
| §4.2 HEAD as GET without the body | MUST | Conforms on every route that serves `GET` |
| §4.3 GET; a missing instance is `404` `invalid-value` | MUST | Conforms |
| §4.4.1 create by POST on the parent, `201` with `Location` and no body; an existing entry is `409` `resource-denied` | MUST | Conforms but for the body (the deviation in §2.2, recommended to keep). §4.4.1 names `resource-denied` for a POST of an existing entry, and the IETF rejected changing it to `data-exists` (erratum 5761), so that is what the twin sends |
| §4.4.1, §4.5 `insert`/`point` "MUST be supported" | MUST | Conforms vacuously: "only allowed if the list or leaf-list is 'ordered-by user'", and no T-API v2.6.0 list is, so they are refused (`400`) |
| §4.5 PUT creates (`201`) or replaces (`204`); a missing body is `400` `invalid-value`; the key in the body must be the URI's | MUST | Conforms: on the connectivity-service and path-comp-service entries (`201`/`204`), and on the containers, the context and the datastore, which always exist (`204`). A body that is not the target, `{"<module>:<target>": …}` and nothing else, is `400` `invalid-value` |
| §4.5 "The PUT method MUST be supported for data and datastore resources" | MUST | Conforms for every resource a client writes: the datastore, `tapi-common:context`, the `connectivity-context` and `path-computation-context` containers and their list entries. Replace semantics: entries absent from the body are deleted, new ones admitted (route, spectrum, QoT) or computed, changed ones replaced, unchanged ones left as they are; one critical section, all or nothing — a refused entry leaves services, spectrum, OAM jobs, caches, EDFA tracker and path-comp-services as they were (`TapiContext.replace_lists`; tested on both backends). The `config true` nodes the twin derives — the context's `uuid` and `name`, the topologies and links, the OAM jobs — may be repeated unchanged ("unaltered data nodes are ignored", RFC 8341 §3.4.5) and are otherwise `403` `access-denied` (§4.5), omission included; config-false members in a body are ignored (RFC 7950 §7.21.1). Remaining `405`s: `config false` resources (SIPs, topology, nodes, paths, devices, streams) and the topology and OAM job resources the twin derives, whose content a context `PUT` may only repeat. Destructive by nature: a datastore or context `PUT` replaces every service, which API.md says with an example; no bundled client sends one |
| §4.6 PATCH: plain patch, `200` with a body or `204`; a missing target is not created | MUST | Conforms for connectivity-service, restricted to the leaves a patch can change (§2.2). **Gap** elsewhere: the containers, the context, the datastore and a path-comp-service answer `405`. A plain patch there merges entries into the existing ones (a merge of each entry's leaves, never a deletion), which is not the replacement machinery of `PUT` but a second design; left for a change of its own |
| §4.7 DELETE `204`; a missing instance `404` `invalid-value` | MUST | Conforms. A `404` is what §4.7 permits ("A server MAY return a '404 Not Found' status-line ... 'invalid-value'"); NETCONF's own error for deleting missing data is `data-missing`, which §7 pairs with `409`, a stricter reading the twin does not take because clients, the ONOS adapter included, treat a `404` on DELETE as "already gone" |

**Messages**

| Requirement | Level | Status |
|-------------|-------|--------|
| §4.8 an unexpected or repeated query parameter is `400` `invalid-value` | MUST | Conforms |
| §4.8.1 `content` "MUST be supported" | MUST | **Gap** for `config`/`nonconfig` (`501`); `all`, the default, is accepted. Recommendation: implement it from the YANG `config` statements (the trees mark every node `rw`/`ro`) — a filter over the bodies, with no client depending on its absence |
| §4.8.2–§4.8.9 `depth`, `fields`, `filter`, `with-defaults`, `start-time`, `stop-time` | optional | Not supported, not advertised, refused with `400` |
| §5.2 one of XML and JSON; `Accept` and `406`; `415` for an unsupported input encoding | MUST | Conforms: JSON. `application/json` request bodies are accepted beside `application/yang-data+json` — the same JSON, sent by generic HTTP clients (`TapiClient`, the examples and the ONOS scripts send it); it is a second spelling of one media type, not a second encoding. Recommendation: keep it |
| §5.5 `Cache-Control` "in every response" | MUST | Conforms (`no-cache`) |
| §3.4.1.2 "The server MUST maintain a unique opaque entity-tag for the datastore resource and MUST return it in the 'ETag' header"; §3.4.1.1 and §3.5.1–§3.5.2 `Last-Modified` and per-resource ETags | MUST / SHOULD | **Gap**: no `ETag` or `Last-Modified` on any T-API resource, and no `If-Match`/`If-None-Match`/`If-Unmodified-Since` handling (RFC 9110 §13 makes evaluating them a MUST once they are). Recommendation: a revision counter on the T-API configuration (service and path-comp-service writes, restores), its value as the datastore's ETag on `GET`/`HEAD` of every T-API resource, and `412`/`304` on the conditional headers, as the transparent controller already does for its own tree. Additive for clients, but it touches every write path, so it is left for a change of its own |
| §6 notifications, §9.2 `streams` | optional | Not offered (the T-API stream is the gNMI one, §1.6) |
| §7 each error-tag with its status; `ietf-restconf:errors` | MUST/SHOULD | Conforms (§2.2, Error format). `missing-element` is paired with 400 by verified erratum 7311 |
| YANG Patch (RFC 8072) | optional | Not offered: `application/yang-patch+json` on a T-API resource is a `415` |

**Encoding (RFC 7951)**

| Requirement | Level | Status |
|-------------|-------|--------|
| §4 member names: qualified at the top level and where the module changes, simple otherwise | MUST | Conforms: every member of every GET body of a twin holding services, a path-comp-service and OAM jobs, on both backends, was checked against the v2.6.0 YANG trees (augments included) — no unknown member, none qualified where it must not be. `tests/test_api/test_tapi_schema.py` keeps checking it, against the tree the request checks use |
| §5.4 a list instance is a name/array pair | MUST | Conforms for every list and every list entry. A container whose list is empty carries `[]` (`connectivity-context`, `path-computation-context`) or omits it (`oam-context`): RFC 7951 encodes instances and says nothing of an empty list, so both are read the same way; a list GET with no entries answers `[]` |
| §6.8 identityref values qualified unless the identity is in the leaf's own module | MUST | Conforms (`tapi-streaming:…`, `tapi-oam:…` qualified; `GRID_TYPE_FLEX`, `MT_DP-QPSK`, `ADJUSTMENT_GRANULARITY_G_6_25GHZ` are `tapi-photonic-media` identities in `tapi-photonic-media` leaves) |
| §6.1 "A value of the 'int64', 'uint64', or 'decimal64' type is represented as a JSON string" | MUST | **Gap**: the `uint64` frequencies (`lower-frequency`, `upper-frequency`, `central-frequency`), `number-of-mc`, `number-of-otsi` and the `decimal64` `channel-output-power` are JSON numbers. Input accepts both. Recommendation: emit strings — `"191278125000000"` — once the readers parse both (the UI's `spectrumOf` returns nothing for a string today, and the ONOS adapter's `_mc_pool` and `_spectrum_of` do arithmetic on them), in a change of its own with a migration row |

**Request bodies**

| Requirement | Level | Status |
|-------------|-------|--------|
| A body with a member the schema does not define is an error (`unknown-element`, RFC 6241 Appendix A) | MUST (NETCONF `edit-config` semantics, which RFC 8040 §1.4 maps onto) | Conforms: every member of a connectivity-service or path-comp-service body (`POST`, `PUT`, `PATCH`), and of a container, context or datastore `PUT`, is checked at any depth against the v2.6.0 data-node tree of the implemented modules, augments included (`models/tapi_schema.py`, generated from the YANG by `tools/gen_tapi_schema.py`). One the tree does not define there is `400` `unknown-element` with `error-info` `bad-element` and the holding node as `error-path`; one qualified with a module the twin does not implement is `unknown-namespace` (`bad-namespace`); a defined one qualified where "the simple form ... MUST be used" (RFC 7951 §4) is `malformed-message`. A defined configuration member the twin does not act on is `501` (§2.3); a config-false one is ignored, being no part of the configuration (RFC 7950 §7.21.1). The ONOS adapter drops T-API 2.1's `service-layer` and `service-type` before forwarding (integrations/onos) |
| A leafref to an instance that does not exist: `data-missing`, `instance-required` (RFC 7950 §15.5) | MUST | Conforms for the end-point SIP (with `error-path`) and for constraint nodes and links |

---

## 3. Summary table

| Category | Implemented | Gaps |
|----------|-------------|------|
| **T-API modules** | Common, Topology, Connectivity (full CRUD), Path Computation, Equipment, Photonic Media (SIP spectrum capability, OTSi modulation), OAM jobs, Streaming (TR-548 measurement stream) | Virtual Network, Fault, Notification, the rest of OAM, entity and alarm streams; media channels |
| **RESTCONF** | Root resource + `host-meta` discovery + `yang-library-version`, the datastore, `ietf-yang-library` and `ietf-restconf-monitoring`, HEAD and OPTIONS everywhere, paths, JSON with `406`/`415`, `yang-data+json` content type and `Cache-Control`, `ietf-restconf:errors` with the RFC 8040 §7 tags, standard response bodies, PUT on every writable data resource and the datastore (all or nothing), plain-patch PATCH, unknown request members refused | `content=config`/`nonconfig`, `ETag`/`Last-Modified` and conditional requests, 64-bit numbers as JSON strings, the empty-root mode's missing discovery, optional query parameters, XML, YANG Patch (see §2.7) |
| **Data model** | Core topology + connectivity + SIP, per-SIP spectrum capability, standard modulation and assigned-spectrum augments, link latency-characteristic | Separate Connection resource, routing/resilience/cost constraints, richer topology quality attributes |
| **Behaviour** | Create/read/update/delete connectivity with QoT-aware admission; path computation; snapshot/restore | State and lifecycle semantics are stored but not enforced; the modulation format is immutable after create; QoT is exposed non-standardly |
| **gNMI** | TR-548 measurement stream: Capabilities, the §6.3.4 Subscribe, history-extension resume, PROTO records | Get, Set, other streams |
| **Security** | Optional bearer-token authentication on RESTCONF and gNMI | Authorization scopes, TLS, token service |

---

## 4. References

- **T-API 2.6**: ONF Transport API 2.6 (YANG, OpenAPI, path computation, virtual network, equipment, OAM, fault, notification).
- **RESTCONF**: [RFC 8040](https://www.rfc-editor.org/rfc/rfc8040) and its [errata](https://www.rfc-editor.org/errata/rfc8040) (7311 verified; 5565 and 5761 rejected) — methods, query parameters, media types and error format; [RFC 7951](https://www.rfc-editor.org/rfc/rfc7951) — JSON encoding; [RFC 7950](https://www.rfc-editor.org/rfc/rfc7950) §15 — error tags of YANG constraints; [RFC 6241](https://www.rfc-editor.org/rfc/rfc6241) Appendix A — the error-tags; [RFC 8525](https://www.rfc-editor.org/rfc/rfc8525) — YANG library.
- **TR-548 v3.2**: TAPI v2.6.0 Reference Implementation Agreement — Streaming. [TAPI-Documentation v2.6.0](https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI-Documentation/tree/v2.6.0/ReferenceImplementationAgreements/TR-548) — §3.7, §3.8.1.2, §6, use cases ST-0.x, ST-1.1, ST-6.x.
- **TR-547 v3.3**: TAPI v2.6.0 Reference Implementation Agreement, use cases 17a/17b (OAM jobs).
- **TAPI v2.6.0 YANG and protobuf**: [`tapi-streaming`, `tapi-gnmi-streaming`, `tapi-oam`](https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI/tree/v2.6.0/YANG); [`tapi-streaming-performance.proto`](https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI/blob/v2.6.0/PROTOBUF/tapi-streaming-performance.proto).
- **gNMI**: [gNMI specification](https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-specification.md); [history extension](https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-history.md).
- **Project**: [API.md](API.md) (endpoint catalogue), [ARCHITECTURE.md](ARCHITECTURE.md) (why the standard and non-standard surfaces are kept apart).
