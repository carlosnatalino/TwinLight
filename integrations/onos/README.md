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
                 │  T-API v2.6.0 over /restconf/data/… (root read from host-meta)
                 ▼
       TwinLight  (:8080 REST, :50051 gNMI)
                 │  GNPy split-step propagation, RMSA, four transient models
                 ▼
     Prometheus (:9090) → Grafana (:3000)     TwinLight UI (:5173)
```

---

## Why there is an adapter

ONOS's ODTN driver was written against **T-API v2.1** in 2018 and has not been
updated since; TwinLight serves **T-API v2.6.0**. The adapter bridges the two
versions. It lives outside `src/twinlight` because the reshaping it performs is
*not* standard T-API, and the twin's T-API modules implement only the standard
(see [CONTRIBUTING.md](../../CONTRIBUTING.md#t-api-surfaces-stay-standard)).

Four concrete incompatibilities, all read off the ONOS driver source rather than
any specification:

| # | What ONOS does | What the adapter does |
|---|---|---|
| 1 | Derives the ONOS **port number** from the last dash-segment of the SIP UUID, via `PortNumber.portNumber(String)` → `UnsignedLongs.decode()` | TwinLight's SIP UUIDs are RFC 4122 `uuid5` values, so that segment is hex (`40965af0c942`) and `decode()` throws `NumberFormatException` — port discovery fails silently and the device shows **zero ports**. The adapter republishes each SIP as `<real-uuid>-<index>` and strips the suffix on the way back. |
| 2 | Dereferences the T-API **2.1** `tapi-photonic-media:media-channel-service-interface-point-spec` → `mc-pool` → `available-spectrum`, with no null check | T-API 2.6 has no `mc-pool`; the twin publishes the same information as `photonic-media-service-interface-point-spec` → `spectrum-capability-pac`. The adapter translates it, including Hz → MHz and identityrefs → the bare tokens ONOS matches. |
| 3 | `TapiDeviceLambdaQuery` reads `mc-pool` from the **top level** of the per-SIP response | The twin answers that resource as RFC 8040 §4.3 and RFC 7951 §5.4 specify, `{"tapi-common:service-interface-point": [ {…} ]}`. The adapter serves ONOS the bare object. |
| 4 | POSTs a connectivity-service with `service-layer`/`service-type` and no modulation format, which T-API 2.1 has nowhere to carry | The adapter adds a modulation as policy, in the T-API 2.6 location (a `tapi-photonic-media` augment on the end-point), and reuses ONOS's UUID as the twin's service UUID so the later DELETE lines up. |

Those four, plus showing ONOS only the services it created
([COMPATIBILITY.md § A3](COMPATIBILITY.md#a3-onos-deletes-pre-existing-connectivity-services))
and the 50 GHz granularity it can parse (§ A6), are all that is left: each is
an ONOS T-API 2.1 behaviour, not a twin deviation. Everything else — paths,
hyphenated keys, the create on the connectivity-context, the array-of-one
create body (forwarded as ONOS sent it), RESTCONF error bodies — passes
through unchanged. Earlier releases also reshaped the create body into a bare
object and re-targeted the create to the list path; the twin's now standard
behaviour made both unnecessary, and they are gone.

[COMPATIBILITY.md](COMPATIBILITY.md) works through each incompatibility and
records, with the specification clause it rests on, whether the fix belongs in
the twin or in the adapter.

> **Two common configuration pitfalls with ONOS and T-API.**
> The driver is named **`ols`**, not `tapi`; there is no
> `org.onosproject.drivers.tapi` app — the TAPI behaviours live in the
> `odtn-driver` bundle that `odtn-service` pulls in. And the netcfg `rest`
> block must **not** contain a `url` key: ONOS treats it as a *prefix* prepended
> to every request path (`RestSBControllerImpl.getUrlString`), so setting it to
> `/restconf/data/tapi-common:context` produces doubled paths. Use `testUrl`
> for the reachability probe instead.

---

## Which direction changes flow

Changes propagate in one direction only. Know which before planning a
walkthrough, because the other direction silently does nothing.

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

This removes the device's netcfg, removes its ports from ONOS's store while
the device is unavailable (`device-ports-remove`, through `onos-cli.sh`; ONOS
offers no REST call for it), and re-adds the netcfg. ONOS then re-runs port
discovery, because it does so only for a device with no ports. Removing and
re-adding the netcfg alone, as earlier versions of the script did, rediscovers
nothing and leaves all 75 ports disabled. The device and its flow rules stay.
It refreshes **the port list and its annotations**. It does **not** surface
twin-created lightpaths — nothing can.

Port discovery is also the one operation that can tear down ONOS-created
lightpaths, via `removeInitalConnectivityServices()`, if ONOS's connection
cache for the device is empty at that moment. While ONOS holds flows for the
device the cache is not empty, and with none there is nothing ONOS-created to
lose, but the script checks afterwards and tells you if lightpaths went.

This asymmetry is a property of ODTN's `ols` driver, not of the twin. The
integration treats **ONOS as the orchestrator**: provisioning originates there,
and the twin is the physics authority that answers. Creating lightpaths in the
TwinLight UI is an out-of-band change, which in a real network would also
require a controller resync.

## Prerequisites

- Docker with Compose v2 (Docker Desktop ≥ 4.30 is fine)
- Python 3 and `curl` on the host (used by the scripts)
- ~6 GB free RAM and ~4 GB disk

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
(image pulls, the amd64 twin build, ONOS boot). Once the images are built, a
cold boot (`demo-down.sh --purge` first) took 2¼–2½ minutes on Apple Silicon
in testing, about a minute of it waiting for ONOS's apps to report ready.

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

## Walkthrough

About ten minutes end to end. Keep four windows open: a terminal, the ONOS GUI,
the TwinLight UI, and Grafana.

### Part 1 — ONOS discovers the twin

*What it shows: a standard SDN controller sees a digital twin as a real optical
domain, with no twin-specific code in the controller.*

```bash
./integrations/onos/scripts/onos-cli.sh devices
./integrations/onos/scripts/onos-cli.sh ports rest:172.28.0.10:8282
```

`devices` shows one device of type `OLS`. `ports` shows **75 OCh ports**, one per
CORONET CONUS transceiver, each annotated with the twin's real T-API SIP UUID
and carrying a DWDM lambda set derived from the twin's per-SIP spectrum
capability.

In the ONOS GUI this is an ordinary ONOS device — the controller does not know
that the domain beneath it is simulated.

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

### Part 2 — ONOS provisions a lightpath, the twin supplies the physics

*What it shows: the orchestrator decides, the twin evaluates. Provisioning is a
real RMSA + QoT admission, not a bookkeeping entry.*

```bash
./integrations/onos/scripts/lightpath.sh create Abilene Atlanta
```

What happens:

1. The script pushes an **ONOS flow rule** on the OLS device (in-port 1 →
   out-port 4). Nothing here touches the twin's API.
2. ONOS's `TapiFlowRuleProgrammable` turns that into a T-API
   **connectivity-service POST** on the connectivity-context, which the
   adapter translates and forwards to the twin's connectivity-context.
3. The twin routes it (k-shortest paths over 2149 km, 9 hops), first-fits
   spectrum, runs GNPy split-step propagation, and checks GSNR against the
   DP-QPSK threshold plus the configured margin.
4. It admits the request and returns the allocated spectrum, as a
   `tapi-photonic-media` augment on the service's end-points.

The script prints the allocation and then the live OPM:

```
  ADMITTED by the twin
    service uuid  : 25a60596-ed43-4e07-b2ba-d6f1a5e3f6d8
    modulation    : DP-QPSK
    centre freq   : 191.3063 THz
    slot width    : 56.25 GHz
