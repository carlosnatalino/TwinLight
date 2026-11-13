# Integrations

Third-party systems driven against the TwinLight digital twin. Each
subdirectory is self-contained: its own compose overlay, scripts and runbook.

| Integration | What it does |
|---|---|
| [onos/](onos/) | ONOS as a hierarchical orchestrator over the twin, via ODTN / T-API. ONOS discovers the twin as an open line system, provisions lightpaths across it, and receives the twin's RMSA/QoT verdict — including refusals. |
| [opendaylight/](opendaylight/) | *Investigation only, no code.* What OpenDaylight TransportPCE offers for an integration, why its T-API (northbound only, 2.4.0) cannot drive the twin the way ONOS does, and the viable options. |

Nothing here is imported by `src/twinlight`, and nothing here may be. An
integration adapts to the twin's published interfaces; when it needs the twin to
behave differently, that is either a genuine standards gap to fix in the twin or
a quirk of the third-party system to absorb in the integration. `onos/` keeps
that distinction explicit in its
[COMPATIBILITY.md](onos/COMPATIBILITY.md).

All commands in these runbooks are written to be run **from the repository
root**, not from inside the integration directory — compose overlays resolve
their build contexts against the root.
