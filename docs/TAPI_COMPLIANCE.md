# T-API v2.6 compliance: current implementation and gaps

This document evaluates TwinLight's RESTCONF and gNMI interfaces against a **fully compliant T-API v2.6.0** implementation. It summarizes what is implemented, then lists gaps by category.

Target audience: integrators, test suites, and contributors who need to know where the twin is spec-compliant and where it is not.

For the endpoint catalogue itself see [API.md](API.md).

---

## 1. What Is Implemented (T-API–aligned)

### 1.1 TAPI Common

| Aspect | Status | Notes |
|--------|--------|------|
| **Context** | ✓ | `GET /data/tapi-common:context` returns context with SIP list and topology-context ref |
| **Service interface points** | ✓ | `GET .../service-interface-point`, `GET .../service-interface-point={uuid}`; each SIP carries the `tapi-photonic-media:photonic-media-service-interface-point-spec` augment |
| **Path & JSON keys** | ✓ | Hyphenated keys, `tapi-common:context` wrapper |
| **Content-Type** | ✓ | Responses under `/data/` and `{restconf-root}/data/` use `application/yang-data+json` (RESTCONF) |

### 1.2 TAPI Topology

| Aspect | Status | Notes |
|--------|--------|------|
| **Topology context** | ✓ | `GET .../tapi-topology:topology-context` |
| **Topology list / single** | ✓ | `GET .../topology={uuid}` |
| **Node** | ✓ | `GET .../topology={uuid}/node={node_uuid}` |
| **Link** | ✓ | `GET .../topology={uuid}/link={link_uuid}` |
| **Link latency-characteristic** | ✓ | Propagation delay (ns) from fiber length / speed of light in fiber |
| **Owned node-edge-point (NEP)** | ✓ | `GET .../topology={uuid}/node={node_uuid}/owned-node-edge-point={nep_uuid}` |
| **Data model** | ✓ | Topology, Node, Link, NodeEdgePoint with layer-protocol-name, states, NEP refs |

### 1.3 TAPI Connectivity

| Aspect | Status | Notes |
|--------|--------|------|
| **Create service** | ✓ | `POST .../connectivity-context/connectivity-service` with end-points (SIP refs) and the photonic modulation augment |
| **List / get service** | ✓ | `GET .../connectivity-service`, `GET .../connectivity-service={uuid}` |
| **Replace service** | ✓ | `PUT .../connectivity-service={uuid}` (full replacement; body UUID must match path) |
| **Update service** | ✓ | `PATCH .../connectivity-service={uuid}` (name, administrative-state, lifecycle-state) |
| **Delete service** | ✓ | `DELETE .../connectivity-service={uuid}` (204) |
| **Conflict handling** | ✓ | 409 for no path, insufficient spectrum, or insufficient QoT |
| **End-point model** | ✓ | local-id, service-interface-point (SIP ref), direction |

### 1.4 Protocol and conventions

- **RESTCONF root**: The T-API modules are served under `server.restconf_root` (default `/restconf`), discoverable via `GET /.well-known/host-meta` (XRD, RFC 6415) as RFC 8040 §3.1 requires. They remain reachable at the bare `/data/...` for clients written against earlier releases; that mount is scheduled for removal (see [ROADMAP.md](ROADMAP.md)).
- **Capability discovery**: `GET {restconf-root}` and `GET {restconf-root}/yang-library-version` answer per RFC 8040 §3.3.
- **URL path encoding**: Colons in paths handled (PathDecodeMiddleware for `%3A` → `:`).
- **RESTCONF-style paths**: Resource paths follow the `.../connectivity-service={uuid}` pattern.
- **List encoding**: POST accepts a list entry as a single-element JSON array (RFC 7951 §5.4, RFC 8040 App. B.2.1) as well as a bare object.
- **gNMI**: the T-API v2.6.0 performance-measurement stream of TR-548 §6 — see §1.6 and the deviations in §2.5.

### 1.5 Path computation and equipment