==> Twin optical performance monitoring
    25a60596  2148.7 km  GSNR  12.71 dB  OSNR  13.76 dB  Q  12.71 dB  BER 7.83e-06
```

Now switch to the **TwinLight UI** — the lightpath is on the map and in the
spectrum heat map — and to **Grafana**, where the OPM series has started moving.
Poll it twice: the numbers change, because the four transient models are
re-evaluated at read time rather than cached.

**The same sequence step by step.** Open the twin's *Monitoring* page before
you start — it is the only page that auto-refreshes, on the interval set in
Settings. The *Services* page does not poll, so reload it after provisioning.

```bash
# 1. establish that the twin has no such lightpath
curl -s localhost:8080/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["tapi-connectivity:connectivity-service"]), "services")'

# 2. provision it FROM ONOS — a flow rule, nothing T-API in sight
./integrations/onos/scripts/lightpath.sh create Boston New_York

# 3. the same lightpath, now on the twin, with GNPy physics attached
./integrations/onos/scripts/correlate.sh
```

The twin's Services page now shows a new entry named
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
  48976647025378110    ADDED        2->6    Albany -> Baltimore        c8d870de      830.6    16.53    1.0e-11
  48976648847065518    ADDED        1->4    Abilene -> Atlanta         25a60596     2148.7    12.37    1.6e-05
```

