# T-API Network Digital Twin — Web UI Implementation Plan

## Overview

A standalone **React + TypeScript** single-page application (SPA) that connects to the T-API Digital Twin REST API and provides an interactive visualization of the optical network topology, device details, link properties, and real-time monitoring data.

**Key design principles:**

- **Fully stateless** — the UI holds no authoritative state. All truth lives in the Digital Twin API.
- **localStorage caching** — topology data is cached locally and refreshed on demand (refresh button). Time-series monitoring data is accumulated in localStorage for historical charts.
- **Standalone deployment** — builds to static HTML/JS/CSS. Configurable DT URL. No backend.
- **REST polling** — periodically polls the DT REST API for monitoring data. No gNMI dependency.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (SPA)                            │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │  Connection  │  │  Topology    │  │  Monitoring           │  │
│  │  Settings    │  │  Cache       │  │  Time-Series Store    │  │
│  │              │  │              │  │                       │  │
│  │  DT URL      │  │  localStorage│  │  localStorage         │  │
│  │  Poll rate   │  │  (JSON blob) │  │  (ring buffer per     │  │
│  │  Credentials │  │  + ETag/ts   │  │   metric per service) │  │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘  │
│         │                 │                      │              │
│  ┌──────▼─────────────────▼──────────────────────▼───────────┐  │
│  │                    API Client Layer                       │  │
│  │           (fetch wrapper + error handling)                │  │
│  └──────────────────────────┬────────────────────────────────┘  │
│                             │                                   │
└─────────────────────────────┼───────────────────────────────────┘
                              │ HTTP (CORS)
                              ▼
                  ┌───────────────────────┐
                  │   T-API Digital Twin  │
                  │   (FastAPI :8080)     │
                  └───────────────────────┘
