# TwinLight under ONOS — hierarchical orchestration demo

ONOS as the hierarchical controller, the TwinLight digital twin as the
optical domain beneath it. ONOS discovers the twin as an ODTN **open line
system (OLS)**, provisions lightpaths across it over T-API, and the twin answers
with real GNPy physics — including refusing requests that will not close.

```
            ONOS 2.7  (:8181 REST + GUI, :8101 CLI)
                 │  ODTN "ols" driver, REST southbound
                 │  T-API v2.1 over /restconf/data/…
                 ▼
     T-API adapter  (:8282)          ← integrations/onos/adapter/tapi_adapter.py
                 │  T-API v2.6.0 over /data/…
                 ▼
       TwinLight  (:8080 REST, :50051 gNMI)
                 │  GNPy split-step propagation, RMSA, four transient models
                 ▼
     Prometheus (:9090) → Grafana (:3000)     TwinLight UI (:5173)
```

---

## Why there is an adapter

ONOS's ODTN driver was written against **T-API v2.1** in 2018 and has not moved
since; TwinLight serves **T-API v2.6.0**. The adapter is the version bridge. It
is deliberately outside `src/twinlight`, because the reshaping it performs is
*not* standard T-API and
[CLAUDE.md constraint #1](../../CLAUDE.md) keeps the twin's T-API modules pure.

Four concrete incompatibilities, all read off the ONOS driver source rather than
any specification:

| # | What ONOS does | Why TwinLight alone does not satisfy it |
|---|---|---|
| 1 | Derives the ONOS **port number** from the last dash-segment of the SIP UUID, via `PortNumber.portNumber(String)` → `UnsignedLongs.decode()` | TwinLight's SIP UUIDs are `uuid5`, so that segment is hex (`40965af0c942`) and `decode()` throws `NumberFormatException`. Port discovery fails silently and the device shows **zero ports**. The adapter republishes each SIP as `<real-uuid>-<index>` and strips the suffix on the way back. |
| 2 | Dereferences `tapi-photonic-media:media-channel-service-interface-point-spec` → `mc-pool` → `available-spectrum` with no null check | TwinLight's SIPs carry no `mc-pool`; `docs/TAPI_COMPLIANCE.md` lists Photonic Media as partial. The adapter synthesises it from the twin's real spectrum context. |
| 3 | `TapiDeviceLambdaQuery` reads `mc-pool` from the **top level** of the per-SIP response | TwinLight wraps that resource in `{"tapi-common:context": {"service-interface-point": [...]}}`. The adapter serves it unwrapped. |
| 4 | POSTs a connectivity-service as a **JSON list**, with `service-layer`/`service-type` and no modulation format | TwinLight expects a single object and a `modulation-format`. The adapter translates, and reuses ONOS's UUID as the twin's service UUID so the later DELETE lines up. |

Everything else — paths, hyphenated keys, RESTCONF error bodies — passes through
unchanged, because TwinLight was already standards-correct there.

Two of those four (#2 and, in part, #3) are genuine T-API gaps in TwinLight that
would be better fixed in the twin than worked around here; the other two are
ONOS deviations that must stay in the adapter forever.
[COMPATIBILITY.md](COMPATIBILITY.md) works through each one and says which is
which, along with an open modelling question the demo deliberately does not
hide.

> **Two corrections to the integration advice circulating for ONOS + T-API.**
> The driver is named **`ols`**, not `tapi`; there is no
> `org.onosproject.drivers.tapi` app — the TAPI behaviours live in the
> `odtn-driver` bundle that `odtn-service` pulls in. And the netcfg `rest`
> block must **not** contain a `url` key: ONOS treats it as a *prefix* prepended
> to every request path (`RestSBControllerImpl.getUrlString`), so setting it to
> `/restconf/data/tapi-common:context` produces doubled paths. Use `testUrl`
> for the reachability probe instead.

---

## Which direction changes flow

Worth knowing before the talk, because the asymmetry is total and someone will
try the wrong direction live.

| Change | Reaches the other side? |
|---|---|
| Lightpath provisioned **from ONOS** | **Yes, immediately.** Flow rule → T-API POST → twin admits or refuses. Visible in the twin's UI, `/internal/opm`, Prometheus and Grafana within one poll. |
| Lightpath created **in the TwinLight UI** | **No — never**, and no setting changes that. |
| Fiber cut via `/config/` | Not as an event. The twin reports `status=link-failed` and its OPM collapses; ONOS keeps the device up and the flow installed. |
| SIP list / spectrum changes | Only after an explicit resync (below). |

**ONOS → twin works; twin → ONOS does not.** Two upstream behaviours cause
this, neither configurable:

1. `TapiFlowRuleProgrammable.getFlowEntries()` reads ONOS's own
   `DeviceConnectionCache`, never the device. The call that would read the
   device's connectivity services back is commented out upstream:

   ```java
   //TODO this is a blocking call on ADVA OLS, right now using cache.
   //return getFlowsFromConnectivityServices(deviceId);
   ```

   So a lightpath created anywhere but through ONOS cannot appear in the Flows
   view. This is a hard limit, not a configuration gap.

2. `RestDeviceProvider.checkAndUpdateDevice()` re-runs port discovery **only
   when the port list is already empty**:

   ```java
   //if ports are not discovered, retry the discovery
   if (deviceService.getPorts(deviceId).isEmpty()) { discoverPorts(deviceId); }
   ```

   So after the initial 75 ports are found, `discoverPortDetails()` is never
   called again and no change to the SIP list reaches ONOS on its own.

### Forcing a resync

```bash
./integrations/onos/scripts/resync.sh          # bounce the device, re-run port discovery
./integrations/onos/scripts/resync.sh --check  # compare what each side holds, change nothing
```

This removes and re-adds the device in netcfg, which is the supported way to
make ONOS re-run discovery. It refreshes **the port list and its annotations**.
It does **not** surface twin-created lightpaths — nothing can.

It is also the one operation that can tear down ONOS-created lightpaths, via
`removeInitalConnectivityServices()`, if ONOS's flow cache happens to be empty
at reconnect. In testing the cache survived and the lightpaths did too, but the
script checks afterwards and tells you if they did not.

This asymmetry is not a flaw in the demo — it is the honest state of ODTN's
`ols` driver, and saying so is more interesting than pretending otherwise. The
demo's thesis is that **ONOS is the orchestrator**: provisioning originates
there, and the twin is the physics authority that answers. Creating lightpaths
in the TwinLight UI is an out-of-band change, which in a real network would also
require a controller resync.

## Prerequisites

- Docker with Compose v2 (Docker Desktop ≥ 4.30 is fine)
- Python 3 and `curl` on the host (used by the scripts)
- ~6 GB free RAM and ~4 GB disk
- Optional: `sshpass` for non-interactive ONOS CLI access
  (`brew install hudochenkov/sshpass/sshpass`)

**On Apple Silicon:** ONOS publishes `linux/amd64` images only — there is no
arm64 tag for any release — so it runs under Rosetta, as the twin image already
does. Karaf boot takes **2–4 minutes** rather than ~40 seconds. This is normal
and the scripts' timeouts account for it. Give Docker Desktop at least 6 GB of
memory, or ONOS will OOM mid-boot.

---

## Quick start

**Every command in this document is run from the repository root.**

```bash
./integrations/onos/scripts/demo-up.sh     # builds + boots everything, registers the device
./integrations/onos/scripts/validate.sh    # 16 checks; green means the demo will work
./integrations/onos/scripts/seed-demo.sh   # optional: provision 8 lightpaths to explore
```

`demo-up.sh` is idempotent — re-run it freely. First run takes 5–10 minutes
(image pulls, the amd64 twin build, ONOS boot); later runs are much quicker.

### Driving Compose directly

`demo-up.sh` wraps Compose and additionally waits for ONOS, activates the ODTN
apps and pushes the netcfg — so prefer it. If you want the raw command, the ONOS
file is an **overlay** on the root stack and both files must be passed together,
from the repository root:

```bash
docker compose -f docker-compose.yml \
               -f integrations/onos/docker-compose.onos.yml up -d --build
```

Order matters and the working directory matters: Compose takes its project
directory from the **first** `-f` file, and the adapter's build context
(`./integrations/onos/adapter`) is resolved against it. Running this from inside
`integrations/onos/` will not work.

Bringing the stack up this way leaves the device unregistered in ONOS, so run
`./integrations/onos/scripts/demo-up.sh` afterwards — or push the netcfg
yourself, as [netcfg/README.md](netcfg/README.md) describes.

When it finishes:

| What | Where | Credentials |
|------|-------|-------------|
| ONOS GUI | <http://localhost:8181/onos/ui> | `onos` / `rocks` |
| ONOS CLI | `./integrations/onos/scripts/onos-cli.sh` | `onos` / `rocks` |
| TwinLight UI | <http://localhost:5173> | — |
| Grafana | <http://localhost:3000> | `admin` / `admin` |
| Adapter status | <http://localhost:8282/adapter/status> | — |
| Twin API docs | <http://localhost:8080/docs> | — |

Tear down with `./integrations/onos/scripts/demo-down.sh` (add `--purge` to drop volumes).

---

## The demo, in three acts

Roughly 8 minutes at a conference pace. Have four things on screen: a terminal,
the ONOS GUI, the TwinLight UI, and Grafana.

### Act 1 — ONOS discovers the twin (≈2 min)

*The claim: a standard SDN controller sees a digital twin as a real optical
domain, with no twin-specific code in the controller.*

```bash
./integrations/onos/scripts/onos-cli.sh devices
./integrations/onos/scripts/onos-cli.sh ports rest:172.28.0.10:8282
```

`devices` shows one device of type `OLS`. `ports` shows **75 OCh ports**, one per
CORONET CONUS transceiver, each annotated with the twin's real T-API SIP UUID
and carrying a DWDM lambda set derived from the twin's own spectrum context.

Point out in the ONOS GUI that this is an ordinary ONOS device — the controller
does not know or care that the domain beneath it is simulated.

**Finding the ports in the GUI**, which is not where most people first look: the
**Topology** view shows the OLS as a single node and does not draw ports at all,
so it looks empty. Use **Devices** in the left nav instead — the table has a
*Ports* column, and clicking the row opens a detail panel whose ports icon lists
all 75 with their lambda and SIP-UUID annotations. If a view ever looks stale,
`curl -u onos:rocks localhost:8181/onos/v1/devices/rest:172.28.0.10:8282/ports`
is the authoritative answer.

To map port numbers back to cities:

```bash
curl -s localhost:8282/adapter/ports | python3 -m json.tool | head -20
```

Ports are numbered 1–75 alphabetically by city, so port 1 is `trx Abilene` and
port 4 is `trx Atlanta`.

### Act 2 — ONOS provisions a lightpath, the twin supplies the physics (≈3 min)

*The claim: the orchestrator decides, the twin evaluates. Provisioning is a real
RMSA + QoT admission, not a bookkeeping entry.*

```bash
./integrations/onos/scripts/lightpath.sh create Abilene Atlanta
```

What happens, and it is worth narrating:

1. The script pushes an **ONOS flow rule** on the OLS device (in-port 1 →
   out-port 4). Nothing here touches the twin's API.
2. ONOS's `TapiFlowRuleProgrammable` turns that into a T-API
   **connectivity-service POST**.
3. The twin routes it (k-shortest paths over 2149 km, 9 hops), first-fits
   spectrum, runs GNPy split-step propagation, and checks GSNR against the
   DP-QPSK threshold plus the configured margin.
4. It admits the request and returns the allocated `frequency-slot`.

The script prints the allocation and then the live OPM:

```
  ADMITTED by the twin
    modulation    : DP-QPSK
    centre freq   : 190.725 THz
==> Twin optical performance monitoring
    f07163a5   2148.7 km  GSNR   2.64 dB  OSNR   4.19 dB  Q -0.61 dB  BER 1.76e-01
```

Now switch to the **TwinLight UI** — the lightpath is on the map and in the
spectrum heat map — and to **Grafana**, where the OPM series has started moving.
Poll it twice: the numbers change, because the four transient models are
re-evaluated at read time rather than cached.

**The full sequence, if you want to show it live rather than run the script.**
Have the twin's *Monitoring* page open on the projector before you start — it is
the only page that auto-refreshes, on the interval set in Settings. The
*Services* page does not poll, so reload it after provisioning.

```bash
# 1. establish that the twin has no such lightpath
curl -s localhost:8080/data/tapi-connectivity:connectivity-context/connectivity-service \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["tapi-connectivity:connectivity-context"]["connectivity-service"]), "services")'

# 2. provision it FROM ONOS — a flow rule, nothing T-API in sight
./integrations/onos/scripts/lightpath.sh create Boston New_York

# 3. the same lightpath, now on the twin, with GNPy physics attached
./integrations/onos/scripts/correlate.sh
```

Then point at the twin's Services page: a new entry named
`ONOS port 11->40  (Boston -> New_York)`, with a path, a frequency slot and live
OPM that ONOS never supplied and could not have computed.

**Tying the two views together.** ONOS and the twin share no identifier: ONOS's
flow id never leaves ONOS, and the T-API service UUID the driver generates never
appears in the Flows view. What they do share is the **in/out port pair**, so
that is the join key. The adapter stamps it into the service's T-API name, which
is what the TwinLight UI renders — a flow shown in ONOS as `2 -> 6` appears in
the twin's service list as:

```
ONOS port 2->6  (Albany -> Baltimore)
```

For the full correspondence, including ONOS's flow ids and the twin's physics in
one table:

```bash
./integrations/onos/scripts/correlate.sh
```

```
  ONOS FLOW ID         ONOS STATE   PORTS   LIGHTPATH                  SERVICE          KM  GSNR dB        BER
  ----------------------------------------------------------------------------------------------------------
  48976649027695507    ADDED        32->58  Los_Angeles -> San_Diego   b12fa809      223.8    20.87    2.0e-28
  48976650132659142    ADDED        2->6    Albany -> Baltimore        eca1f799      830.6    11.43    1.9e-04
  48976647806749592    ADDED        1->4    Abilene -> Atlanta         761bf68f     2148.7     3.02    1.6e-01
```

A flow with no twin service in that table is one the twin refused.

> **Worth being explicit about on stage**, because someone will ask: admission
> gates on the *pristine GNPy baseline* (11.9 dB here), while `/internal/opm`
> reports the baseline **after** the transient layer, which is why the live GSNR
> is much lower than the number the admission decision used. That is TwinLight's
> existing design, not an artefact of the ONOS path.

### Act 3 — the twin refuses an infeasible request (≈2 min)

*The claim — the interesting one: the twin is not a yes-machine. A digital twin
in the control loop can reject a request the controller would otherwise have
provisioned into a failure.*

ONOS's T-API 2.1 connectivity request has nowhere to carry a modulation format,
so it is adapter policy. Raise it to DP-16QAM and ask for the same path:

```bash
curl -s -X POST localhost:8282/adapter/modulation \
     -H 'Content-Type: application/json' \
     -d '{"modulation-format":"DP-16QAM"}'

./integrations/onos/scripts/lightpath.sh create Abilene Atlanta
```

```
  REFUSED by the twin (rmsa-qot-refused)
    Path GSNR 11.9 dB below required 16.0 dB (req 14.5 + margin 1.5)
```

The twin returns RFC 8040 `409 resource-denied`, the adapter relays it
unchanged, and ONOS leaves the flow rule stuck in `PENDING_ADD`. The same A–Z
pair that closes at DP-QPSK does not close at DP-16QAM, and the twin is what
knows that.

> **Read `PENDING_ADD` carefully — it is not by itself a refusal.** Every flow
> starts there, and an *admitted* one transitions to `ADDED` on ONOS's next
> reconciliation poll, which took about a minute in testing. A refused flow is
> one that is *still* `PENDING_ADD` after that. The unambiguous signal is
> `./integrations/onos/scripts/correlate.sh`: a refused flow has no twin service against it,
> and the reason is spelled out at `localhost:8282/adapter/status`.

For a sharper version of the same point, try a *marginal* pair instead — this
one misses by 0.2 dB, which makes it obvious the twin is doing real arithmetic
rather than applying a distance cutoff:

```bash
./integrations/onos/scripts/lightpath.sh create Albany Baltimore
#   REFUSED by the twin (rmsa-qot-refused)
#     Path GSNR 15.8 dB below required 16.0 dB (req 14.5 + margin 1.5)
```

Reset with:

```bash
curl -s -X POST localhost:8282/adapter/modulation \
     -H 'Content-Type: application/json' -d '{"modulation-format":"DP-QPSK"}'
```

> **Expect a delayed encore.** A rule left in `PENDING_ADD` is retried by ONOS,
> so once modulation is back at DP-QPSK the previously-refused flow may install
> itself a few seconds later and appear as a new lightpath. That is ONOS's
> normal flow-reconciliation behaviour and is worth pointing at rather than
> being surprised by. `./integrations/onos/scripts/lightpath.sh clear` between acts avoids
> it.

### Act 4 (optional) — fiber cut (≈1 min)

```bash
./integrations/onos/scripts/lightpath.sh create Albany Baltimore
./integrations/onos/scripts/fault.sh cut-path <service-uuid>      # uuid from the output above
```

The twin's `/config/` plane marks the fiber failed, invalidates every affected
baseline, and OPM for the lightpath collapses to `status=link-failed`. Grafana
shows the drop immediately.

Restore with `./integrations/onos/scripts/fault.sh heal-all`.

> **Be honest about the boundary here.** ONOS is *not* notified of the fault
> through a standard interface: T-API v2.6 defines a `tapi-fault` module and
> TwinLight does not implement it (see
> [docs/TAPI_COMPLIANCE.md](../../docs/TAPI_COMPLIANCE.md)). ONOS keeps the device
> up and the flow installed; what changes is the physics the twin reports. That
> gap — closing the loop from twin-detected impairment back to controller
> re-optimisation — is the natural next step, and a good thing to say out loud
> rather than let someone find.

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/demo-up.sh` | Build and boot the stack, verify the ODTN apps, push the netcfg, wait for discovery |
| `scripts/validate.sh` | 16 end-to-end checks across all layers; non-zero exit on any failure |
| `scripts/seed-demo.sh` | Provision 8 lightpaths through ONOS spanning the QoT range, so every UI has state to show (`--reset` clears first) |
| `scripts/lightpath.sh` | `create <A> <Z>` / `list` / `delete <flow-id>` / `clear` — provisioning driven from ONOS |
| `scripts/correlate.sh` | Join the ONOS Flows view to the twin's service list, one row per flow (`--json` for the raw join) |
| `scripts/resync.sh` | Force ONOS to re-discover ports (`--check` just compares the two sides) |
| `scripts/fault.sh` | `list` / `cut <uid>` / `cut-path <svc-uuid>` / `heal <uid>` / `heal-all` |
| `scripts/onos-cli.sh` | ONOS Karaf CLI, interactive or one-shot |
| `scripts/demo-down.sh` | Stop the stack (`--purge` also drops volumes) |

Overridable environment variables: `ONOS_URL`, `ONOS_AUTH`, `ADAPTER_URL`,
`TWIN_URL`, `DEVICE_ID`, and `VALIDATE_A` / `VALIDATE_Z` for the validation A–Z
pair.

## Adapter endpoints

RESTCONF surface, polled by ONOS — do not call these by hand during a demo, as
ONOS's port cache depends on them being consistent:

- `GET /restconf/data/tapi-common:context`
- `GET /restconf/data/tapi-common:context/service-interface-point={uuid}`
- `GET|POST /restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/`
- `DELETE …/connectivity-context/connectivity-service={uuid}`

Introspection, safe to call any time:

- `GET /health` — liveness plus twin reachability
- `GET /adapter/status` — modulation policy, ONOS-created services, recent refusals
- `GET /adapter/ports` — the SIP ↔ ONOS-port map
- `POST /adapter/modulation` — switch modulation format at runtime

## Configuration

`integrations/onos/docker-compose.onos.yml` environment, on the `tapi-adapter` service:

| Variable | Default | Notes |
|----------|---------|-------|
| `TWIN_BASE_URL` | `http://twin:8080` | |
| `ADAPTER_MODULATION` | `DP-QPSK` | Requested format; runtime-switchable |
| `ADAPTER_GRID_GRANULARITY` | `G_50GHZ` | **Only `G_50GHZ` and `G_25GHZ` are safe** — ONOS's `getChannelSpacing()` has trailing spaces in its other case labels (`"G_100GHZ "`, `"G_12_5GHZ "`), so those fall through to `CHL_0GHZ` and then divide by zero in `getOchSignal()` |
| `ADAPTER_EXPOSE_TWIN_SERVICES` | `false` | See below |
| `ADAPTER_SIP_REFRESH_SECONDS` | `30` | SIP catalogue cache lifetime |

### Why twin-native lightpaths are hidden from ONOS

`TapiDeviceHelper.removeInitalConnectivityServices()` deletes **every**
connectivity service it can see whenever ONOS's flow cache for the device is
empty — which includes the moment it first connects, and again after any ONOS
restart. Left exposed, that silently tears down anything the TwinLight UI or
`examples/demo_services.py` created. The adapter therefore shows ONOS only the
services ONOS itself created. Set `ADAPTER_EXPOSE_TWIN_SERVICES=true` if you
specifically want to demonstrate that behaviour.

---

## Troubleshooting

**Device appears but has zero ports.** The usual cause is check 5 or 6 in
`validate.sh` failing — a SIP without an `mc-pool`, or a UUID whose last segment
is not a plain decimal. Confirm with:

```bash
curl -s localhost:8282/restconf/data/tapi-common:context | python3 -m json.tool | head -40
docker logs twinlight-onos 2>&1 | grep -i "tapi\|ols\|NumberFormat"
```

**Device never becomes `available`.** ONOS cannot reach the adapter. The netcfg
IP must match the adapter's static address — both are `172.28.0.10` and must
stay in sync between `docker-compose.onos.yml` and `netcfg/twinlight-ols.json`.
A compose service name will not work there: ONOS parses that field as an
`IpAddress`.

**`ols` driver missing.** `org.onosproject.odtn-service` did not activate.
`./integrations/onos/scripts/onos-cli.sh apps -a -s` to check, then
`app activate org.onosproject.odtn-service`.

**Flow rule stays `PENDING_ADD`.** Expected when the twin refused the request —
that is Act 3, not a fault. Confirm the reason at
`localhost:8282/adapter/status` under `recent-rejections`.

**ONOS OOMs or boots very slowly.** Give Docker Desktop ≥ 6 GB. Under Rosetta,
2–4 minutes to a usable CLI is normal.

**`ssh` to port 8101 refuses to negotiate.** ONOS 2.7 ships an older Karaf SSHD.
`onos-cli.sh` already passes the needed `+ssh-rsa` / `diffie-hellman-group14-sha1`
options; if you connect by hand you will need them too.

---

## What this demo does not claim

Stated plainly so nobody has to discover it during questions:

- **The adapter is a real component, not a formality.** ONOS is talking T-API
  2.1 to a shim, which talks T-API 2.6 to the twin. Without it, ONOS discovers
  the device and zero ports.
- **No fault notification path.** No `tapi-fault`, no T-API notifications, no
  gNMI Get/Set. ONOS learns nothing about impairments; only the twin's own
  observability surfaces them.
- **ONOS sees one OLS device, not the 75-node graph.** That is how ODTN models
  a line system — the twin's topology stays inside the domain. The T-API
  topology context is served at `/data/tapi-common:context/tapi-topology:topology-context`
  but ONOS's `ols` driver does not consume it.
- **No authentication anywhere.** The twin, the adapter and the ONOS northbound
  are all unauthenticated. Demo only.
- **Modulation format is adapter policy**, because T-API 2.1 connectivity
  requests cannot express one. A 2.6-native controller would not need that.