A flow with no twin service in that table is one the twin refused. Flows ONOS
is still removing (`PENDING_REMOVE`) are left out: their lightpaths are
already gone from the twin.

> **Why the live GSNR differs from the admission figure.** Admission gates on
> the *pristine GNPy baseline*, while `/internal/opm` reports GSNR **after**
> the transient layer, so the live value moves by a few tenths of a dB around
> the number the admission decision used — PDL drift and EEPN.
> `rmsa.qot_margin_db`, 1.5 dB by default, is the allowance for exactly that,
> the same role a system margin plays in network design.
>
> Gating on a live sample instead would make admission depend on the phase of
> the PDL drift at the instant the request arrived, so two identical requests
> seconds apart could decide differently. See
> [docs/PHYSICS.md § 3](../../docs/PHYSICS.md#3-from-gsnr-to-ber-and-q).

### Part 3 — the twin refuses an infeasible request

*What it shows: a digital twin in the control loop can reject a request the
controller would otherwise have provisioned into a failure.*

ONOS's T-API 2.1 connectivity request has nowhere to carry a modulation format,
so it is adapter policy. Release the Part 2 lightpaths, raise the format to
DP-16QAM and ask for the same path:

```bash
./integrations/onos/scripts/lightpath.sh clear
curl -s -X POST localhost:8282/adapter/modulation \
     -H 'Content-Type: application/json' \
     -d '{"modulation-format":"DP-16QAM"}'

./integrations/onos/scripts/lightpath.sh create Abilene Atlanta
```

```
  REFUSED by the twin (rmsa-qot-refused)
    candidate 1 (trx Abilene > roadm Abilene > roadm Dallas > … > trx Atlanta): DP-16QAM GSNR 12.6 dB below 16.0 dB; candidate 2 (…): DP-16QAM GSNR 12.7 dB below 16.0 dB; … (required GSNR includes the 1.5 dB qot_margin_db)
```

The `clear` matters: ONOS identifies a flow rule by its in-port, so a second
flow from Abilene's port would replace the first in ONOS while the twin kept
both lightpaths. `lightpath.sh create` refuses to do that and names the flow to
delete.

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

A *marginal* pair shows the same thing more precisely — this one misses by
0.2 dB on its best route, so the refusal is clearly a QoT computation rather
than a distance cutoff:

```bash
./integrations/onos/scripts/lightpath.sh create Boston Buffalo
#   REFUSED by the twin (rmsa-qot-refused)
#     candidate 1 (trx Boston > roadm Boston > roadm Albany > roadm Syracuse > roadm Rochester > roadm Buffalo > trx Buffalo): DP-16QAM GSNR 15.8 dB below 16.0 dB; …
```

Reset with:

```bash
curl -s -X POST localhost:8282/adapter/modulation \
     -H 'Content-Type: application/json' -d '{"modulation-format":"DP-QPSK"}'
```

> **Refused flows may install later.** A rule left in `PENDING_ADD` is retried
> by ONOS, so once modulation is back at DP-QPSK the previously refused flow may
> install itself a few seconds later and appear as a new lightpath. That is
> ONOS's normal flow-reconciliation behaviour.
> `./integrations/onos/scripts/lightpath.sh clear` between parts avoids it.

### Part 4 (optional) — fiber cut

```bash
./integrations/onos/scripts/lightpath.sh create Albany Baltimore
./integrations/onos/scripts/fault.sh cut-path <service-uuid>      # uuid from the output above
```

The twin's `/config/` plane marks the fiber failed, invalidates every affected
baseline, and OPM for the lightpath collapses to `status=link-failed`. Grafana
shows the drop immediately.

Restore with `./integrations/onos/scripts/fault.sh heal-all`.

> **Limitation.** ONOS is *not* notified of the fault through a standard
> interface: T-API v2.6 defines a `tapi-fault` module and TwinLight does not
> implement it (see [docs/TAPI_COMPLIANCE.md](../../docs/TAPI_COMPLIANCE.md)).
> ONOS keeps the device up and the flow installed; what changes is the physics
> the twin reports. Closing the loop from twin-detected impairment back to
> controller re-optimisation is future work.

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/demo-up.sh` | Build and boot the stack, verify the ODTN apps, push the netcfg, wait for discovery |
| `scripts/validate.sh` | 16 end-to-end checks across all layers; non-zero exit on any failure |
| `scripts/seed-demo.sh` | Provision 8 lightpaths through ONOS spanning the QoT range *and all three modulation formats*, so every UI has state to show (`--reset` clears first). Pairs are discovered by probing, not hardcoded — see `scripts/find_pairs.py` |
| `scripts/find_pairs.py` | Probe random endpoint pairs through `/internal/path-info` and report which modulation format each can carry. Used by `seed-demo.sh`; useful on its own when a topology changes |
| `scripts/lightpath.sh` | `create <A> <Z>` / `list` / `delete <flow-id>` / `clear` — provisioning driven from ONOS |
| `scripts/correlate.sh` | Join the ONOS Flows view to the twin's service list, one row per flow (`--json` for the raw join) |
| `scripts/resync.sh` | Force ONOS to re-discover ports (`--check` just compares the two sides) |
| `scripts/fault.sh` | `list` / `cut <uid>` / `cut-path <svc-uuid>` / `heal <uid>` / `heal-all` |
| `scripts/onos-cli.sh` | ONOS Karaf CLI, interactive or one-shot, through Karaf's own client in the ONOS container (no host `ssh` needed) |
| `scripts/demo-down.sh` | Stop the stack (`--purge` also drops volumes) |
| `scripts/test-integration.sh` | `unit` / `e2e` / `all` / `preflight` — test the integration on this machine; see [Testing the integration locally](#testing-the-integration-locally) |

Overridable environment variables: `ONOS_URL`, `ONOS_AUTH`, `ADAPTER_URL`,
`TWIN_URL`, `DEVICE_ID`, and `VALIDATE_A` / `VALIDATE_Z` for the validation A–Z
pair. The first four default to what the stack variables in
[Running a second stack side by side](#running-a-second-stack-side-by-side)
publish, so set those rather than these. Against a twin with API tokens, the scripts present `TWINLIGHT_API_TOKEN`
on the requests they send to the twin directly (see
[Authentication](#authentication)).

## Testing the integration locally

Nothing in CI boots ONOS; `scripts/test-integration.sh` is how a change to the
integration is tested, on your machine:

```bash
./integrations/onos/scripts/test-integration.sh unit         # adapter unit tests, seconds, no Docker
./integrations/onos/scripts/test-integration.sh e2e          # boot, validate, every script, tear down
./integrations/onos/scripts/test-integration.sh all          # both
./integrations/onos/scripts/test-integration.sh preflight    # will the test stack fit? changes nothing
```

**`unit`** runs `adapter/tests` in the repo's Python environment (`.venv`, or
`$PYTHON`): the one [CONTRIBUTING.md](../../CONTRIBUTING.md) sets up, which
has FastAPI, httpx, uvicorn and pytest, so it needs no Docker and takes a few
seconds. That FastAPI is the twin's, not the adapter image's pin, and the run
prints both versions; `unit --pinned` runs the same tests in
`python:3.12-slim` with exactly `adapter/requirements.txt` instead. If the
environment lacks a package, the step says what to install rather than failing
on an import.

**With authentication on.** Export the twin's tokens and the one the adapter
and the scripts present before running; Compose passes both into the stack
from the environment, and the run adds a step proving the twin refuses an
anonymous request:

```bash
export TWINLIGHT_API_TOKENS="onos-test:$(openssl rand -hex 24)"
export TWINLIGHT_API_TOKEN="${TWINLIGHT_API_TOKENS#onos-test:}"
./integrations/onos/scripts/test-integration.sh all
unset TWINLIGHT_API_TOKENS TWINLIGHT_API_TOKEN
```

The header of the run says `authentication on` or `off`.

**`e2e`** boots a stack of its own from cold (`down -v` of its own project,
a fresh snapshot directory, `demo-up.sh`), runs `validate.sh`, then drives
every other script and checks what it did:

| Step | Checked |
|------|---------|
| `lightpath.sh create Boston New_York`, `list` | admitted, OPM read back, the flow listed |
| `correlate.sh`, `--json` | every ONOS flow joins a twin service |
| `fault.sh cut-path`, `heal-all`, `list` | OPM reports `link-failed`, then recovers |
| `lightpath.sh delete` | the twin releases the service |
| `seed-demo.sh --reset`, `correlate.sh` | ONOS flows and twin services agree in number |
| `resync.sh --check`, `resync.sh` | ONOS-created lightpaths survive, every port enabled |
| `onos-cli.sh devices` | the CLI answers and lists the device |
| `lightpath.sh clear`, `validate.sh` again | the twin is emptied; still 16/16 |
| `demo-down.sh --purge` | nothing of the project is left |

Options: `--keep` leaves the stack up for inspection, `--reuse` tests a stack
that is already running instead of booting one (and never tears it down),
`--boots N` cold-boots N times in a row before the checks, `--rmi` also
removes the images it built, `--out DIR` chooses where the logs go.

It never touches a stack it did not start. The test stack has its own project,
container prefix and image tag (`twinlight-onos-test`), ports (28080 REST,
28282 adapter, 28181 ONOS, 25173 UI, 23000 Grafana, 29090, 25051, 28101) and
subnet (`172.30.0.0/16`); each can be moved with the variables of
[Running a second stack side by side](#running-a-second-stack-side-by-side),
from the environment only. Everything it creates carries the label
`org.twinlight.onos-test`, and before booting it refuses to go on if the
project, a container name, an image tag, a port or the subnet is held by
anything without that label. `preflight` runs only that check.

Each step's output goes to `<run dir>/steps/`, and the containers' logs to
`<run dir>/logs/` before teardown, so a failure can be read after the stack is
gone; the run directory is printed first and last. The exit status is non-zero
if any step failed, and the summary names them.

How long it takes:

| Host | `unit` | `e2e`, images already built | each extra `--boots` |
|------|--------|-----------------------------|----------------------|
| Apple Silicon, ONOS under emulation, 8 GB Docker VM (measured) | 3 s | about 4½ min: a cold boot ≈ 2¼ min, the checks ≈ 2 min | ≈ 2¼ min |
| amd64 Linux (estimated, not measured) | 3 s | about 3 min: ONOS and the twin run natively | ≈ 1 min |

The first run on a machine also pulls ONOS (≈ 900 MB) and builds the three
images, the twin's for `linux/amd64`, which under emulation adds several
minutes.

## Adapter endpoints

RESTCONF surface, polled by ONOS — avoid calling these by hand while ONOS is
connected, as ONOS's port cache depends on them being consistent:

- `GET /restconf/data/tapi-common:context`
- `GET /restconf/data/tapi-common:context/service-interface-point={uuid}`
- `GET|POST /restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/`
- `DELETE …/connectivity-context/connectivity-service={uuid}`

Introspection, safe to call any time:

- `GET /health` — liveness, twin reachability, and whether the twin accepts the adapter's token
- `GET /adapter/status` — modulation policy, ONOS-created services, recent refusals, the twin's verdict on the adapter's token. Always `200`: a twin that is down sets `"twin-reachable": false` with the reason in `twin-error`, as a refused token sets `twin-authorized` and `twin-auth-error`
- `GET /adapter/ports` — the SIP ↔ ONOS-port map
- `POST /adapter/modulation` — switch modulation format at runtime

## Configuration

`integrations/onos/docker-compose.onos.yml` environment, on the `tapi-adapter` service:

| Variable | Default | Notes |
|----------|---------|-------|
| `TWIN_BASE_URL` | `http://twin:8080` | |
| `TWIN_DATA_ROOT` | *(discovered)* | The twin's T-API data resource, e.g. `/restconf/data`. Unset, the adapter reads the twin's RESTCONF root from `/.well-known/host-meta` on its first call (a `404` there means `/data`); set it to pin the root and skip discovery |
| `ADAPTER_MODULATION` | `DP-QPSK` | Requested format; runtime-switchable |
| `ADAPTER_GRID_GRANULARITY` | `G_50GHZ` | **Only `G_50GHZ` and `G_25GHZ` are safe** — ONOS's `getChannelSpacing()` has trailing spaces in its other case labels (`"G_100GHZ "`, `"G_12_5GHZ "`), so those fall through to `CHL_0GHZ` and then divide by zero in `getOchSignal()` |
| `ADAPTER_EXPOSE_TWIN_SERVICES` | `false` | See below |
| `ADAPTER_SIP_REFRESH_SECONDS` | `30` | SIP catalogue cache lifetime |
| `TWINLIGHT_API_TOKEN` | *(empty)* | The bearer token the adapter presents to a twin with `TWINLIGHT_API_TOKENS` set. Compose passes it from `.env`. Empty, the adapter sends no credentials. See [Authentication](#authentication) |

### Running a second stack side by side

Everything a stack claims on the Docker host has a variable whose default is
the value above: the Compose project, the container names, the image tags, the
published ports, the twin's snapshot directory and, with this overlay, the
subnet. Leave them all unset and nothing changes. Set them, and a second stack
runs next to the first, with no override file and no edited script: both
compose files and every script in `scripts/` read the same names, from the
environment first and then from the repo-root `.env`.

| Variable | Default | Moves |
|----------|---------|-------|
| `COMPOSE_PROJECT_NAME` | the checkout directory's name | project, network and volume names (Compose's own variable) |
| `TWINLIGHT_CONTAINER_PREFIX` | `twinlight` | `twinlight`, `twinlight-ui`, `-prometheus`, `-grafana`, `-grafana-init`, `-tapi-adapter`, `-onos` |
| `TWINLIGHT_IMAGE_TAG` | `latest` | `twinlight`, `twinlight-ui` and `twinlight-tapi-adapter` images |
| `TWINLIGHT_SNAPSHOT_DIR` | `./snapshots` | the twin's snapshots and shutdown checkpoint |
| `TWINLIGHT_REST_PORT` / `_GNMI_PORT` / `_UI_PORT` | `8080` / `50051` / `5173` | |
| `TWINLIGHT_PROMETHEUS_PORT` / `_GRAFANA_PORT` | `9090` / `3000` | |
| `TWINLIGHT_ADAPTER_PORT` | `8282` | |
| `TWINLIGHT_ONOS_REST_PORT` / `_CLI_PORT` | `8181` / `8101` | |
| `TWINLIGHT_ONOS_SUBNET_PREFIX` | `172.28` | the overlay's `/16`, the adapter's and ONOS's addresses, and `DEVICE_ID` |
| `TWINLIGHT_COMPOSE_FILES` | *(none)* | extra `-f` files for the scripts' Compose calls, colon-separated |

For example, beside a default stack that is already running:

```bash
export COMPOSE_PROJECT_NAME=tl2 TWINLIGHT_CONTAINER_PREFIX=tl2 TWINLIGHT_IMAGE_TAG=tl2 \
       TWINLIGHT_REST_PORT=38080 TWINLIGHT_GNMI_PORT=35051 TWINLIGHT_UI_PORT=35173 \
       TWINLIGHT_PROMETHEUS_PORT=39090 TWINLIGHT_GRAFANA_PORT=33000 \
       TWINLIGHT_ADAPTER_PORT=38282 TWINLIGHT_ONOS_REST_PORT=38181 TWINLIGHT_ONOS_CLI_PORT=38101 \
       TWINLIGHT_ONOS_SUBNET_PREFIX=172.29 TWINLIGHT_SNAPSHOT_DIR="$PWD/snapshots-tl2"
./integrations/onos/scripts/demo-up.sh      # and every other script, in the same shell
./integrations/onos/scripts/demo-down.sh --purge
```

Use the environment, not `.env`, for a second stack from the **same**
checkout: `.env` is shared by everything run from that directory. In a separate
checkout or worktree, `.env` works just as well. Without ONOS the same
variables apply to a plain `docker compose up`.

**Why a subnet prefix, and only that.** The overlay pins its network's subnet
because ONOS's netcfg parses the device's `rest.ip` as an `IpAddress`, so the
adapter needs a literal, predictable address. Docker refuses to create two
networks whose subnets overlap, so a second ONOS stack needs another `/16`;
nothing else about the addressing has to change. One variable carries the
first two octets: the subnet is `<prefix>.0.0/16`, the adapter `<prefix>.0.10`
and ONOS `<prefix>.0.20`, and the scripts derive
`DEVICE_ID=rest:<prefix>.0.10:8282`. `netcfg/twinlight-ols.json` stays written
for the default; when `DEVICE_ID` differs, the scripts rewrite its device key
and `rest` address as they push it. Pick a prefix no existing network uses:

```bash
docker network inspect $(docker network ls -q) \
  --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
```

If you pick a taken one, `up` fails with `Pool overlaps with other one on this
address space`.

**What does not follow.** The second stack's **web UI** loads, but its API
calls are refused by the browser: the twin's `server.cors_origins` (in the
scenario YAML, not an environment variable) allows `http://localhost:5173`
and not the moved port. Nothing in the ONOS integration uses the UI, and the
twin's REST API, Grafana and Prometheus are unaffected.