```

---

## Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Framework | React 18 + TypeScript 5 | Largest ecosystem, strong typing, wide contributor familiarity |
| Build tool | Vite 6 | Fast HMR, zero-config for React/TS, produces optimized static output |
| Routing | React Router v7 | Client-side routing for multi-page layout |
| State management | Zustand v5 | Lightweight, no boilerplate, good for small-medium apps |
| Topology graph | Cytoscape.js + react-cytoscapejs | Purpose-built network graph lib with interactive pan/zoom, dragging, multiple layouts |
| Charts / monitoring | Recharts | React-native charting for time-series. Lightweight, composable |
| UI components | Tailwind CSS **v3** (not v4) | Utility-first CSS, no runtime dependency |
| HTTP client | Built-in fetch (wrapped) | No dependency needed for simple REST polling |
| Testing | Vitest + React Testing Library | Fast, Vite-native, standard React test patterns |
| Linting | ESLint + Prettier | Standard tooling |

---

## Project Structure

```
tapi-twin-ui/
├── public/
│   └── favicon.svg
├── src/
│   ├── main.tsx                      # Entry point
│   ├── App.tsx                       # Root layout + router
│   │
│   ├── api/                          # DT API client layer
│   │   ├── client.ts                 # Base fetch wrapper (URL, headers, errors)
│   │   ├── tapi-common.ts            # GET context, SIPs
│   │   ├── tapi-topology.ts          # GET topology context, topologies, nodes, links, NEPs
│   │   ├── internal.ts               # GET /internal/opm, /internal/opm/{uuid}
│   │   └── types.ts                  # TypeScript interfaces mirroring TAPI JSON
│   │
│   ├── store/                        # Zustand stores
│   │   ├── connection.ts             # DT URL, poll interval, connection status
│   │   ├── topology.ts               # Cached topology data + refresh logic
│   │   ├── monitoring.ts             # Time-series ring buffer per metric
│   │   └── selection.ts              # Currently selected node/link/service
│   │
│   ├── hooks/                        # Custom React hooks
│   │   ├── usePolling.ts             # Generic setInterval-based poller with cleanup
│   │   ├── useTopologyGraph.ts       # Transform TAPI topology → Cytoscape elements
│   │   └── useMonitoringHistory.ts   # Read/write time-series from localStorage
│   │
│   ├── pages/                        # Route-level page components
│   │   ├── DashboardPage.tsx         # Overview: connection status, topology summary, alerts
│   │   ├── TopologyPage.tsx          # Full-screen Cytoscape graph + detail panel
│   │   ├── MonitoringPage.tsx        # Time-series charts for selected services
│   │   ├── DeviceDetailPage.tsx      # Single node: NEPs, SIPs, properties, OPM
│   │   ├── LinkDetailPage.tsx        # Single link: endpoints, span elements, OPM
│   │   └── SettingsPage.tsx          # Configure DT URL, poll rate, clear cache
│   │
│   ├── components/                   # Reusable UI components
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx           # Navigation sidebar
│   │   │   ├── Header.tsx            # Top bar with connection indicator + refresh
│   │   │   └── PageShell.tsx         # Shared page wrapper
│   │   │
│   │   ├── topology/
│   │   │   ├── TopologyGraph.tsx     # Cytoscape wrapper component
│   │   │   ├── NodeTooltip.tsx       # Hover tooltip for nodes
│   │   │   ├── LinkTooltip.tsx       # Hover tooltip for links
│   │   │   ├── GraphControls.tsx     # Layout switch, zoom, fit, refresh button
│   │   │   └── DetailPanel.tsx       # Slide-out panel on node/link click
│   │   │
│   │   ├── monitoring/
│   │   │   ├── MetricChart.tsx       # Single time-series chart (Recharts)
│   │   │   ├── MetricCard.tsx        # Current value + sparkline
│   │   │   ├── OpmDashboard.tsx      # Grid of MetricCards for a service
│   │   │   └── HistoryControls.tsx   # Time range selector, export CSV
│   │   │
│   │   ├── device/
│   │   │   ├── NepTable.tsx          # Table of Node Edge Points
│   │   │   ├── SipBadge.tsx          # Service Interface Point indicator
│   │   │   └── StateIndicator.tsx    # Admin/Operational/Lifecycle state badges
│   │   │
│   │   └── common/
│   │       ├── ConnectionBanner.tsx  # Red banner when DT is unreachable
│   │       ├── JsonViewer.tsx        # Collapsible raw JSON tree view
│   │       ├── RefreshButton.tsx     # Manual refresh with loading spinner
│   │       └── EmptyState.tsx        # Placeholder when no data loaded
│   │
│   ├── lib/                          # Utility functions
│   │   ├── cache.ts                  # localStorage read/write with versioning
│   │   ├── tapi-helpers.ts           # Extract name from NameAndValue[], etc.
│   │   ├── cytoscape-layout.ts       # Cytoscape layout configs (cose, dagre, grid)
│   │   └── time-series.ts            # Ring buffer impl for monitoring history
│   │
│   └── styles/
│       └── globals.css               # Tailwind directives + custom properties
│
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
├── eslint.config.js
└── README.md
```

---

## Data Flow

### Topology Loading & Caching

```
User opens app or clicks Refresh
        │
        ▼
  GET /data/tapi-common:context
        │
        ▼
  Parse response → extract:
    • topology-context.topology[] → nodes[], links[]
    • service-interface-point[]
        │
        ▼
  Transform to Cytoscape elements:
    • TAPI Node → cy node {id, label, type, ...data}
    • TAPI Link → cy edge {source, target, ...data}
      (resolve node-edge-point refs → parent node UUIDs)
        │
        ├──► Zustand topology store (in-memory)
        └──► localStorage "tapi-twin-ui:topology" (JSON + timestamp)
```

**On app startup:**
1. Read `localStorage["tapi-twin-ui:topology"]` — if exists and < 5 min old, hydrate the store immediately (instant render).
2. In parallel, fetch from the DT API. On success, update store + cache.
3. If DT is unreachable and cache exists, show cached data with a "stale" indicator.
4. If DT is unreachable and no cache, show empty state with connection settings prompt.

### Monitoring Polling

```
Every N seconds (configurable, default 10s):
        │
        ▼
  GET /internal/opm  (all services)
    — or —
  GET /internal/opm/{uuid}  (per-service, if connectivity services exist)
        │
        ▼
  For each measurement:
    Append {timestamp, value} to ring buffer
        │
        ├──► Zustand monitoring store (in-memory, drives charts)
        └──► localStorage "tapi-twin-ui:series:{metric}:{uuid}"
             (capped at configurable max points, default 1000)
