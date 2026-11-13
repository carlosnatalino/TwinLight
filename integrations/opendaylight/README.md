# TwinLight and OpenDaylight: investigation notes

> **Status: design note. There is no integration code here yet.** This page
> records what OpenDaylight offers for an integration with the twin, why the
> [ONOS pattern](../onos/README.md) does not carry over, and what the viable
> options are. Nothing under this directory is built or run.
>
> Checked in November 2026 against TransportPCE **13.0.0** (latest source tag),
> **13.1.0** (latest release on Maven Central) and `master` at
> [`86d361b`][master]. Upstream is moving, so re-check the
> [T-API southbound](#t-api-southbound-tapisbi) before acting on this.

---

## Summary

- **OpenDaylight's T-API lives in TransportPCE**, the ODL project for optical
  transport. ODL's core (MD-SAL, NETCONF, RESTCONF) does not depend on any
  particular YANG model. The T-API models and the code behind them ship with
  TransportPCE.
- **That T-API is northbound only.** TransportPCE *serves* T-API **2.4.0** to
  a controller or orchestrator above it, as an abstraction of the OpenROADM
  devices it manages over NETCONF. TwinLight also *serves* T-API (2.6.0). Two
  servers have nothing to say to each other.
- **The T-API southbound is an empty skeleton.** It is on `master` only, its
  RPCs return success without doing anything, and no release contains it. So
  the setup used for ONOS, where the controller drives the twin over T-API, is
  **not possible with any released OpenDaylight**.
- **One released interface does fit the twin: TransportPCE's GNPy hook.** Its
  path computation element (PCE) asks an external GNPy REST server whether a
  path's quality of transmission (QoT) will close. The twin could answer those
  calls through an adapter.
- **Docker:** there is no official TransportPCE image, but its released
  features are on Maven Central, so a controller image can probably be
  assembled from them in a multi-stage build. That build has not been tried.

## What OpenDaylight offers for optical transport

| Interface | Direction | Version | Status |
|---|---|---|---|
| T-API (`odl-transportpce-tapi`) | Northbound: TransportPCE is the server | T-API 2.4.0, RPC-based | Released (13.1.0) |
| T-API southbound (`tapisbi`) | Southbound: TransportPCE would be the client | T-API 2.4 | Skeleton on `master`; not released |
| GNPy client in the PCE | Outbound REST call to a QoT engine | GNPy API v1 | Released |
| OpenROADM / OpenConfig over NETCONF | Southbound to devices | OpenROADM 1.2.1, 2.2.1 and 7.1; OpenConfig | Released |

### T-API northbound

The `odl-transportpce-tapi` 13.1.0 feature on [Maven Central][central-tapi]
depends on `odl-tapi-2.4.0-models` 24.1.0. TransportPCE's model artifacts carry
only `tapi-2.1.1` and `tapi-2.4.0`.

The service is built from RPCs ([`tapi/.../impl/rpc`][tapi-rpc] at 13.0.0):
`create-connectivity-service`, `delete-connectivity-service`,
`get-topology-details`, `get-service-interface-point-list` and so on, served
under ODL's RFC 8040 root `/rests/operations/…`. A connectivity request goes
through TransportPCE's own pipeline (service handler → PCE → renderer →
NETCONF edit-config on the devices). The T-API topology is an abstraction of
TransportPCE's OpenROADM topology.

TwinLight serves T-API 2.6.0 as datastore resources (`POST
…/connectivity-context` and so on) and has no T-API RPCs. The two would not
interoperate even if one of them were a client.

> **The published developer guide is out of date.** The
> [TransportPCE developer guide][devguide] still describes T-API 2.1.2 (revision
> 2018-12-10), says nothing about a southbound, and does not document the GNPy
> REST contract. Everything on this page comes from source and release
> artifacts instead.

### T-API southbound (`tapisbi`)

This module would let TransportPCE drive a domain controller over T-API, which
is TwinLight's role under ONOS. It was added to `master` on 2026-04-15 by
[*Build skeleton for TAPI-SBI feature*][sbi-commit], and as of [`86d361b`][master]
it is still a skeleton:

- [`TapiSbiServiceImplementationRequestImpl.invoke()`][sbi-rpc] logs the
  service name and returns an empty success (`// TODO: provide implementation
  of this RPC`).
- [`TapiSbiRendererServiceImpl.createSbiService()`][sbi-renderer] and
  `deleteSbiService()` return `null`.
- [`TapiSbiNotificationSubcriptionService`][sbi-notif] is an empty class with
  the intended notification subtree in a comment.

The module is not in the 13.0.0 source tag, and there is no
`odl-transportpce-tapisbi` artifact on Maven Central.

One detail matters for any future work. The renderer's TODO says the service
should be created *"writing in the DataStore of the SouthBound NMS/Controller
without relying on TAPI RPcs so that it can scale to release higher than 2.4"*.
Datastore writes are how TwinLight's T-API 2.6 already works. If this module is
finished along those lines, it is the natural attachment point for the twin.

### GNPy hook in the PCE

From [`GnpyConsumerImpl`][gnpy-consumer] at 13.0.0 (unchanged on `master`):

| | |
|---|---|
| Liveness | `GET {url}/api/v1/status`. Only `200` counts as available |
| Validation | `POST {url}/api/v1/path-computation`, `Content-Type: application/json`. Only `201` is parsed; anything else is treated as "no answer" |
| Auth | HTTP Basic, default `gnpy` / `gnpy` |
| Configuration | OSGi config PID `org.opendaylight.transportpce.pce`, keys `url` (default `http://127.0.0.1:8008`), `username` and `password` |
| Request body | A `gnpy-api` `request` (YANG `gnpy-api@2022-02-21`), JSON-encoded, with the `gnpy-network-topology:` prefix removed |
| Verdict | [`GnpyResult`][gnpy-result] looks only at the response type: a `path` case is feasible and a `no-path` case is not |

How the PCE uses it ([`PceSendingPceRPCs.pathComputation()`][pce-flow]):

1. The PCE computes a path itself.
2. If GNPy is reachable, the PCE asks it whether that path is feasible.
3. If GNPy says no, the PCE asks GNPy for a new path with only the original
   constraints. It then recomputes in strict mode along that route and asks
   GNPy to verify again.
4. If GNPy is unreachable or throws, the PCE keeps its own path. GNPy is
   advisory.

**GNPy is consulted only for paths over the OpenROADM topology.** For a T-API
topology the code logs *"No GNPY path analysis on T-API topology : T-API Path
serializer not available yet"*.

**What TransportPCE sends.** [`GnpyTopoImpl`][gnpy-topo] builds the GNPy
topology from its own `openroadm-network` and `openroadm-topology`:

- ROADM and XPONDER nodes become `Roadm` and `Transceiver` elements, keyed by
  their `openroadm-network` node id.