### Why twin-native lightpaths are hidden from ONOS

`TapiDeviceHelper.removeInitalConnectivityServices()` deletes **every**
connectivity service it can see whenever ONOS's flow cache for the device is
empty — which includes the moment it first connects, and again after any ONOS
restart. Left exposed, that silently tears down anything the TwinLight UI or
`examples/demo_services.py` created. The adapter therefore shows ONOS only the
services ONOS itself created. Set `ADAPTER_EXPOSE_TWIN_SERVICES=true` if you
specifically want to demonstrate that behaviour.

## Authentication

The integration works against a twin with or without API tokens. ONOS is
configured the same way in both cases (`netcfg/twinlight-ols.json` carries no
credentials), and the adapter accepts its requests without any.

When the twin has `TWINLIGHT_API_TOKENS` set, the adapter presents
`TWINLIGHT_API_TOKEN`, the same token the TwinLight CLIs and the scripts
present. Set it once, in the `.env` next to the root `docker-compose.yml`. The
twin reads its accepted token from there, the overlay passes the token to the
adapter, and the CLIs and the scripts read it too:

```dotenv
TWINLIGHT_API_TOKENS=<token>
TWINLIGHT_API_TOKEN=<token>
```

The adapter reads the token once at start-up and sends it as a bearer token on
every request it makes to the twin, root discovery included. Run outside
Compose, it reads the variable from its own environment. With the variable
unset or empty, it sends no `Authorization` header, as before. The scripts
present the same token on the requests they send to the twin directly, taking
it from the environment or else from the repository's `.env`. They send it to
the twin only (never to ONOS or the adapter) and never print it. Neither the
adapter nor the scripts check the token beyond its being non-empty, because the
twin decides.