| Aspect | Status | Notes |
|--------|--------|------|
| **Path computation context** | ✓ | `GET /data/tapi-path-computation:path-computation-context`, `.../path-computation-service` |
| **Compute-path RPC** | ✓ | `POST .../path-computation-service/compute-path` returns candidate paths without committing state |
| **Equipment context** | ✓ | `GET /data/tapi-equipment:equipment-context`, `.../equipment`, `.../equipment={uuid}` |
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
| **Value qualifiers (ST-6.3)** | ✓ | `MISSING` for a link-failed or unsampleable service, `INVALID` for a non-finite value |
| **Resume (§6.3.4 history, ST-6.4)** | ✓ | `range.start` replays from the log; see §2.5 for the edge cases |
| **Authentication (ST-0.1)** | ◐ | Optional pre-shared bearer token on every gNMI call — see §2.5 |

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
| **Path Computation Service** | Request candidate paths (A–Z, constraints, diversity) | ✓ Implemented — `GET /data/tapi-path-computation:path-computation-context`, `.../path-computation-service`, and `POST .../compute-path`. Constraint support is limited to A–Z endpoints and a candidate count; no diversity, inclusion/exclusion or cost constraints. |
| **Equipment** | Physical / logical equipment inventory | ✓ Implemented — `GET /data/tapi-equipment:equipment-context`, `.../equipment`, `.../equipment={uuid}`. Inventory is derived from the GNPy element list; holder/physical-position modelling is not represented. |
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
| **Query parameters** | RFC 8040: `content`, `depth`, `filter`, `with-defaults` on GET | Not supported. All GETs return full resource; no `content=(config\|nonconfig\|all)`, no `depth`, no `filter`. |
| **Accept header** | RESTCONF allows client to request `application/yang-data+json` or `application/yang-data+xml` | Only JSON is produced; XML not supported. |
| **Error format** | RESTCONF errors (e.g. `application/yang-data+json` with `ietf-restconf:errors`) | ✓ Implemented: HTTP and validation errors return `ietf-restconf:errors` with `error-type`, `error-tag`, `error-message`. |
| **PATCH semantics** | RFC 7396 (Merge Patch) / YANG patch rules | PATCH is implemented as a few allowed fields (name, states); not a full merge or YANG-aware PATCH. |
| **PUT** | Create/replace resource at exact path | ✓ Implemented for connectivity-service: `PUT .../connectivity-service={uuid}` replaces by UUID. |
| **OPTIONS** | Discovery of allowed methods on a resource | Not explicitly documented; relies on default FastAPI behavior. |

### 2.3 Data model gaps (within implemented modules)

**Connectivity**

- **ConnectivityService**: Minimal set of attributes. Standard T-API may include, for example: `connection`, `connectivity-service-end-point` refinements, `routing-constraint`, `resilience-constraint`, `cost-characteristic`, other QoS/route constraints. Only name, end-point and states are supported, plus the end-point's `layer-protocol-constraint` carrying modulation.
- **Connection**: T-API often models a “Connection” (actual path/route) separate from “ConnectivityService”. This twin does not expose a separate Connection resource; path is internal (and exposed only via `/internal/services/{uuid}`).
- **Modulation**: ✓ Carried where T-API 2.6 puts it — `end-point/layer-protocol-constraint/tapi-photonic-media:otsia-connectivity-service-end-point-spec/otsi-config/modulation/standard-modulation-technique`, with `MT_*` identities. `tapi-connectivity.yang` has no modulation leaf of its own.
- **Spectrum / L0**: ✓ Assigned spectrum is carried where T-API 2.6 puts it — `end-point/layer-protocol-constraint/tapi-photonic-media:mcg-connectivity-service-end-point-spec/mc-spectrum-config-pac/spectrum`, in uint64 Hz. Available and occupied spectrum per port is the SIP's `spectrum-capability-pac`. The grid parameters themselves are twin configuration rather than T-API and are served from `/internal/spectrum-context`. Spectrum assignment remains first-fit at create time; no client-specified slot in POST.

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
| Auth token (ST-0.1) | Client acquires a token, e.g. from an authorisation service (§7.11.2) | Optional pre-shared token, `streaming.auth_token`, sent as `authorization: Bearer <token>` gRPC metadata; `UNAUTHENTICATED` otherwise. No token service, no TLS | Lab tool; the token keeps an unintended client off the stream without an identity provider |
| `log-record-strategy` | May be omitted when it is the default (`WHOLE_ENTITY`) | Stated explicitly | A client can match all six §6.2.2 properties without knowing YANG defaults |
| `connection-address` | `grpc://<host-fqdn>:<port>` | `streaming.advertised_address`, else `grpc://<FQDN>:<grpc_port>` | The FQDN is wrong behind NAT or a Compose port mapping, so it can be overridden |

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
| Link failure | ST-6.3: `MISSING` when a value should have been provided but was not | Every measure `MISSING`, value and units omitted — BERs included, although `/internal/opm` shows them as 1.0 | A receiver with no signal measures nothing; 1.0 is a charting convention of the REST view |
| No physics baseline | — | When the backend cannot compute a baseline, the OPM falls back to a deterministic sinusoidal mock; the stream reports those values as `OK`, exactly as `/internal/opm` does | Values must equal `/internal/opm`; `SUSPECT` is reserved for "solutions aware of the valid range" |
| `sample-qualifier` (FIRST/FINAL/DELAYED) | Optional | Never set | Optional; no record is delayed |

