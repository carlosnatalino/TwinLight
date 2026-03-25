# T-API v2.6 Compliance: Current Implementation and Gaps

This document evaluates the digital twin’s REST and gNMI interfaces against a **fully compliant T-API v2.6.0** implementation. It summarizes what is implemented, then lists gaps by category.

Target audience: integrators, test suites, and contributors who need to know where the twin is spec-compliant and where it is not.

---

## 1. What Is Implemented (T-API–aligned)

### 1.1 TAPI Common

| Aspect | Status | Notes |
|--------|--------|------|
| **Context** | ✓ | `GET /data/tapi-common:context` returns context with SIP list and topology-context ref |
| **Service interface points** | ✓ | `GET .../service-interface-point`, `GET .../service-interface-point={uuid}` |
| **Path & JSON keys** | ✓ | Hyphenated keys, `tapi-common:context` wrapper |
| **Content-Type** | ✓ | Responses under `/data/` use `application/yang-data+json` (RESTCONF) |

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

### 1.4 Protocol and Conventions

- **URL path encoding**: Colons in paths handled (PathDecodeMiddleware for `%3A` → `:`).
- **RESTCONF-style paths**: Resource paths follow `.../connectivity-service={uuid}` pattern.
- **gNMI**: Capabilities and Subscribe (ONCE / STREAM / POLL) for topology/context paths; JSON_IETF encoding.

---

## 2. Gaps to Full T-API v2.6 Compliance

### 2.1 Missing T-API modules (no endpoints)

T-API 2.6 defines several modules beyond Common, Topology, and Connectivity. None of these are exposed as T-API REST resources:

| Module | Purpose | Gap |
|--------|---------|-----|
| **Path Computation Service** | Request candidate paths (A–Z, constraints, diversity) | No `tapi-path-computation:*` endpoints. Path computation is internal (RMSA); no standard RPC or REST resource for “compute path” or “path query”. |
| **Virtual Network Service** | Virtual network (slicing / abstraction) | No `tapi-virtual-network:*` endpoints. |
| **Equipment** | Physical / logical equipment inventory | No `tapi-equipment:*` endpoints. Equipment is GNPy-internal only. |
| **OAM (Operations, Admin, Maintenance)** | Maintenance entities, MEP/MIP, tests | No `tapi-oam:*` endpoints. |
| **Fault** | Alarms, fault records, severity | No `tapi-fault:*` endpoints. |
| **Notification** | Subscribe to notifications (e.g. fault, state change) | No T-API notification subscription; gNMI Subscribe is used for topology/context only. |
| **Streaming** | T-API-defined streaming (if distinct from gNMI) | Not implemented as T-API streaming; internal OPM is `/internal/opm`. |

So: **only Common, Topology, and Connectivity** are partially implemented; all other T-API 2.6 modules are **missing**.

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

- **ConnectivityService**: Minimal set of attributes. Standard T-API may include, for example: `connection`, `connectivity-service-end-point` refinements, `routing-constraint`, `resilience-constraint`, `cost-characteristic`, other QoS/route constraints. Only name, end-point, states, and modulation-format are supported.
- **Connection**: T-API often models a “Connection” (actual path/route) separate from “ConnectivityService”. This twin does not expose a separate Connection resource; path is internal (and exposed only via `/internal/services/{uuid}`).
- **Spectrum / L0**: ✓ Connectivity-service responses include **frequency-slot** (nominal-central-frequency THz, slot-width GHz) when allocated. **GET tapi-photonic-media:spectrum-context** returns grid parameters (num-slots, slot-width-ghz, nominal-central-frequency-thz). Spectrum assignment remains first-fit at create time; no client-specified slot in POST.

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
| **Modulation format** | Supported at create time only; PATCH does not allow changing modulation-format (documented as intentional). |
| **QoT in T-API** | No standard T-API “path computation result” or “connectivity with QoT” response; QoT is internal and exposed via `/internal/opm`. |
| **Versioning / capability** | No RESTCONF `restconf/data/` or `yang-library-version`-style capability discovery; no explicit T-API version in responses. |

### 2.5 gNMI-specific gaps

| Gap | Notes |
|-----|--------|
| **Path coverage** | `_PATH_MAP` and subscriptions only cover topology and context paths. No gNMI paths for connectivity or OPM (as in IMPLEMENTATION_PLAN.md). |
| **Get RPC** | gNMI Get is not implemented (returns UNIMPLEMENTED). |
| **Set RPC** | gNMI Set is not implemented. |

---

## 3. Summary Table

| Category | Implemented | Gaps |
|----------|-------------|------|
| **T-API modules** | Common, Topology, Connectivity (read + connectivity CRUD) | Path Computation, Virtual Network, Equipment, OAM, Fault, Notification, Streaming |
| **RESTCONF** | Paths, JSON, content-type on `/data/` | content/depth/filter/with-defaults, error format, PUT, full PATCH/merge semantics |
| **Data model** | Core topology + connectivity + SIP | Connection resource, spectrum in API, topology quality/latency, full ConnectivityService/end-point attributes |
| **Behavior** | Create/read/update/delete connectivity; path and spectrum internal | State/lifecycle semantics, modulation-format update, standard path/QoT exposure |
| **gNMI** | Capabilities, Subscribe (topology/context) | Get, Set, connectivity and OPM paths |

---

## 4. References

- **T-API 2.6**: ONF/ONMI TAPI 2.6 (YANG, OAS, path computation, virtual network, equipment, OAM, fault, notification).
- **RESTCONF**: RFC 8040 (and updates); query parameters and error format.
- **Project**: `CLAUDE.md` (architecture, endpoints, constraints), `IMPLEMENTATION_PLAN.md` (planned work).