Because every client presents the same token, revoking it (removing it from
`TWINLIGHT_API_TOKENS` and restarting the twin) cuts off the adapter, the CLIs,
the scripts and any UI session that entered it, all at once. The twin's log
and `GET /internal/whoami` name the same principal for all of them, so they
cannot tell the adapter's requests from a CLI's. An operator who wants a separate token for the adapter can set
`TWINLIGHT_API_TOKEN` on the `tapi-adapter` service in their own Compose
override file, as an option outside the default setup.

### What the adapter exposes

With a token, the adapter is a way into the twin that needs no credentials.
Anyone who can reach its port can do what the adapter forwards, with the
token's rights: read the T-API context and its SIPs, list connectivity
services, create a connectivity service, and delete any connectivity service
whose UUID they know (including one created in the UI). The adapter forwards
nothing else (no `/config/`, `/transparent/`, `/admin/` or `/internal/`), but
it does accept `POST /adapter/modulation` from anyone.

The overlay publishes the adapter's port on all of the host's interfaces
(`8282:8282`), which is the easiest to use from the host and from other
machines, so every machine that can reach the host on 8282 has that access.
ONOS reaches the adapter over the Compose network at `172.28.0.10:8282` and
does not use the published port. An operator who wants to narrow access can
bind the mapping to the loopback interface
(`"127.0.0.1:8282:8282"`), which keeps the scripts working because they call
`localhost:8282`, or drop the mapping and the scripts that use `ADAPTER_URL`
with it, or close the port in the host firewall. The token also sits in the
adapter container's environment, where `docker inspect` shows it, as it does
`TWINLIGHT_API_TOKENS` for the twin. It is never written into the image.