- ROADM-to-ROADM links become `Fiber` elements (`SSMF`, length summed from the
  link's SRLG lengths) and `Edfa` elements.
- Placeholder coordinates and a fixed target power are used throughout.

That is a simplified physical description. An answer from the twin should take
the route from the request and the physics from the twin.

**No devices are needed to exercise it.** Upstream's own
[`test03_gnpy.py`][gnpy-test] loads a portmapping and the `clli-network`,
`openroadm-network` and `openroadm-topology` networks straight into the
datastore over RESTCONF, then calls `transportpce-pce:path-computation-request`.

### Devices on the southbound

TransportPCE manages OpenROADM and OpenConfig devices over NETCONF. At 13.0.0
there are functional test suites for OpenROADM 1.2.1, 2.2.1 and 7.1, for mixed
("hybrid") networks and for OpenConfig terminals (`oc`).

The tests use the **lightynode** simulator, a lighty.io NETCONF device that
serves one OpenROADM device per JVM. [`install_lightynode.sh`][lightynode-sh]
downloads it as a zip from a GitLab Maven package (default version
`22.1.0.7`); the [source is on GitLab][lightynode-src]. The older honeynode
simulators have a [Dockerfile][honeynode-docker] built on an OpenJDK 11 base.

## Why the ONOS pattern does not carry over

```
ONOS (works today)                         OpenDaylight (today)

  ONOS ── ODTN "ols" driver                  orchestrator
    │     = T-API CLIENT (2.1)                    │ T-API 2.4 (RPCs)
    ▼                                             ▼
  tapi-adapter  (2.1 ↔ 2.6)                  TransportPCE = T-API SERVER
    │                                             │
    ▼                                             │ NETCONF / OpenROADM
  TwinLight = T-API SERVER (2.6)                  ▼
                                             devices        TwinLight = T-API SERVER (2.6)
                                                             (no client on either side)
```

ONOS's ODTN driver is a T-API **client**, so a version adapter was enough. The
only T-API client in TransportPCE is the unfinished `tapisbi`.

## Integration options

| | A. Twin as QoT engine | B. A + emulated devices | C. Finish `tapisbi` upstream |
|---|---|---|---|
| ODL interface used | GNPy hook (released) | GNPy hook + NETCONF southbound (released) | T-API southbound (unreleased) |
| TransportPCE provisions lightpaths | No: path computation and QoT checks only | Yes | Yes |
| Topology size | Any, including CORONET | Small (about 2–4 ROADMs): one JVM per device | Any |
| Who holds the network state | Twin | Simulators and twin, kept in sync | Twin |
| Work | Medium | Large | Large, and upstream review is needed |
| Main risk | Mapping TransportPCE element ids to twin elements | Keeping two sources of truth consistent; simulator PM data is static | Upstream timeline; T-API 2.4 versus 2.6 |

### A. The twin as TransportPCE's QoT engine

```
  TransportPCE  (Karaf, :8181 /rests)
    │  PCE: path-computation-request
    │  GNPy client → POST /api/v1/path-computation
    ▼
  gnpy-adapter  (new, Python, outside src/twinlight)
    │  map the request's route onto twin elements, query twin QoT,
    │  answer `path` or `no-path`
    ▼
  TwinLight  (:8080)      topology-loader (one-shot): twin topology →
                          openroadm-network/topology + portmapping → RESTCONF PUT
```

**Components:**

- **TransportPCE container.** A Karaf distribution assembled from released
  13.1.0 features.
- **`gnpy-adapter`.** Implements the two GNPy endpoints above. Like the ONOS
  `tapi-adapter`, it is non-standard glue, so it lives here and not in
  `src/twinlight`.
- **One-shot loader.** Generates TransportPCE's OpenROADM networks from the
  twin's topology and PUTs them, as `test03_gnpy.py` does. Generating the node
  ids from twin UIDs makes the adapter's mapping a lookup rather than a guess.

**What it gives:** TransportPCE routes, and the twin decides whether each route
closes. This mirrors the published TransportPCE + GNPy work, with GNPy replaced
by the twin and its transient models.

**What it does not give:** provisioning. `service-create` reaches the renderer,
which needs NETCONF devices.

### B. A plus emulated OpenROADM devices

Add lightynode simulators configured from the twin's topology. TransportPCE
mounts them and links them with the `transportpce-networkutils` RPCs
(`init-roadm-nodes`, `init-xpdr-rdm-links`, `init-rdm-xpdr-links`). A sync agent
mirrors the cross-connects TransportPCE writes into twin connectivity services
on the same route and spectrum.

This is the only option that gives full provisioning with released software.
It costs one JVM per device, which rules out CORONET. It also means writing
device XML configurations, and reconciling two copies of the network state. The
optical-line manager (OLM) power setup reads performance data from the
simulators, which is static and not the twin's live OPM.

### C. Finish the T-API southbound upstream

Implement `tapisbi` in TransportPCE (a Java contribution through ODL Gerrit),
writing to the downstream controller's datastore as its TODO describes. The
twin would then sit under TransportPCE the way it sits under ONOS, with an
adapter for the 2.4 ↔ 2.6 differences. It has the best end state, but the
timeline belongs to upstream.

## Docker feasibility

| Component | Image | Status |
|---|---|---|
| TransportPCE controller | None official; no Karaf tarball on Nexus; no `transportpce-karaf` on Maven Central | **Unproven.** A Karaf assembly from Maven Central looks feasible: the 13.0.0 [`karaf/pom.xml`][karaf-pom] uses `karaf4-parent` 14.3.1, which is on Central along with every `odl-transportpce*` feature. Needs Java 21 (odlparent 14.3.1 targets 1.21). Java images are multi-arch, so it should run natively on arm64 |
| `gnpy-adapter`, loader | `python:3.12-slim` | No obstacle; same shape as the ONOS `tapi-adapter` |
| lightynode simulators (B only) | Downloadable zip; needs a small Java image | Feasible; one container per device |
| TwinLight | Existing `twin` service | Unchanged (`linux/amd64`, emulated on Apple Silicon) |

## Open questions

These need answering before building option A:

1. Does the Karaf assembly actually build and boot in Docker from Maven Central
   alone, without ODL's Nexus? This is the first thing to prove.
2. Does TransportPCE's T-API northbound populate its topology from OpenROADM
   networks PUT straight into the datastore, or only from device-connect
   events? This decides whether A also exposes the twin upward over T-API 2.4.
3. Exactly what JSON TransportPCE sends. The encoding is draft-lhotka YANG JSON
   with one prefix stripped, so the adapter should be built against captured
   requests, not the YANG alone.
4. Which modulation format and margin the adapter should judge feasibility
   against. TransportPCE's request carries no modulation the twin understands.
   This is the same policy question the ONOS adapter answers with
   `ADAPTER_MODULATION`.

[master]: https://github.com/opendaylight/transportpce/tree/86d361bc2892a4d78d4f196c00522554dc846bbb
[central-tapi]: https://repo1.maven.org/maven2/org/opendaylight/transportpce/odl-transportpce-tapi/13.1.0/
[tapi-rpc]: https://github.com/opendaylight/transportpce/tree/13.0.0/tapi/src/main/java/org/opendaylight/transportpce/tapi/impl/rpc
[devguide]: https://docs.opendaylight.org/projects/transportpce/en/latest/developer-guide.html
[sbi-commit]: https://github.com/opendaylight/transportpce/commit/b79765994b74473df03d2c69ffed474e9f435681
[sbi-rpc]: https://github.com/opendaylight/transportpce/blob/86d361bc2892a4d78d4f196c00522554dc846bbb/tapisbi/src/main/java/org/opendaylight/transportpce/tapisbi/rpcs/TapiSbiServiceImplementationRequestImpl.java
[sbi-renderer]: https://github.com/opendaylight/transportpce/blob/86d361bc2892a4d78d4f196c00522554dc846bbb/tapisbi/src/main/java/org/opendaylight/transportpce/tapisbi/renderer/TapiSbiRendererServiceImpl.java
[sbi-notif]: https://github.com/opendaylight/transportpce/blob/86d361bc2892a4d78d4f196c00522554dc846bbb/tapisbi/src/main/java/org/opendaylight/transportpce/tapisbi/listener/TapiSbiNotificationSubcriptionService.java
[gnpy-consumer]: https://github.com/opendaylight/transportpce/blob/13.0.0/pce/src/main/java/org/opendaylight/transportpce/pce/gnpy/consumer/GnpyConsumerImpl.java
[gnpy-result]: https://github.com/opendaylight/transportpce/blob/13.0.0/pce/src/main/java/org/opendaylight/transportpce/pce/gnpy/GnpyResult.java
[gnpy-topo]: https://github.com/opendaylight/transportpce/blob/13.0.0/pce/src/main/java/org/opendaylight/transportpce/pce/gnpy/GnpyTopoImpl.java
[pce-flow]: https://github.com/opendaylight/transportpce/blob/13.0.0/pce/src/main/java/org/opendaylight/transportpce/pce/PceSendingPceRPCs.java
[gnpy-test]: https://github.com/opendaylight/transportpce/blob/13.0.0/tests/transportpce_tests/pce/test03_gnpy.py
[lightynode-sh]: https://github.com/opendaylight/transportpce/blob/86d361bc2892a4d78d4f196c00522554dc846bbb/tests/install_lightynode.sh
[lightynode-src]: https://gitlab.com/Orange-OpenSource/lfn/odl/lightynode-simulator
[honeynode-docker]: https://github.com/opendaylight/transportpce/blob/13.0.0/tests/Xtesting/DockerSims/Dockerfile
[karaf-pom]: https://github.com/opendaylight/transportpce/blob/13.0.0/karaf/pom.xml
