# TwinLight — Web UI

A React + TypeScript SPA that connects to the [TwinLight](../README.md) REST API
and provides interactive visualization of the optical network topology, device
details, spectrum occupancy, and live monitoring.

The UI is **fully stateless**: all truth lives in the twin. It polls the REST
API and caches topology and time series in `localStorage` (key prefix
`twinlight-ui:`) so a reload does not lose a monitoring session.

The easiest way to run it together with a twin is the Compose stack described in
the [main README](../README.md#quick-start-with-docker-compose) — everything
below is for UI development.

## Quick start

```bash
npm ci             # reproducible install from package-lock.json
npm run dev        # dev server on http://localhost:5173
npm run build      # production build → dist/
npm test           # test suite (vitest)
npm run lint       # eslint
```

Run these from `twinlight-ui/`, or from the repository root with
`npm --prefix twinlight-ui <script>`.

## Connecting to a twin

1. Start the backend: `twinlight --config examples/twin_config.yaml`
2. Open <http://localhost:5173>
3. Go to **Settings** and enter the twin's base URL (default
   `http://localhost:8080`)
4. Click **Test Connection**, then **Refresh** on any page to load data

### CORS

The twin must allow the UI's origin. Both shipped example configs already do:

```yaml
server:
  cors_origins:
    - "http://localhost:5173"
```

## Docker

The UI ships as a static nginx image. The Digital Twin URL is **not** baked in
at build time — it is injected at container startup from the `TWIN_URL`
environment variable into `config.js`, so one image works against any twin.

```bash
# Standalone
docker build -t twinlight-ui ./twinlight-ui
docker run --rm -p 5173:80 -e TWIN_URL="http://localhost:8080" twinlight-ui

# Or as part of the full stack (twin + ui + Prometheus + Grafana)
docker compose up --build      # UI on http://localhost:5173
```

`TWIN_URL` is the address the **browser** uses, so it must be reachable from
the host (the twin's published port, e.g. `http://localhost:8080`) — not the
compose service name. A value saved in the UI's Settings page (localStorage)
overrides the injected default. Make sure that origin is listed in the twin's
`cors_origins`; `http://localhost:5173` already is in the example configs.

## Features

- **Dashboard** — network health at a glance: node/link/SIP counts, operational state, topology breakdown
- **Topology** — interactive Cytoscape.js graph with force-directed / hierarchical / grid / breadth-first / circle layouts, pan and zoom, click-through to details
- **Device detail** — full node view: NEP table, SIP badges, state indicators, connected links, OPM metrics
- **Link detail** — endpoints with node resolution, span element table, state indicators
- **Services** — service list, logical topology graph, spectrum allocation, delete with confirmation
- **Add service** — SIP selection, modulation format, direction, admin and lifecycle state
- **Monitoring** — time-series OPM dashboard (OSNR, GSNR, BER, Q-factor, CD, PMD) with configurable time ranges, pause/resume, CSV export and min/max/avg statistics
- **Spectrum grid** — per-link slot allocation heat map with hover detail
- **Path computation** — A/Z SIP selection, k-shortest paths, QoT estimate
- **Equipment** — inventory list grouped and filterable by element type
- **Settings** — twin URL, connection test, poll interval, cache management

Constellation and eye-diagram components exist as placeholders; the backend
synthesises the data (`/internal/services/{uuid}/constellation` and
`/eye-diagram`) but the rendering is not implemented yet.

## Architecture

- **React 18 + TypeScript** on Vite 6
- **Zustand** stores: connection, topology, monitoring, services, selection
- **localStorage** caching: topology (5-minute TTL) plus time-series ring buffers (1000 points per metric per service)
- **Cytoscape.js** for graph rendering, via react-cytoscapejs
- **Recharts** for time-series charts
- **Tailwind CSS v3** for styling

### Conventions worth knowing before editing

- **Tailwind stays on v3.** Do not upgrade to v4.
- **npm only** — not pnpm, not bun. Commit `package-lock.json` changes.
- `vite.config.ts` and `vitest.config.ts` are deliberately separate files; the
  `vite` and `vitest/config` type exports conflict when merged.
- Use `globalThis`, not `global`, in tests — the browser tsconfig has no Node
  types.
- Cytoscape stylesheets are typed
  `Array<cytoscape.StylesheetStyle | cytoscape.StylesheetCSS>`, not
  `cytoscape.Stylesheet[]`.
- Local type declarations for untyped packages live in `src/types/`.

## Tech stack

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
