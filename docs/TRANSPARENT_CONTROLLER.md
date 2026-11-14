# The transparent optical domain controller

TwinLight's T-API northbound is the *abstracted* view of the network that an
optical domain controller (ODC) shows its clients: service-interface points,
nodes, links and connectivity services. The physical layer stays hidden behind
them. A *fully transparent* ODC also exposes every element and every parameter
it models, so an upper orchestrator — or an experimenter — can plan, check and
apply physical-layer changes itself.

TwinLight serves both at once. T-API stays where it is, under
`/restconf/data/…`; the transparent view is a separate path, **`/transparent`**,
with:

- **one data tree** holding every element of the physical backend's network
  (designed boosters and fiber sub-spans included), every parameter the twin
  models, and the twin's own model configuration;
- **ns-3-style paths** to address any part of it, keyed by element UIDs rather
  than T-API UUIDs;
- **a RESTCONF-shaped protocol** — NMDA datastores (running, candidate,
  startup, operational), YANG Patch transactions, entity-tags, RESTCONF error
  bodies, and `commit` / `validate` / `compare` operations, including a QoT
  what-if.

Nothing in it bypasses the twin. Every write goes through the same
`TapiContext` methods as `/config`, so baselines are invalidated, snapshots
capture the change, and T-API, OPM, gNMI and Prometheus all reflect it.

