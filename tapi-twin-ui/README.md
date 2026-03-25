# T-API Network Digital Twin — Web UI

A React + TypeScript SPA that connects to the T-API Digital Twin REST API and provides interactive visualization of the optical network topology, device details, and real-time monitoring.

## Quick Start

```bash
cd tapi-twin-ui
npm install
npm run dev        # Dev server on http://localhost:5173
npm run build      # Production build → dist/
npm test           # Run test suite
```

## Configuration

1. Start the Digital Twin backend: `tapi-twin --config examples/twin_config.yaml`
2. Open the UI at `http://localhost:5173`
3. Go to **Settings** and enter the DT base URL (default: `http://localhost:8080`)
4. Click **Test Connection** to verify, then **Refresh** on any page to load data

### CORS

Add your UI origin to the DT config (`twin_config.yaml`):
```yaml
server:
  cors_origins:
    - "http://localhost:5173"
```

## Features

- **Dashboard** — Network health at a glance: node/link/SIP counts, operational state, topology breakdown
- **Topology** — Interactive Cytoscape.js graph with force-directed/hierarchical/grid/breadthfirst/circle layouts, pan/zoom, click-through to details
- **Device Detail** — Full node view: NEP table, SIP badges, state indicators, connected links, OPM metrics
- **Link Detail** — Full link view: endpoints with node resolution, span element table, state indicators
- **Monitoring** — Time-series OPM dashboard (OSNR, GSNR, BER, Q-factor, CD, PMD) with configurable time ranges, pause/resume, CSV export
- **Settings** — DT URL, poll interval, cache management

## Architecture

- **React 18 + TypeScript** with Vite 6
- **Zustand** for state management (connection, topology, monitoring, selection)
- **localStorage** caching: topology (5-min TTL) + time-series ring buffers (1000 pts/metric/service)
- **Cytoscape.js** for graph rendering (via react-cytoscapejs)
- **Recharts** for time-series charts
- **Tailwind CSS v3** for styling

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | React 18 + TypeScript 5 |
| Build | Vite 6 |
| Routing | React Router v7 |
| State | Zustand v5 |
| Graph | Cytoscape.js + react-cytoscapejs |
| Charts | Recharts |
| Styling | Tailwind CSS v3 |
| Testing | Vitest + React Testing Library |
