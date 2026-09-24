# T-API v2.6 compliance: current implementation and gaps

This document evaluates TwinLight's REST and gNMI interfaces against a **fully compliant T-API v2.6.0** implementation. It summarizes what is implemented, then lists gaps by category.

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
| **Create service** | ✓ | `POST .../connectivity-context/connectivity-service` with end-point (SIP refs), modulation-format |
| **List / get service** | ✓ | `GET .../connectivity-service`, `GET .../connectivity-service={uuid}` |
| **Replace service** | ✓ | `PUT .../connectivity-service={uuid}` (full replacement; body UUID must match path) |
| **Update service** | ✓ | `PATCH .../connectivity-service={uuid}` (name, administrative-state, lifecycle-state) |
| **Delete service** | ✓ | `DELETE .../connectivity-service={uuid}` (204) |
| **Conflict handling** | ✓ | 409 for no path, insufficient spectrum, or insufficient QoT |
| **End-point model** | ✓ | local-id, service-interface-point (SIP ref), direction |

### 1.4 Protocol and conventions

- **RESTCONF root**: The T-API modules are served under `server.restconf_root` (default `/restconf`), discoverable via `GET /.well-known/host-meta` (XRD, RFC 6415) as RFC 8040 §3.1 requires. They remain reachable at the bare `/data/...` for clients written against earlier releases; retiring that mount is tracked in [PENDING.md](PENDING.md).
- **Capability discovery**: `GET {restconf-root}` and `GET {restconf-root}/yang-library-version` answer per RFC 8040 §3.3.
- **URL path encoding**: Colons in paths handled (PathDecodeMiddleware for `%3A` → `:`).
- **RESTCONF-style paths**: Resource paths follow the `.../connectivity-service={uuid}` pattern.
- **List encoding**: POST accepts a list entry as a single-element JSON array (RFC 7951 §5.4, RFC 8040 App. B.2.1) as well as a bare object.
- **gNMI**: Capabilities and Subscribe (ONCE / STREAM / POLL) over context, topology and OPM paths; JSON_IETF encoding.

### 1.5 Path computation and equipment

| Aspect | Status | Notes |
|--------|--------|------|
| **Path computation context** | ✓ | `GET /data/tapi-path-computation:path-computation-context`, `.../path-computation-service` |
| **Compute-path RPC** | ✓ | `POST .../path-computation-service/compute-path` returns candidate paths without committing state |
| **Equipment context** | ✓ | `GET /data/tapi-equipment:equipment-context`, `.../equipment`, `.../equipment={uuid}` |
| **Spectrum capability** | ✓ | Per-SIP `spectrum-capability-pac` — supportable / available / occupied spectrum-bands in uint64 Hz, from live occupancy |
| **Assigned spectrum** | ✓ | Per-service `mcg-connectivity-service-end-point-spec/mc-spectrum-config-pac` on the end-point |

---

## 2. Gaps to Full T-API v2.6 Compliance

### 2.1 T-API module coverage

T-API 2.6 defines several modules beyond Common, Topology, and Connectivity. Coverage is as follows:

| Module | Purpose | Status |
|--------|---------|--------|
| **Path Computation Service** | Request candidate paths (A–Z, constraints, diversity) | ✓ Implemented — `GET /data/tapi-path-computation:path-computation-context`, `.../path-computation-service`, and `POST .../compute-path`. Constraint support is limited to A–Z endpoints and a candidate count; no diversity, inclusion/exclusion or cost constraints. |
| **Equipment** | Physical / logical equipment inventory | ✓ Implemented — `GET /data/tapi-equipment:equipment-context`, `.../equipment`, `.../equipment={uuid}`. Inventory is derived from the GNPy element list; holder/physical-position modelling is not represented. |
| **Photonic Media** | SIP spectrum capability, OTSi config, media channels | ◐ Partial — the SIP augment `photonic-media-service-interface-point-spec/spectrum-capability-pac` and the connectivity-service end-point augment `otsia-connectivity-service-end-point-spec/otsi-config/modulation` are implemented. Media-channel (MCG) resources, `mc-spectrum-config-pac` and the rest of `otsi-config` are not. |
| **Virtual Network Service** | Virtual network (slicing / abstraction) | ✗ No `tapi-virtual-network:*` endpoints. |
| **OAM (Operations, Admin, Maintenance)** | Maintenance entities, MEP/MIP, tests | ✗ No `tapi-oam:*` endpoints. |
| **Fault** | Alarms, fault records, severity | ✗ No `tapi-fault:*` endpoints. Fiber failure is injected through the non-standard `/config/` plane instead. |
| **Notification** | Subscribe to notifications (e.g. fault, state change) | ✗ No T-API notification subscription; gNMI Subscribe covers topology, context and OPM paths. |
| **Streaming** | T-API-defined streaming (if distinct from gNMI) | ✗ Not implemented as T-API streaming; live OPM is served over gNMI and at `/internal/opm`. |