- [Quick tour](#quick-tour)
- [The data tree](#the-data-tree)
- [Paths](#paths)
- [Protocol](#protocol)
- [Datastores](#datastores)
- [What is writable](#what-is-writable)
- [The QoT what-if](#the-qot-what-if)
- [Clients](#clients)
- [Scenario: one maintenance window](#scenario-one-maintenance-window)
- [How it differs from RESTCONF and ns-3](#how-it-differs-from-restconf-and-ns-3)

---

## Quick tour

Against the two-site example (`twinlight --config examples/twin_config.yaml`).
`curl -g` stops curl from expanding `[…]` and `{…}` itself.

```bash
# Discover: datastores, operations, backend
curl -s localhost:8080/transparent

# Every writable parameter of every amplifier, one line each
curl -sg 'localhost:8080/transparent/data/ElementList/*/$Edfa?format=leaves&content=config'

# The inline elements of a link, in propagation order
curl -sg 'localhost:8080/transparent/data/LinkList/Site_A/Site_B/ElementList?depth=2'

# Set every fiber's input connector loss, now (204, or 200 with Prefer)
curl -sg -X PATCH 'localhost:8080/transparent/data/ElementList/*/$Fiber' \
     -H 'Content-Type: application/yang-data+json' \
     -H 'Prefer: return=representation' -d '{"con_in": 1.0}'

# Stage a change, predict its QoT impact, commit it
curl -sg -X PATCH 'localhost:8080/transparent/ds/candidate/ElementList/Edfa1/p_max' -d '17'
curl -s -X POST localhost:8080/transparent/operations/validate \
     -d '{"input": {"with-qot-impact": true}}'
curl -s -X POST localhost:8080/transparent/operations/commit

# Everything you changed since startup, then put it all back
curl -s 'localhost:8080/transparent/ds/running?with-defaults=explicit'
curl -s -X DELETE localhost:8080/transparent/data
```

The same with the bundled CLI:

```bash
twinlight-transparent get '/ElementList/*/$Edfa' --leaves --content config
twinlight-transparent set --candidate '/ElementList/Edfa1/p_max=17'
twinlight-transparent validate --qot
twinlight-transparent commit
twinlight-transparent revert /
```

## The data tree

```
/
├── ElementList/<uid>                every element: Transceiver, Roadm, Fiber, Edfa
├── NodeList/<uid>                   the terminals (ROADMs, transceivers) — ns-3's NodeList
├── LinkList/<from>/<to>             a directional OMS between two terminals
│     from, to, length_km, failed, tapi_link_uuid
│     ElementList/<uid>              its inline elements, in propagation order
├── ServiceList/<uuid>               connectivity services (read-only here)
│     uuid, name, modulation_format, path, start_slot, num_slots,
│     central_frequency_thz, slot_width_ghz, required_gsnr_db
│     qot/                           status, gsnr_db, osnr_db, margin_db, cd_ps_nm,
│                                    pmd_ps, latency_ms, total_fiber_km
│     ElementList/<uid>              the elements of the allocated path, in order
├── ModulationList/<format>          transceiver mode table (read-only)
└── Config/<section>/…/<field>       the twin's model configuration (the YAML tree)
```

An element entry carries its state — `uid`, `type`, `type_variety` (GNPy),
`upstream`/`downstream` terminals (inline elements), `length_km` (fibers),
`services` crossing it, and the T-API cross-references `tapi_node_uuid`/`sips`
(terminals) — followed by its writable attributes.

Leaves are **config** (writable) or **state** (read-only), as in YANG. There are
only two kinds of config leaf, whichever branch reaches them:

| Config leaf | Canonical path | Written through |
|-------------|----------------|-----------------|
| An element attribute | `/ElementList/<uid>/<attr>` | `apply_element_overrides_atomic` (and `set_link_failed` for `failed`) |
| A twin-config field | `/Config/<section>/…/<field>` | `apply_twin_overrides` |

So `/LinkList/a/z/ElementList/x/loss_coef`,
`/ServiceList/s/ElementList/x/loss_coef` and `/ElementList/x/loss_coef` are one
leaf. `NodeList`, `LinkList` and `ServiceList` are *views*: a conventional
datastore (running, candidate, startup) read at `/` shows only the canonical
`ElementList` and `Config` branches, so each leaf appears once.

`GET /transparent/schema` describes every node and leaf — type, unit, bounds,
`config` flag, description, and for a read-only config field why it is
read-only. It is generated from the same specs the resolver uses, so it cannot
drift from the data.

## Paths

[ns-3](https://www.nsnam.org/docs/manual/html/attributes.html) reaches every
attribute of a simulation through a configuration path such as
`/NodeList/3/DeviceList/*/$ns3::WifiNetDevice/Phy/TxPowerStart`. The
transparent controller keeps that shape:

| ns-3 | TwinLight |
|------|-----------|
| Root namespaces `/NodeList`, `/ChannelList` | `/ElementList`, `/NodeList`, `/LinkList`, `/ServiceList`, `/ModulationList`, `/Config` |
| Index `3`, range `[0-3]`, wildcard `*`, alternatives `1\|3` | Index `[3]`, range `[0-3]`, any glob, alternatives `a\|b` — plus names, quoted literals and regexes |
| `/Names/<name>` (object name service) | List keys *are* names: element UIDs, service UUIDs or `service-name`s, format names |
| Object vectors (`DeviceList` under a node) | Nested lists: `/LinkList/<from>/<to>/ElementList/…` |
| Type cast `$ns3::WifiNetDevice` | `$Fiber`, `$Edfa\|Roadm` |
| Attribute `TxPowerStart` | Leaf `loss_coef` (the GNPy/YAML name, unchanged) |
| `Config::SetDefault`, `GlobalValue` | `/Config/<section>/<field>` |
| `Config::LookupMatches` | `GET …?format=leaves` |
| `Config::Set` | `PATCH` / `PUT` |

Every segment is a selector made of terms joined by `|`:

| Term | Example | Matches |
|------|---------|---------|
| Glob | `*`, `east*`, `roadm ?orlaix` | `fnmatch` |
| Quoted literal | `"fiber (A → B)-F_(1/2)"` | exactly that name; required when a name holds `/`, `\|`, `"` or glob characters |
| Regex | `~"^fiber \(Brest"` | `re.search` (unanchored) |
| Index / range | `[0]`, `[2-5]` | entries by position: sorted UIDs; creation order for services; propagation order for a link's elements |

Inside quotes `\"` and `\\` escape a quote and a backslash; other backslashes
are literal, so regex escapes need no doubling. The UID terms are those of
[`/config/parameters`](API.md#parameter-paths), so a selector means the same
in both places.

A `$` cast keeps only elements of those kinds **and** makes the leaves that
follow specific to them: `/ElementList/*/$Edfa/loss_coef` is an error, while
`/ElementList/*/loss_coef` simply matches the elements that have one. A
spelled-out name that exists nowhere along the path (`loss_coeff`) is an error;
a pattern that happens to match nothing (`loss*` on a ROADM) is not.

**In a URL**, each `/` is a separator except inside quotes, and `%2F` is
always a slash *inside* a segment. GNPy's sub-span UIDs therefore work either
way:

```
/transparent/data/ElementList/fiber%20(A%20%E2%86%92%20B)-F_(1%2F2)/loss_coef
/transparent/data/ElementList/"fiber (A → B)-F_(1/2)"/loss_coef
```

Encode `?` as `%3F` and `#` as `%23` in names — they start the query and the
fragment.

## Protocol

| RESTCONF feature | Here |
|------------------|------|
| Root resource (RFC 8040 §3.3) | `GET /transparent` — datastores, operations, schema location, backend |
| `{+restconf}/data` | `/transparent/data/<path>` — the operational view; writes go to running |
| NMDA datastores (RFC 8527) | `/transparent/ds/{running,candidate,startup,operational}/<path>` (also `ietf-datastores:running`, …) |
| `GET` query: `depth`, `content` (RFC 8040 §4.8) | `depth=1..65535\|unbounded`; `content=all\|config\|nonconfig` |
| `with-defaults` (RFC 6243) | `report-all` (default), `explicit` — only leaves set since startup, `trim` — only leaves that differ from startup |
| `PUT` | replace: set what the body names, revert every other config leaf under the target |
| `PATCH` (plain) | merge: set what the body names |
| `PATCH` + `application/yang-patch+json` (RFC 8072) | ordered edits (`merge`, `replace`, `remove`, `delete`) as one transaction; per-edit `yang-patch-status` |
| `DELETE` | revert every config leaf under the target to its startup value |
| `POST` (create) | `405` — the tree's lists mirror the network; create services through T-API |
| Entity-tags (§3.4.1) | `ETag` and `Last-Modified` on every data response; `If-Match` on writes and `commit` (`412` when stale); `If-None-Match` on `GET` (`304`) |
| Operations (`{+restconf}/operations`) | `GET /transparent/operations`; `POST /transparent/operations/<name>` with `{"input": {…}}`, answered with `{"twinlight-transparent:output": {…}}` |
| Errors (§7.1) | `{"ietf-restconf:errors": {"error": [{error-type, error-tag, error-path, error-message}]}}`, one entry per offending leaf |
| `Prefer: return=representation` (RFC 7240) | writes answer `204`; with this header, `200` and the applied changes plus the services whose baselines they invalidated |

A `GET` answers with the matched nodes as one tree rooted at `/`, keyed by path
segment, under `twinlight-transparent:data`. The JSON pointer to any value is
therefore its path, and the output of `GET …?content=config` can be edited and
sent straight back with `PATCH`. A body may be given bare (`{"loss_coef": 0.22}`,
or just `0.22` for a leaf) or wrapped in its target's name, RESTCONF style
(`{"twinlight-transparent:loss_coef": 0.22}`). With a wildcard target the same
relative body applies to every match: `PATCH /ElementList/*/$Edfa
{"p_max": 21}` sets every amplifier.

`format=leaves` returns a flat list instead — ns-3's `LookupMatches` — with
each leaf's `path`, `value`, type, unit and bounds, and for config leaves the
`canonical` path to write it by, whether it is `explicit`ly set, and its
`startup` value.

### Operations

| Operation | Input | Output |
|-----------|-------|--------|
| `commit` | — (honours `If-Match` on running) | the applied changes, invalidated services, and `running-changed-since-staged` |
| `discard-changes` | — | `discarded-edits` |
| `validate` | `with-qot-impact`; and either nothing (the candidate) or an inline `yang-patch` (checked against running, never staged) | the changes with their previous values, and with `with-qot-impact` the [QoT what-if](#the-qot-what-if) |
| `compare` | `source`, `target` (datastore names; default running → candidate) | `differences`: the YANG Patch that turns `source` into `target` (RFC 9144-inspired) |
| `redesign` | — | re-runs GNPy's `designed_network`; `501` under EGN |

`compare`'s output is a valid YANG Patch body: applying it to running
reproduces the target, override records included — an edit whose target value
is the startup value is a `remove`, not a `replace` with that value.

## Datastores

| Datastore | Holds | Writable |
|-----------|-------|----------|
| **operational** (`/data`) | every leaf, config and state — the live twin | writes go to running |
| **running** | config leaves at their live values | yes, immediately |
| **candidate** | running plus staged edits | yes; `commit` applies, `discard-changes` drops |
| **startup** | config leaves at their values when the twin started | no |

**Startup** is the network as the engine loaded and designed it, and the
configuration as the YAML file set it. A restored checkpoint or snapshot is
applied *after* startup, so its overrides show as running changes — and
`DELETE /transparent/data` takes the twin back to the YAML and topology files,
not to the checkpoint. Startup values are captured exactly as the engine stored
them, an unset value included (an amplifier tilt GNPy loaded as `None` comes
back as `None`).

**The candidate** is shared by every client, as NETCONF's is. Its edits are
absolute values, validated when staged — cross-field rules against the other
staged edits — and again at commit. A commit over a running datastore that moved
since the first edit was staged is allowed (the values are absolute) and
reported (`running-changed-since-staged`); clients that need stricter
guarantees send `If-Match` with the running ETag they validated against.

**The ETag** of running is a revision counter bumped by every configuration
change through **any** interface — `/transparent`, `/config/set`,
`/config/parameters`, a link failure, a restore. A client holding an ETag
therefore learns about a write made through the legacy `/config` plane as
surely as about one made here. Time-varying measurements (OPM) are not
configuration and do not change it; neither does creating or deleting a
service.

Every change set — a `PUT`, a `PATCH`, a YANG Patch, a `DELETE`, a commit — is
validated in full before anything is written, reports every offending leaf, and
lands atomically: element writes roll back if one fails, and validation has
already ruled out the twin-config and failure writes failing.

## What is writable

Under **GNPy**:

| Kind | Writable | Read-only, and why |
|------|----------|--------------------|
| Fiber | `loss_coef` (dB/km), `att_in`, `con_in`, `con_out` (dB), `pmd_coef` (ps/√km), `dispersion` (ps/(nm·km)), `failed` | `length_km` — latency, the T-API link delay and Raman coefficients are derived from it at load time. Effective area and gamma likewise; gamma is not even read by propagation. A frequency-dependent dispersion profile reads as unset and refuses a scalar write |
| Edfa | `gain_target`, `tilt_target`, `out_voa`, `in_voa`, `nf_offset` (dB), `p_max` (dBm), `pmd` (ps) | The equipment model (`nf_model`, gain limits); `nf0` is never read by propagation — use `nf_offset` (see [PHYSICS.md](PHYSICS.md#known-limitations)) |
| Roadm | `target_pch_out_db` (dBm) | Per-degree impairment profiles |
| Transceiver | — | Launch power and baud rate come from the modulation table |

Every writable attribute is read on every propagation.
`tests/test_physics/test_element_params_propagation.py` writes each of the
attributes added for the transparent controller and checks that the propagated
result moves; the original ones have their own tests in `tests/test_state/`.

Under **EGN**: Fiber `loss_coef`, `att_in` (accepted, no effect), `failed`;
Edfa `nf0`.

**Twin configuration**: every scalar field of `transients`, `rmsa` and `fec` —
the sections read live on each OPM sample or admission (34 fields). Bounds and
choices come from the pydantic model; the hard-decision code's cross-field rules
are checked on the section as it would be after the whole change. Four fields
that no code reads stay read-only, with the reason in the schema:
`transients.edfa_reservoir.solver_method`, `….solver_max_step_ms`,
`transients.polarization.pmd_coeff_ps_per_sqrt_km` (PMD comes from each fiber's
`pmd_coef`) and `….sop_drift_rate_rad_per_s`. Everything else (`gnpy`,
`physics`, `spectrum`, `server`, `simulation`, `logging`) is restart-only.

These are the same allow-lists `/config/set` and `/config/parameters` use — the
transparent controller widens what is reachable for every plane, not only for
itself.

**Services** are read-only here: their lifecycle belongs to T-API. `DELETE` on a
`ServiceList` entry reverts the parameters of the elements on its path — it
never deletes the service.

## The QoT what-if

`validate` with `with-qot-impact: true` predicts, for every service whose
allocated path crosses a changed element (here: the candidate of the scenario
below on the two-site example):

```json
{
  "service": "8d7c20eb-…", "name": "odc-scenario", "modulation_format": "DP-QPSK",
  "required_gsnr_db": 8.5, "qot_margin_db": 2.0,
  "current":   {"status": "ok", "gsnr_db": 27.5171, "osnr_db": 30.2392, "cd_ps_nm": 1336.0, "pmd_ps": 0.8485},
  "predicted": {"status": "ok", "gsnr_db": 27.0561, "osnr_db": 29.2394, "cd_ps_nm": 1336.0, "pmd_ps": 0.8485},
  "delta_gsnr_db": -0.461,
  "above_required": true,
  "meets_admission_margin": true
}
```

`above_required` answers *would the lightpath still work*; `meets_admission_margin`
answers *would the controller still admit it today* — judged against the
candidate's `rmsa.qot_margin_db` when the change moves it.

How it works (`TapiContext.preview_qot`): under the propagation lock, every
changed attribute is captured, written to the live element, the affected
allocated paths are propagated by the backend, and every attribute is restored
from its capture in a `finally`. The backends' propagation does not yield to the
event loop, so no other request can observe the tentative values; nothing is
cached and the revision does not move. Fiber failures are evaluated
hypothetically — a path through a fiber that would be cut reports
`link-failed` — so the routing graphs are never touched.

The prediction is of the **static baseline**: the QoT a commit would give the
next OPM read before the transient models are applied. The scenario below checks
it against the committed result; on both bundled topologies they agree to
within rounding. Transient models are not predicted — they vary with time by
design.

## Clients

- **Python**: `twinlight_client.TransparentClient` — `get`, `leaves`, `value`,
  `set`, `merge`, `replace`, `revert`, `yang_patch`, `commit`, `discard`,
  `validate`, `compare`, `redesign`, `etag`. Synchronous, with an injectable
  `httpx.Client`; refusals raise `TransparentError` with the RESTCONF error list.
- **Shell**: `twinlight-transparent` (`get`, `set`, `revert`, `commit`,
  `discard`, `validate --qot`, `compare`, `schema`, `redesign`; `--candidate`,
  `--if-match`). `set` and `revert` send one YANG Patch, so several paths land
  together or not at all.

## Scenario: one maintenance window

[`examples/transparent_odc_scenario.py`](../examples/transparent_odc_scenario.py)
plays an orchestrator against a running twin:

1. discover the controller;
2. inventory the network through the transparent tree — no T-API objects;
3. provision a lightpath through T-API, the abstracted interface;
4. look at it, and every parameter of its first link, transparently;
5. stage span ageing (+1 dB over the link), a degraded amplifier pump, and a
   stricter admission margin in the candidate. Under GNPy the pump loses 6 dB
   of `p_max`; EGN has no `p_max`, so there the fault is a 2 dB higher `nf0`;
6. validate with the QoT what-if and compare candidate with running;
7. commit guarded by the running ETag — losing a race to a concurrent
   `/config/set` write, then retrying;
8. check the committed result against the prediction;
9. ask what a fiber cut would do, without cutting anything;
10. list everything that differs from startup, then roll it all back.

```bash
twinlight --config examples/twin_config.yaml &
python examples/transparent_odc_scenario.py
```

Abridged output on the two-site example:

```
 5. Stage a maintenance change in the candidate datastore
   staged /ElementList/Span1/loss_coef: 0.2 -> 0.2125 dB/km
   staged /ElementList/Edfa1/p_max: 23.0 -> 17.0 dBm
   staged /Config/rmsa/qot_margin_db: 1.5 -> 2.0
   running is untouched: 0.2 dB/km on /ElementList/Span1/loss_coef
 6. Validate with a QoT what-if; compare candidate with running
   odc-scenario: GSNR 27.5171 dB -> 27.0561 (delta -0.461), required 8.5 dB,
   margin 2.0 dB: above required=True, admissible=True
 7. Commit, guarded by the running datastore's ETag
   read running ETag "r0"
   meanwhile, another operator changes rmsa.k_shortest_paths via /config/set
   commit refused: HTTP 412: operation-failed: The running datastore changed
   since the entity-tag you sent (now "r1"); re-read it and retry
   retried with "r1": committed 3 edits; invalidated ['eb9a514e-…'];
   running moved since staging: True
 8. Check the committed result against the prediction
   predicted 27.0561 dB, now 27.056148 dB
 9. What would a cut of the link's first fiber do? (nothing is cut)
   odc-scenario: GSNR 27.0561 dB -> link-failed, … above required=False
10. Everything that differs from startup, then roll back
   /Config/rmsa/k_shortest_paths: 4 (startup: 3)
   /Config/rmsa/qot_margin_db: 2.0 (startup: 1.5)
   /ElementList/Edfa1/p_max: 17.0 (startup: 23.0)
   /ElementList/Span1/loss_coef: 0.2125 (startup: 0.2)
   DELETE /transparent/data reverted 4 leaves
   running vs startup now: identical
```

On CORONET (`examples/coronet_conus_config.yaml`: 2088 elements, sub-span UIDs
such as `fiber (Abilene → El_Paso)-_(1/8)`) the same script runs unchanged in
about three seconds; reading the whole operational tree takes under a second.

## How it differs from RESTCONF and ns-3

The interface borrows shapes, not conformance. Deliberate differences:

- **No YANG module.** The schema is served as JSON (`/transparent/schema`), and
  the module name `twinlight-transparent` only namespaces the JSON members.
- **Lists are JSON objects keyed by name**, not RFC 7951 arrays, so a value's
  JSON pointer is its path; and paths may contain wildcards, which RESTCONF
  URIs cannot.
- **One ETag per datastore**, not per resource.
- **Nothing is created or deleted.** `create`/`insert`/`move` are refused;
  `delete` and `remove` both revert to startup, and neither fails on a leaf
  already at startup.
- **No locking, no confirmed commit**, no NETCONF, no event streams. The twin
  keeps no clock for parameter changes (see
  [ARCHITECTURE.md](ARCHITECTURE.md#changing-parameters-by-path)); a confirmed
  commit's rollback timer would be one. Live QoT values stream as T-API
  performance records over gNMI ([API.md](API.md#gnmi-streaming)); parameter
  values are read, not streamed.
- **ns-3 indices are positions in a sorted list**, not creation-order object
  IDs; UIDs and names are the primary keys.
- **No authentication**, like every other interface — see
  [SECURITY.md](../SECURITY.md). This interface can change everything the twin
  models.