### When the twin refuses the adapter

A twin with tokens answers `401` when the adapter has no token or one it does
not accept. The adapter logs that once, at `ERROR`, when it starts (and once at
`INFO` when it ends), naming the variable and never the token:

```
[ERROR] tapi-adapter: the twin requires an API token (HTTP 401) and the adapter has none: set TWINLIGHT_API_TOKEN for the adapter
[ERROR] tapi-adapter: the twin rejected the adapter's token (HTTP 401): TWINLIGHT_API_TOKEN is not one of the twin's TWINLIGHT_API_TOKENS
```

`/health` still answers `200`, so the Compose healthcheck passes and the
scripts get to read the reason. Its body says `"status": "unauthorized"`,
`"twin-authorized": false` and gives the sentence above in `twin-auth-error`.
A twin that is down still reads `"status": "degraded"` with
`"twin-reachable": false`, because the twin's `/health` is public and says
nothing about the token. `/adapter/status` carries the same `twin-authorized`
and `twin-auth-error` fields next to the adapter's own state, and answers
`200` in both cases, with `twin-reachable` and `twin-error` for a twin that is
down. Either trouble is logged when it starts and when it ends, not on every
ONOS poll. `demo-up.sh`,
`validate.sh` and the scripts that need ONOS stop with that sentence instead of
waiting for ports that never appear. A script whose own token the twin refuses
says so in the same words, naming the twin.