```

**Ring buffer strategy:**
- Each metric per service gets its own localStorage key.
- Cap at N data points (default 1000). When full, oldest entries are evicted.
- On page load, hydrate from localStorage to show historical data immediately.
- Export to CSV available from the monitoring page.

---

## TypeScript API Types

These mirror the TAPI JSON wire format (hyphenated keys):

```typescript
// api/types.ts

interface NameAndValue {
  "value-name": string;
  value: string;
}

interface ServiceInterfacePoint {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string;
  direction: string;
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

interface NodeEdgePointRef {
  "topology-uuid": string;
  "node-uuid": string;
  "node-edge-point-uuid": string;
}

interface NodeEdgePoint {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string;
  direction: string;
  "mapped-service-interface-point": { "service-interface-point-uuid": string }[];
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

interface TapiNode {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string[];
  "owned-node-edge-point": NodeEdgePoint[];
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

interface TapiLink {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string[];
  "node-edge-point": NodeEdgePointRef[];
  direction: string;
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

interface Topology {
  uuid: string;
  name: NameAndValue[];
  node: TapiNode[];
  link: TapiLink[];
}

interface OpmMeasurements {
  "osnr-db": number;
  "gsnr-db": number;
  "pre-fec-ber": number;
  "q-factor-db": number;
  "chromatic-dispersion-ps-per-nm": number;
  "pmd-ps": number;
}

interface ServiceOpm {
  "service-uuid": string;
  timestamp: number;
  measurements: OpmMeasurements;
}
```

---

## Page Descriptions

### 1. Settings Page (`/settings`)

**Purpose:** Configure the connection to the Digital Twin.

- Text input for DT base URL (e.g., `http://localhost:8080`), stored in `localStorage`.
- "Test Connection" button — calls `GET /health`, shows green/red result.
- Poll interval slider (5s–60s, default 10s).
- "Clear Cache" button — purges all `tapi-twin-ui:*` keys from localStorage.
- "Clear Monitoring History" — purges time-series data only.
- Display localStorage usage (bytes used / quota).

### 2. Dashboard Page (`/` — default)

**Purpose:** At-a-glance network overview.

- **Connection status card** — green/yellow/red indicator, DT URL, last successful poll timestamp.
- **Topology summary cards** — number of topologies, nodes, links, SIPs. Clickable to navigate.
- **Node type breakdown** — donut chart or table: Transceivers vs. ROADMs (derived from node names or NEP/SIP presence).
- **State overview** — count of nodes/links by operational state (ENABLED/DISABLED).
- **Recent monitoring** — sparklines for key OPM metrics (if services are provisioned).
- **Quick actions** — "View Topology", "Open Monitoring", "Refresh All".

### 3. Topology Page (`/topology`)

**Purpose:** Interactive network graph visualization.

- **Full-screen Cytoscape.js graph** with the following mappings:
  - TAPI Nodes → Cytoscape nodes, styled by type:
    - Transceivers (nodes with SIPs): rectangle, blue.
    - ROADMs (nodes with >1 NEP, no SIP): diamond, green.
  - TAPI Links → Cytoscape edges between the nodes referenced by link NEP refs.
    - Edge label: link name (e.g., "roadm Brest -> roadm Morlaix").
    - Edge color by operational state: green (ENABLED), red (DISABLED).
  - Node label: extracted from `name[value-name="node-name"].value`.

- **Graph controls toolbar:**
  - Layout selector: force-directed (cose), hierarchical (dagre), grid, breadthfirst.
  - Zoom in/out, fit-to-screen.
  - Refresh button (re-fetches from DT API, updates cache).
  - Toggle labels on/off.
  - Toggle link direction arrows.

- **Interaction:**
  - Click node → slide-out detail panel showing: UUID, name, operational/admin/lifecycle state, list of NEPs with SIP mappings, link to full device detail page.
  - Click link → slide-out detail panel showing: UUID, link name, span elements (from `name[value-name="span-elements"]`), endpoints (source/dest node names), link to full link detail page.
  - Hover → tooltip with node/link name and state.
  - Double-click node → navigate to DeviceDetailPage.

- **State-aware styling:**
  - Nodes with `operational-state: DISABLED` → grayed out with dashed border.
  - Links with `operational-state: DISABLED` → dashed red line.

### 4. Device Detail Page (`/topology/:topoId/node/:nodeId`)

**Purpose:** Detailed view of a single network element.

- **Header:** Node name, UUID (copyable), type indicator (Transceiver/ROADM).
- **State section:** Admin, Operational, Lifecycle state badges (color-coded).
- **NEP table:** All owned-node-edge-points with columns:
  - UUID, name, layer-protocol-name, direction, admin state, operational state.
  - If a NEP has mapped SIPs, show SIP UUID with a link badge.
- **Connected links:** List of links that reference this node's NEPs, with peer node name.
- **OPM section** (if service is associated):
  - Current OPM metrics as cards (OSNR, GSNR, BER, Q-factor, CD, PMD).
  - Sparkline for each metric from the time-series history.
- **Raw JSON viewer:** Collapsible tree showing the raw TAPI JSON for this node.

### 5. Link Detail Page (`/topology/:topoId/link/:linkId`)

**Purpose:** Detailed view of a single link/span.

- **Header:** Link name, UUID, direction (unidirectional/bidirectional).
- **State section:** Admin, Operational, Lifecycle state badges.
- **Endpoints:**
  - Source node name + NEP UUID.
  - Destination node name + NEP UUID.
  - Clickable links to respective DeviceDetailPages.
- **Span elements:** Parsed from the link's `name[value-name="span-elements"]` — shows the intermediate Fiber/EDFA elements that compose this span.
- **Raw JSON viewer.**

### 6. Monitoring Page (`/monitoring`)

**Purpose:** Time-series monitoring dashboard for all active services.

- **Service selector** (if connectivity services exist in a future DT version).
- **Metric charts grid** (Recharts line charts):
  - OSNR (dB) over time.
  - GSNR (dB) over time.
  - Pre-FEC BER (log scale) over time.
  - Q-factor (dB) over time.
  - Chromatic Dispersion (ps/nm) over time.
  - PMD (ps) over time.
- **Time range selector:** Last 5 min / 15 min / 1 hour / All data.
- **Controls:** Pause/resume polling, export CSV, clear history.
- **Current values cards** at the top showing the latest measurement for each metric.

---

## localStorage Schema

All keys are prefixed with `tapi-twin-ui:` to avoid collisions.

| Key | Value | Purpose |
|-----|-------|---------|
| `tapi-twin-ui:settings` | `{ url, pollInterval, ... }` | Connection settings |
| `tapi-twin-ui:topology` | `{ data, fetchedAt, etag? }` | Cached topology JSON |
| `tapi-twin-ui:series:{metric}:{serviceUuid}` | `[{t, v}, ...]` | Ring buffer for one metric of one service |
| `tapi-twin-ui:series:index` | `[{metric, serviceUuid, count}]` | Index of all active series (for enumeration) |

**Storage budget:** localStorage is typically 5-10 MB. A topology JSON is ~10-50 KB. Each time-series point is ~30 bytes. At 1000 points × 6 metrics × 10 services = ~180 KB. Well within limits.

---

## Cytoscape.js Integration Details

### TAPI → Cytoscape Element Mapping

```typescript
// hooks/useTopologyGraph.ts

function tapiToCytoscape(topology: Topology, sips: ServiceInterfacePoint[]): cytoscape.ElementDefinition[] {
  const elements: cytoscape.ElementDefinition[] = [];

  // Build SIP lookup: sipUuid → true
  const sipUuids = new Set(sips.map(s => s.uuid));

  // Build NEP → Node UUID lookup (for resolving link endpoints)
  const nepToNode: Map<string, string> = new Map();

  for (const node of topology.node) {
    const nodeName = extractName(node.name, "node-name");
    const hasSip = node["owned-node-edge-point"].some(
      nep => nep["mapped-service-interface-point"]?.length > 0
    );
    const nodeType = hasSip ? "transceiver" : "roadm";

    elements.push({
      data: {
        id: node.uuid,
        label: nodeName,
        type: nodeType,
        adminState: node["administrative-state"],
        operState: node["operational-state"],
        nepCount: node["owned-node-edge-point"].length,
      },
    });

    for (const nep of node["owned-node-edge-point"]) {
      nepToNode.set(nep.uuid, node.uuid);
    }
  }

  for (const link of topology.link) {
    const refs = link["node-edge-point"];
    if (refs.length < 2) continue;

    const sourceNode = refs[0]["node-uuid"];
    const targetNode = refs[1]["node-uuid"];
    const linkName = extractName(link.name, "link-name");

    elements.push({
      data: {
        id: link.uuid,
        source: sourceNode,
        target: targetNode,
        label: linkName,
        operState: link["operational-state"],
      },
    });
  }

  return elements;
}
```

### Cytoscape Style Sheet

```typescript
const cytoscapeStylesheet: cytoscape.Stylesheet[] = [
  {
    selector: "node[type='transceiver']",
    style: {
      shape: "round-rectangle",
      "background-color": "#3b82f6",
      label: "data(label)",
      "text-valign": "bottom",
      "text-margin-y": 8,
      width: 40,
      height: 30,
    },
  },
  {
    selector: "node[type='roadm']",
    style: {
      shape: "diamond",
      "background-color": "#22c55e",
      label: "data(label)",
      "text-valign": "bottom",
      "text-margin-y": 8,
      width: 40,
      height: 40,
    },
  },
  {
    selector: "node[operState='DISABLED']",
    style: {
      "background-color": "#9ca3af",
      "border-style": "dashed",
      "border-width": 2,
      "border-color": "#ef4444",
    },
  },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "line-color": "#6b7280",
      "target-arrow-color": "#6b7280",
      width: 2,
    },
  },
  {
    selector: "edge[operState='ENABLED']",
    style: { "line-color": "#22c55e", "target-arrow-color": "#22c55e" },
  },
  {
    selector: "edge[operState='DISABLED']",
    style: {
      "line-color": "#ef4444",
      "target-arrow-color": "#ef4444",
      "line-style": "dashed",
    },
  },
  {
    selector: ":selected",
    style: {
      "border-width": 3,
      "border-color": "#f59e0b",
      "line-color": "#f59e0b",
    },
  },
];
```

---

## API Client Layer

```typescript
// api/client.ts

class TapiApiClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async get<T>(path: string): Promise<T> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      headers: { Accept: "application/yang-data+json, application/json" },
    });
    if (!resp.ok) {
      throw new ApiError(resp.status, await resp.text());
    }
    return resp.json();
  }

  health(): Promise<{ status: string }> {
    return this.get("/health");
  }

  getContext(): Promise<TapiContextResponse> {
    return this.get("/data/tapi-common:context");
  }

  getTopologyContext(): Promise<TopologyContextResponse> {
    return this.get(
      "/data/tapi-common:context/tapi-topology:topology-context"
    );
  }

  getTopology(uuid: string): Promise<TopologyResponse> {
    return this.get(
      `/data/tapi-common:context/tapi-topology:topology-context/topology=${uuid}`
    );
  }

  getNode(topoUuid: string, nodeUuid: string): Promise<NodeResponse> {
    return this.get(
      `/data/tapi-common:context/tapi-topology:topology-context/topology=${topoUuid}/node=${nodeUuid}`
    );
  }

  getLink(topoUuid: string, linkUuid: string): Promise<LinkResponse> {
    return this.get(
      `/data/tapi-common:context/tapi-topology:topology-context/topology=${topoUuid}/link=${linkUuid}`
    );
  }

  getSips(): Promise<SipResponse> {
    return this.get("/data/tapi-common:context/service-interface-point");
  }

  getAllOpm(): Promise<AllOpmResponse> {
    return this.get("/internal/opm");
  }

  getServiceOpm(serviceUuid: string): Promise<ServiceOpm> {
    return this.get(`/internal/opm/${serviceUuid}`);
  }
}
```

---

## Implementation Phases

### Phase 1: Project Setup + Settings + Connection (~2 days)

**Goal:** Scaffold the project, connect to the DT, verify communication.

1. Initialize project: `npm create vite@latest tapi-twin-ui -- --template react-ts`
2. Install dependencies: `tailwindcss`, `@shadcn/ui`, `zustand`, `react-router`, `cytoscape`, `react-cytoscapejs`, `recharts`
3. Configure Tailwind, ESLint, Prettier, path aliases.
4. Create the `api/client.ts` fetch wrapper with error handling.
5. Create the `store/connection.ts` Zustand store (URL, poll interval, connection status).
6. Build the Settings page: URL input, "Test Connection" button, poll interval slider.
7. Build the `Header` component with connection status indicator (green dot / red dot).
8. Build the `Sidebar` with navigation links.
9. Set up React Router with all route stubs.

**Deliverable:** App connects to DT, shows green/red connection status, settings persist across reloads.

### Phase 2: Topology Fetch + Cache + Graph (~3 days)

**Goal:** Fetch topology from DT, cache in localStorage, render interactive Cytoscape graph.

1. Create `api/tapi-topology.ts` and `api/tapi-common.ts` endpoint wrappers.
2. Create `api/types.ts` with all TypeScript interfaces.
3. Create `store/topology.ts` — fetch, parse, cache, hydrate from localStorage.
4. Create `lib/cache.ts` — versioned localStorage read/write with timestamp.
5. Create `hooks/useTopologyGraph.ts` — TAPI → Cytoscape element transformation.
6. Build `TopologyGraph.tsx` — Cytoscape wrapper with stylesheet, layout options.
7. Build `GraphControls.tsx` — layout selector, zoom, fit, refresh button.
8. Build `NodeTooltip.tsx` and `LinkTooltip.tsx` for hover information.
9. Build the Topology page composing all the above.
10. Wire refresh button to re-fetch from DT API and update cache.

**Deliverable:** Interactive topology graph showing all nodes (colored by type) and links, with pan/zoom/drag, layout switching, and cached data.

### Phase 3: Detail Pages (~2 days)

**Goal:** Click-through from topology graph to device and link detail views.

1. Create `store/selection.ts` — track selected node/link UUID.
2. Build `DetailPanel.tsx` — slide-out panel on topology page for quick view.
3. Build `DeviceDetailPage.tsx` — full page with NEP table, SIP badges, state indicators, connected links list.
4. Build `LinkDetailPage.tsx` — full page with endpoints, span elements, state.
5. Build reusable components: `NepTable`, `SipBadge`, `StateIndicator`, `JsonViewer`.
6. Wire Cytoscape click events → detail panel and double-click → navigate to detail page.

**Deliverable:** Full drill-down from topology graph → device/link detail pages with all TAPI properties.

### Phase 4: Dashboard (~1 day)

**Goal:** Overview page with summary cards.

1. Build `DashboardPage.tsx` with:
   - Connection status card.
   - Topology summary cards (counts of topologies, nodes, links, SIPs).
   - Node type breakdown (derived from NEP/SIP analysis).
   - State overview (enabled/disabled counts).
2. Wire cards to navigate to relevant pages.

**Deliverable:** Dashboard showing network health at a glance.

### Phase 5: Monitoring + Time-Series (~3 days)

**Goal:** Poll OPM data, store history in localStorage ring buffers, render time-series charts.

1. Create `lib/time-series.ts` — ring buffer implementation with localStorage persistence.
2. Create `hooks/usePolling.ts` — generic interval-based poller with start/stop/pause.
3. Create `hooks/useMonitoringHistory.ts` — read/append time-series from localStorage.
4. Create `store/monitoring.ts` — current OPM values, polling state.
5. Create `api/internal.ts` — OPM endpoint wrappers.
6. Build `MetricCard.tsx` — current value with unit + sparkline.
7. Build `MetricChart.tsx` — Recharts line chart with time axis, configurable range.
8. Build `OpmDashboard.tsx` — grid of metric cards.
9. Build `HistoryControls.tsx` — time range selector, pause/resume, export CSV.
10. Build `MonitoringPage.tsx` composing all the above.
11. Add sparklines to Dashboard and Device Detail pages.

**Deliverable:** Live-updating monitoring dashboard with historical time-series charts.

### Phase 6: Polish + Testing (~2 days)

**Goal:** Error handling, edge cases, responsive design, tests.

1. Build `ConnectionBanner.tsx` — persistent red banner when DT is unreachable.
2. Build `EmptyState.tsx` — friendly message when no topology loaded.
3. Handle CORS errors gracefully (detect and suggest DT CORS config).
4. Add loading skeletons for all data-fetching states.
5. Responsive layout: sidebar collapses on mobile, graph fills viewport.
6. Write unit tests for: API client, Cytoscape transformation, ring buffer, cache helpers.
7. Write integration tests for: Settings page, Topology page (mocked API).
8. Build and verify static output: `npm run build` → serve with `npx serve dist/`.

**Deliverable:** Production-ready SPA with error handling, loading states, and test coverage.

---

## CORS Configuration (DT Side)

The Digital Twin already has CORS middleware configured in `app.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.server.cors_origins,  # Must include the UI origin
    allow_methods=["*"],
    allow_headers=["*"],
)
```

The UI's Settings page should document that the DT must have the UI's origin (or `*`) in its `cors_origins` config.

---

## Current Implementation Status (Updated 2026-02-28)

### Phases Completed (V1)
- **Phase 1** (Setup + Settings + Connection): ✓ Complete
- **Phase 2** (Topology Fetch + Cache + Graph): ✓ Complete
- **Phase 3** (Detail Pages): ✓ Complete
- **Phase 4** (Dashboard): ✓ Complete
- **Phase 5** (Monitoring + Time-Series): ✓ Complete
- **Phase 6** (Polish + Testing): Partially complete

### Additional Pages Implemented (beyond original plan)
- **Services Page**: Service list + logical topology graph + detail panel + delete
- **Add Service Page**: Full connectivity service creation form
- **Equipment Page**: Equipment list/filter by type
- **Spectrum Page**: Grid context + service allocation table
- **Spectrum Grid Page**: Visual slot allocation heatmap per ROADM-ROADM link
- **Path Computation Page**: A/Z SIP selection, k-shortest, QoT estimate

---

## Future Enhancements — Ranked by Impact

### Tier 1: High Impact

| # | Feature | Description | Depends On |
|---|---------|-------------|------------|
| 1 | **Eye diagram rendering** | Render statistical eye diagrams from backend API. Component placeholder exists. | Backend eye diagram synthesis |
| 2 | **Constellation diagram rendering** | Render constellation scatter plots from backend API. Component placeholder exists. | Backend constellation synthesis |
| 3 | **Geographic map view** | Render nodes on Leaflet/Mapbox using lat/lon from GNPy topology metadata. Major visual impact. | None (lat/lon already in GNPy elements) |
| 4 | **Failure injection UI** | UI to inject fiber cuts, EDFA failures, etc., and observe impact on monitoring. | Backend failure scenario API |
| 5 | **Snapshot/Restore UI** | Manage DT state checkpoints from the UI. List/create/restore snapshots. | Backend admin endpoints (already implemented) |

### Tier 2: Medium Impact

| # | Feature | Description | Depends On |
|---|---------|-------------|------------|
| 6 | **gNMI streaming via WebSocket proxy** | Sub-second telemetry via WebSocket sidecar subscribing to gNMI. | Backend WebSocket endpoint |
| 7 | **Dark mode** | Toggle light/dark themes. Tailwind v3 supports this natively. | None |
| 8 | **Notification/alarm panel** | Subscribe to DT events (fiber cuts, service failures) and display as feed. | Backend event/alarm API |
| 9 | **Spectrum fragmentation visualization** | Show fragmentation metrics per link, highlight contiguous free blocks. | None (data already available) |
| 10 | **Service path overlay on topology** | Highlight the physical path of a selected service on the topology graph. | None (path data already in /internal/services) |

### Tier 3: Nice-to-Have

| # | Feature | Description | Depends On |
|---|---------|-------------|------------|
| 11 | **Multi-DT support** | Connect to multiple DT instances, switch between them. | None |
| 12 | **OSA (Optical Spectrum Analyzer) view** | Per-channel power spectral density plot. | Backend OSA emulation |
| 13 | **Batch service provisioning** | Upload CSV/JSON of services to provision in bulk. | None |
| 14 | **Topology diff view** | Compare two snapshots and highlight topology/service changes. | Backend snapshot API |
| 15 | **Export topology as image** | Export Cytoscape graph as PNG/SVG. | None (cytoscape supports this) |