### 2.6 Security

Neither interface implements authorization or TLS, and RESTCONF has no authentication. The gNMI stream can require a pre-shared bearer token (`streaming.auth_token`, §2.5). T-API deployments normally sit behind an authenticating gateway; TwinLight is designed for lab and Compose use. See [SECURITY.md](../SECURITY.md).

---

## 3. Summary table

| Category | Implemented | Gaps |
|----------|-------------|------|
| **T-API modules** | Common, Topology, Connectivity (full CRUD), Path Computation, Equipment, Photonic Media (SIP spectrum capability, OTSi modulation), OAM jobs, Streaming (TR-548 measurement stream) | Virtual Network, Fault, Notification, the rest of OAM, entity and alarm streams; media channels |
| **RESTCONF** | Root resource + `host-meta` discovery + `yang-library-version`, paths, JSON, `yang-data+json` content type, `ietf-restconf:errors` error bodies, PUT, list-encoded bodies | `content`/`depth`/`filter`/`with-defaults` query parameters, XML, full YANG-aware PATCH/merge semantics |
| **Data model** | Core topology + connectivity + SIP, per-SIP spectrum capability, standard modulation and assigned-spectrum augments, link latency-characteristic | Separate Connection resource, routing/resilience/cost constraints, richer topology quality attributes |
| **Behaviour** | Create/read/update/delete connectivity with QoT-aware admission; path computation; snapshot/restore | State and lifecycle semantics are stored but not enforced; the modulation format is immutable after create; QoT is exposed non-standardly |
| **gNMI** | TR-548 measurement stream: Capabilities, the §6.3.4 Subscribe, history-extension resume, PROTO records | Get, Set, other streams |
| **Security** | Optional bearer token on gNMI | REST authentication, authorization, TLS |

---

## 4. References

- **T-API 2.6**: ONF Transport API 2.6 (YANG, OpenAPI, path computation, virtual network, equipment, OAM, fault, notification).
- **RESTCONF**: [RFC 8040](https://www.rfc-editor.org/rfc/rfc8040) — query parameters and error format.
- **TR-548 v3.2**: TAPI v2.6.0 Reference Implementation Agreement — Streaming. [TAPI-Documentation v2.6.0](https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI-Documentation/tree/v2.6.0/ReferenceImplementationAgreements/TR-548) — §3.7, §3.8.1.2, §6, use cases ST-0.x, ST-1.1, ST-6.x.
- **TR-547 v3.3**: TAPI v2.6.0 Reference Implementation Agreement, use cases 17a/17b (OAM jobs).
- **TAPI v2.6.0 YANG and protobuf**: [`tapi-streaming`, `tapi-gnmi-streaming`, `tapi-oam`](https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI/tree/v2.6.0/YANG); [`tapi-streaming-performance.proto`](https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI/blob/v2.6.0/PROTOBUF/tapi-streaming-performance.proto).
- **gNMI**: [gNMI specification](https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-specification.md); [history extension](https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-history.md).
- **Project**: [API.md](API.md) (endpoint catalogue), [ARCHITECTURE.md](ARCHITECTURE.md) (why the standard and non-standard surfaces are kept apart).