ONOS gets `502 Bad Gateway` from every RESTCONF call in that state, with the
sentence in `detail`. It handles that answer as it handles a twin that is down
(the device turns unavailable, and discovery and flow rules are retried). The
adapter does not relay the twin's `401`, because ONOS presented no credentials
and needs none, and a `401` would ask ONOS to authenticate to the adapter.
Flow rules pushed in that state fail, and a deleted flow rule stays
`PENDING_REMOVE` until the twin accepts the adapter again.

---

## Troubleshooting

**Device appears but has zero ports.** The usual cause is check 5 or 6 in
`validate.sh` failing — a SIP without an `mc-pool`, or a UUID whose last segment
is not a plain decimal. Confirm with:

```bash
curl -s localhost:8282/restconf/data/tapi-common:context | python3 -m json.tool | head -40
docker logs twinlight-onos 2>&1 | grep -i "tapi\|ols\|NumberFormat"   # <prefix>-onos if you moved TWINLIGHT_CONTAINER_PREFIX
```

**Device never becomes `available`.** ONOS cannot reach the adapter. The netcfg
IP must match the adapter's static address, `172.28.0.10` by default. The
overlay and the scripts both derive it from `TWINLIGHT_ONOS_SUBNET_PREFIX`, so
they agree unless `DEVICE_ID` is set by hand to something else. A compose
service name will not work there: ONOS parses that field as an `IpAddress`.