So: **Common, Topology, Connectivity, Path Computation and Equipment** are implemented to varying depth, Photonic Media partially; the remaining modules are **missing**.

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

- **Link / NEP**: No latency, loss, or other performance/quality attributes in the TAPI topology model (e.g. `risk-characteristic`, `transfer-integrity`, `latency-characteristic`). GNPy-derived data is used internally but not exposed as standard T-API topology attributes.
- **Supporting entities**: No `tapi-topology:link` augmentations for “supporting link” or detailed photonic media extensions in the topology resource tree.

**Common / SIP**

- **SIP**: Model is minimal (name, layer, direction, states). No extra attributes required by profiles (e.g. technology-specific SIP extensions) are exposed.

### 2.4 Behavioral / semantic gaps

| Area | Gap |
|------|-----|
| **Administrative / operational state** | States are stored and returned, but there is no defined behavior (e.g. locking a SIP or link does not block connectivity creation or path computation). |
| **Lifecycle state** | Lifecycle is not driven by a state machine (e.g. PLANNED → INSTALLED transitions); it is just a stored field. |
| **Modulation format** | Supported at create time only; PATCH does not allow changing it (documented as intentional). |
| **QoT in T-API** | No standard T-API “path computation result” or “connectivity with QoT” response; QoT is internal and exposed via `/internal/opm`. |
| **Versioning / capability** | ✓ Implemented: data served under a configurable RESTCONF root (default `/restconf`), advertised via `/.well-known/host-meta`, with `yang-library-version`. No explicit T-API version in responses. |

### 2.5 gNMI-specific gaps

| Gap | Notes |
|-----|--------|
| **Path coverage** | The static `_PATH_MAP` covers context and topology paths; OPM paths are resolved dynamically (any path ending in an `opm` element maps to the live OPM endpoint). Bare connectivity-service paths still fall back to `/data/<joined>` rather than having first-class handling. |
| **Get RPC** | gNMI Get is not implemented (returns UNIMPLEMENTED). |
| **Set RPC** | gNMI Set is not implemented. |
| **Encoding** | Only JSON_IETF is offered; PROTO and ASCII encodings are not. |

### 2.6 Security

Neither interface implements authentication, authorization or TLS. T-API deployments normally sit behind an authenticating gateway; TwinLight is designed for lab and Compose use. See [SECURITY.md](../SECURITY.md).

---

## 3. Summary table

| Category | Implemented | Gaps |
|----------|-------------|------|
| **T-API modules** | Common, Topology, Connectivity (full CRUD), Path Computation, Equipment, Photonic Media (SIP spectrum capability, OTSi modulation) | Virtual Network, OAM, Fault, Notification, T-API Streaming; media channels |
| **RESTCONF** | Root resource + `host-meta` discovery + `yang-library-version`, paths, JSON, `yang-data+json` content type, `ietf-restconf:errors` error bodies, PUT, list-encoded bodies | `content`/`depth`/`filter`/`with-defaults` query parameters, XML, full YANG-aware PATCH/merge semantics |
| **Data model** | Core topology + connectivity + SIP, per-SIP spectrum capability, standard modulation and assigned-spectrum augments, link latency-characteristic | Separate Connection resource, routing/resilience/cost constraints, richer topology quality attributes |
| **Behaviour** | Create/read/update/delete connectivity with QoT-aware admission; path computation; snapshot/restore | State and lifecycle semantics are stored but not enforced; modulation-format is immutable after create; QoT is exposed non-standardly |
| **gNMI** | Capabilities, Subscribe (ONCE/STREAM/POLL) over context, topology and OPM paths | Get, Set, first-class connectivity paths, non-JSON encodings |
| **Security** | — | Authentication, authorization, TLS |

---

## 4. References

- **T-API 2.6**: ONF Transport API 2.6 (YANG, OpenAPI, path computation, virtual network, equipment, OAM, fault, notification).
- **RESTCONF**: [RFC 8040](https://www.rfc-editor.org/rfc/rfc8040) — query parameters and error format.
- **gNMI**: [gNMI specification](https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-specification.md).
- **Project**: [API.md](API.md) (endpoint catalogue), [ARCHITECTURE.md](ARCHITECTURE.md) (why the standard and non-standard surfaces are kept apart).