**`ols` driver missing.** `org.onosproject.odtn-service` did not activate.
`./integrations/onos/scripts/onos-cli.sh apps -a -s` to check, then
`app activate org.onosproject.odtn-service`.

**Flow rule stays `PENDING_ADD`.** Expected when the twin refused the request —
see Part 3; it is not a fault. Confirm the reason at
`localhost:8282/adapter/status` under `recent-rejections`.

**ONOS OOMs or boots very slowly.** Give Docker Desktop ≥ 6 GB. Under Rosetta,
2–4 minutes to a usable CLI is normal.

**`ssh` to port 8101 refuses to negotiate.** ONOS 2.7 ships an older Karaf SSHD.
`onos-cli.sh` does not use `ssh` (it runs Karaf's own client inside the
container); if you connect by hand, a modern OpenSSH client needs
`-o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o KexAlgorithms=+diffie-hellman-group14-sha1`.

**`demo-up.sh` sits at "waiting for app health 'ready'" for a minute or two
on a cold boot.** Expected. ONOS reports its apps `ACTIVE` well before they
run: activating `odtn-service`'s dependencies triggers a Karaf bundle refresh
that restarts ONOS's core components, typically 30–60 s after the REST API
first answers, and a device configuration pushed before that is lost (the
device never appears, or the push is answered `207 "subjectClassKey 'devices'
not found"`). The script therefore waits for each app's
`/onos/v1/applications/<app>/health` to report `ready`, then pushes, retrying
a transient answer with a bounded back-off.
[COMPATIBILITY.md § C](COMPATIBILITY.md#c-integration-pitfalls) has the
detail. Before this, the first `demo-up.sh` on a cold boot usually failed and a
second run recovered.

---

## Limitations

What this integration does not do:

- **The adapter is a real component, not a formality.** ONOS is talking T-API
  2.1 to a shim, which talks T-API 2.6 to the twin. Without it, ONOS discovers
  the device and zero ports.
- **No fault notification path.** No `tapi-fault`, no T-API notifications, no
  gNMI Get/Set. ONOS learns nothing about impairments; only the twin's own
  observability surfaces them.
- **ONOS sees one OLS device, not the 75-node graph.** That is how ODTN models
  a line system — the twin's topology stays inside the domain. The T-API
  topology context is served at `/restconf/data/tapi-common:context/tapi-topology:topology-context`
  but ONOS's `ols` driver does not consume it.
- **ONOS and the adapter do not authenticate their clients.** ONOS talks to
  the adapter without credentials, and the adapter accepts every request.
  Against a twin with tokens, the adapter presents `TWINLIGHT_API_TOKEN` on
  ONOS's behalf, so anyone who can reach port 8282 can read the twin's SIPs and
  create and delete connectivity services (see
  [Authentication](#authentication)). No link uses TLS. Run the demo on a
  local or lab network only.
- **Modulation format is adapter policy**, because T-API 2.1 connectivity
  requests cannot express one. A 2.6-native controller would not need that.
